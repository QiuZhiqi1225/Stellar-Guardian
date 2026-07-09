from __future__ import annotations

import argparse
import importlib
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import webbrowser

import httpx
import uvicorn

from desktop_alert_agent import run_background_alert_agent
from desktop_runtime import app_data_dir, runtime_log_path


WINDOW_TITLE = "Emergency Voice App"
DEFAULT_BIND_HOST = "0.0.0.0"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def write_runtime_log(message: str) -> None:
    runtime_log_path().write_text(message, encoding="utf-8")


def safe_console_print(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        return
    try:
        stream.write(f"{message}\n")
        stream.flush()
    except Exception:
        return


def choose_port(preferred: int) -> int:
    for port in range(preferred, preferred + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("Unable to find a free local port.")


def detect_lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
            if address and not address.startswith("127."):
                return address
        except OSError:
            pass
    return "127.0.0.1"


def load_runtime_env_file() -> None:
    candidates = [Path.cwd() / ".env"]
    if is_frozen():
        candidates.insert(0, Path(sys.executable).resolve().parent / ".env")

    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        for raw_line in resolved.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def configure_runtime(port: int, public_host: str | None = None, public_base_url: str | None = None) -> str:
    data_dir = app_data_dir()
    explicit_public_base_url = (public_base_url or os.environ.get("PUBLIC_BASE_URL") or "").strip()
    resolved_public_host = public_host or detect_lan_ip()
    if explicit_public_base_url.lower().startswith(("http://", "https://")):
        os.environ["PUBLIC_BASE_URL"] = explicit_public_base_url.rstrip("/")
    else:
        os.environ["PUBLIC_BASE_URL"] = f"http://{resolved_public_host}:{port}"
    os.environ["DATABASE_PATH"] = str(data_dir / "data" / "emergency_call.db")
    os.environ.setdefault("INGEST_KEY", "local-emergency-demo")
    os.environ.setdefault("CALL_PROVIDER", "mock")
    return resolved_public_host


def configure_python_logging() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )


def load_app_module():
    import app.main as app_main

    if "app.main" in sys.modules:
        return importlib.reload(sys.modules["app.main"])
    return app_main


def start_server(
    port: int,
    bind_host: str = DEFAULT_BIND_HOST,
    public_host: str | None = None,
    public_base_url: str | None = None,
):
    configure_runtime(port, public_host=public_host, public_base_url=public_base_url)
    configure_python_logging()
    app_module = load_app_module()

    config = uvicorn.Config(
        app_module.app,
        host=bind_host,
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn-server", daemon=True)
    thread.start()
    return server, thread


def wait_for_server(port: int, timeout: float = 20.0) -> str:
    base_url = f"http://127.0.0.1:{port}"
    started = time.time()
    last_error = ""
    with httpx.Client(timeout=1.5) as client:
        while time.time() - started < timeout:
            try:
                response = client.get(f"{base_url}/health")
                if response.status_code == 200:
                    return base_url
            except Exception as exc:  # pragma: no cover
                last_error = str(exc)
            time.sleep(0.3)
    raise RuntimeError(f"Local server failed to start. {last_error}")


def smoke_check_routes(base_url: str) -> None:
    with httpx.Client(timeout=3.0) as client:
        checks = [
            ("/health", 200, None),
            ("/", 200, "华为云告警语音控制台"),
            ("/caregiver-demo", 200, "家属端语音通话 Demo"),
            ("/device-demo", 200, "设备端语音通话 Demo"),
            ("/api/dashboard", 200, None),
        ]
        for path, expected_status, expected_text in checks:
            response = client.get(f"{base_url}{path}")
            if response.status_code != expected_status:
                raise RuntimeError(f"Smoke check failed for {path}: {response.status_code}")
            if expected_text and expected_text not in response.text:
                raise RuntimeError(f"Smoke check content mismatch for {path}")


def open_window(target_url: str) -> None:
    import webview

    webview.create_window(
        WINDOW_TITLE,
        target_url,
        min_size=(1180, 760),
        text_select=True,
    )
    webview.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emergency voice call desktop app")
    parser.add_argument("--port", type=int, default=8010, help="Preferred local port")
    parser.add_argument("--bind-host", default=DEFAULT_BIND_HOST, help="Server bind host, default 0.0.0.0")
    parser.add_argument("--public-host", default="", help="Host or IP shown to other devices, default auto-detect LAN IP")
    parser.add_argument("--public-base-url", default="", help="Full public base URL such as https://demo.trycloudflare.com")
    parser.add_argument("--browser", action="store_true", help="Open in the system browser instead of desktop window")
    parser.add_argument("--open-url", default="", help="Open a specific URL inside the desktop window")
    parser.add_argument("--background-agent", action="store_true", help="Run the local background alert agent")
    parser.add_argument("--smoke-test", action="store_true", help="Start server, verify health endpoint, then exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_runtime_env_file()
    if args.background_agent:
        return run_background_alert_agent()

    if args.open_url:
        try:
            open_window(args.open_url)
            return 0
        except Exception as exc:
            write_runtime_log(f"OPEN_URL_FAILED {exc}\n\n{traceback.format_exc()}")
            safe_console_print(f"OPEN_URL_FAILED {exc}", error=True)
            return 1

    port = choose_port(args.port)
    server, _thread = start_server(
        port,
        bind_host=args.bind_host.strip() or DEFAULT_BIND_HOST,
        public_host=args.public_host.strip() or None,
        public_base_url=args.public_base_url.strip() or None,
    )
    try:
        base_url = wait_for_server(port)
        if args.smoke_test:
            smoke_check_routes(base_url)
            write_runtime_log(f"SMOKE_TEST_OK {base_url}")
            safe_console_print(f"SMOKE_TEST_OK {base_url}")
            server.should_exit = True
            return 0

        if args.browser:
            webbrowser.open(f"{base_url}/")
            safe_console_print(f"APP_RUNNING {base_url}")
            while not server.should_exit:
                time.sleep(0.5)
            return 0

        open_window(f"{base_url}/")
        server.should_exit = True
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        server.should_exit = True
        return 0
    except Exception as exc:
        server.should_exit = True
        write_runtime_log(f"APP_START_FAILED {exc}\n\n{traceback.format_exc()}")
        safe_console_print(f"APP_START_FAILED {exc}", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
