from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


APP_NAME = "EmergencyVoiceApp"
BACKGROUND_AGENT_NAME = "EmergencyVoiceBackgroundAgent"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def app_data_dir() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    target = base / APP_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def runtime_log_path() -> Path:
    return app_data_dir() / "startup.log"


def background_agent_config_path() -> Path:
    return app_data_dir() / "background-alert-config.json"


def background_agent_state_path() -> Path:
    return app_data_dir() / "background-alert-state.json"


def background_agent_lock_path() -> Path:
    return app_data_dir() / "background-alert.lock"


def startup_script_path() -> Path:
    startup_dir = Path(os.getenv("APPDATA") or str(Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    return startup_dir / f"{BACKGROUND_AGENT_NAME}.cmd"


def default_background_agent_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "role": "caregiver",
        "backend_base_url": "",
        "app_user_id": "",
        "recipient_name": "",
        "participant_id": "",
        "label": "",
        "platform": "web",
        "auto_startup": False,
        "updated_at": "",
    }


def load_json_file(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(fallback)
    if not isinstance(raw, dict):
        return dict(fallback)
    merged = dict(fallback)
    merged.update(raw)
    return merged


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_background_agent_config() -> dict[str, Any]:
    return load_json_file(background_agent_config_path(), default_background_agent_config())


def save_background_agent_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = dict(default_background_agent_config())
    config.update(payload)
    config["updated_at"] = utc_now_iso()
    config["auto_startup"] = bool(config.get("auto_startup"))
    config["enabled"] = bool(config.get("enabled"))
    write_json_file(background_agent_config_path(), config)
    return config


def load_background_agent_state() -> dict[str, Any]:
    return load_json_file(
        background_agent_state_path(),
        {
            "pid": 0,
            "started_at": "",
            "last_heartbeat_at": "",
            "last_alert_session_id": "",
            "last_error": "",
            "seen_session_ids": [],
            "mode": "",
        },
    )


def save_background_agent_state(payload: dict[str, Any]) -> dict[str, Any]:
    state = load_background_agent_state()
    state.update(payload)
    write_json_file(background_agent_state_path(), state)
    return state


def clear_background_agent_state() -> None:
    state = load_background_agent_state()
    state["last_heartbeat_at"] = ""
    state["last_error"] = ""
    state["mode"] = ""
    write_json_file(background_agent_state_path(), state)


def background_agent_is_running(grace_seconds: int = 18) -> bool:
    state = load_background_agent_state()
    heartbeat = str(state.get("last_heartbeat_at") or "").strip()
    if not heartbeat:
        return False
    try:
        last = datetime.fromisoformat(heartbeat)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last <= timedelta(seconds=grace_seconds)


def desktop_entry_command(*, background_agent: bool = False, open_url: str = "") -> list[str]:
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parent / "desktop_app.py")]

    if background_agent:
        command.append("--background-agent")
    if open_url:
        command.extend(["--open-url", open_url])
    return command


def launch_detached(command: list[str]) -> None:
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parent),
        creationflags=creationflags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch_background_agent() -> None:
    launch_detached(desktop_entry_command(background_agent=True))


def launch_foreground_window(open_url: str) -> None:
    launch_detached(desktop_entry_command(open_url=open_url))


def install_startup_entry() -> Path:
    script_path = startup_script_path()
    command = subprocess.list2cmdline(desktop_entry_command(background_agent=True))
    script_path.write_text(f"@echo off\r\nstart \"\" {command}\r\n", encoding="utf-8")
    return script_path


def remove_startup_entry() -> None:
    path = startup_script_path()
    if path.exists():
        path.unlink()


def startup_entry_exists() -> bool:
    return startup_script_path().exists()
