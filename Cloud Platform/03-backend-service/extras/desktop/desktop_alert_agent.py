from __future__ import annotations

import json
import msvcrt
import os
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from typing import Any
from urllib.parse import urlencode, urljoin
import webbrowser
import winsound

import httpx
import tkinter as tk
from tkinter import font as tkfont

from desktop_runtime import (
    background_agent_is_running,
    background_agent_lock_path,
    clear_background_agent_state,
    launch_foreground_window,
    load_background_agent_config,
    load_background_agent_state,
    save_background_agent_state,
    utc_now_iso,
)


POLL_SECONDS = 3.5
STATE_LIMIT = 120


def remember_seen_session(session_id: str) -> None:
    state = load_background_agent_state()
    seen = list(state.get("seen_session_ids") or [])
    if session_id in seen:
        return
    seen.append(session_id)
    state["seen_session_ids"] = seen[-STATE_LIMIT:]
    save_background_agent_state(state)


def load_seen_sessions() -> set[str]:
    state = load_background_agent_state()
    return {str(item) for item in state.get("seen_session_ids") or []}


def build_room_url(config: dict[str, Any], session: dict[str, Any]) -> str:
    role = str(config.get("role") or "caregiver").strip() or "caregiver"
    label = str(config.get("recipient_name") or config.get("label") or config.get("app_user_id") or role).strip()
    participant_id = str(config.get("participant_id") or "").strip()
    base_url = str(config.get("backend_base_url") or "").rstrip("/")
    session_id = str(session.get("session_id") or "").strip()
    query = urlencode(
        {
            "api_base": base_url,
            "autojoin": "1",
            "role": role,
            "label": label,
            "participant_id": participant_id,
        }
    )
    return f"{base_url}/app-call/{session_id}?{query}"


def open_room_window(config: dict[str, Any], session: dict[str, Any]) -> None:
    room_url = build_room_url(config, session)
    try:
        launch_foreground_window(room_url)
    except Exception:
        webbrowser.open(room_url)


def lock_background_agent() -> Any:
    lock_path = background_agent_lock_path()
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise RuntimeError("Background agent already running.")
    handle.write(str(os.getpid()).encode("utf-8"))
    handle.flush()
    return handle


class EmergencyPopup:
    def __init__(self, root: tk.Tk, session: dict[str, Any], config: dict[str, Any], on_close) -> None:
        self.root = root
        self.session = session
        self.config = config
        self.on_close = on_close
        self.window = tk.Toplevel(root)
        self.window.title("紧急预警")
        self.window.configure(bg="#61170f")
        self.window.attributes("-topmost", True)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.dismiss)

        try:
            self.window.state("zoomed")
        except Exception:
            self.window.geometry("760x520")
            self.window.update_idletasks()
            width = self.window.winfo_width()
            height = self.window.winfo_height()
            x = max((self.window.winfo_screenwidth() - width) // 2, 10)
            y = max((self.window.winfo_screenheight() - height) // 2, 10)
            self.window.geometry(f"{width}x{height}+{x}+{y}")

        container = tk.Frame(self.window, bg="#61170f", padx=28, pady=28)
        container.pack(fill="both", expand=True)

        kicker_font = tkfont.Font(family="Segoe UI", size=12, weight="bold")
        title_font = tkfont.Font(family="Segoe UI", size=28, weight="bold")
        body_font = tkfont.Font(family="Segoe UI", size=14)

        tk.Label(
            container,
            text="EMERGENCY ALERT",
            bg="#61170f",
            fg="#ffd985",
            font=kicker_font,
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            container,
            text="收到新的紧急预警",
            bg="#61170f",
            fg="#fff8f2",
            font=title_font,
            anchor="w",
            pady=14,
        ).pack(fill="x")

        message = str(session.get("detail") or "主机已发出紧急告警，请立即处理。")
        tk.Label(
            container,
            text=message,
            bg="#61170f",
            fg="#fff0e6",
            font=body_font,
            justify="left",
            wraplength=860,
            anchor="w",
        ).pack(fill="x")

        meta_frame = tk.Frame(container, bg="#61170f")
        meta_frame.pack(fill="x", pady=(20, 8))
        for item in [
            f"会话 ID: {session.get('session_id', '-')}",
            f"接收账号: {config.get('app_user_id') or '-'}",
            f"时间: {session.get('created_at') or '-'}",
            f"模式: {config.get('role') or 'caregiver'}",
        ]:
            pill = tk.Label(
                meta_frame,
                text=item,
                bg="#8f2c1b",
                fg="#fff7f1",
                padx=10,
                pady=6,
                font=("Segoe UI", 11, "bold"),
            )
            pill.pack(side="left", padx=(0, 10), pady=(0, 10))

        actions = tk.Frame(container, bg="#61170f")
        actions.pack(fill="x", side="bottom", pady=(28, 0))

        primary_label = "立即接听" if str(config.get("role")) == "caregiver" else "立即进入"
        tk.Button(
            actions,
            text=primary_label,
            command=self.accept,
            bg="#ffbf5d",
            fg="#4b130c",
            activebackground="#ffd582",
            activeforeground="#4b130c",
            relief="flat",
            padx=20,
            pady=12,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=(0, 12))

        if str(config.get("role")) == "caregiver":
            tk.Button(
                actions,
                text="拒绝本次",
                command=self.reject,
                bg="#fff4ed",
                fg="#61170f",
                activebackground="#ffffff",
                activeforeground="#61170f",
                relief="flat",
                padx=20,
                pady=12,
                font=("Segoe UI", 12, "bold"),
            ).pack(side="left", padx=(0, 12))

        tk.Button(
            actions,
            text="稍后处理",
            command=self.dismiss,
            bg="#8f2c1b",
            fg="#fff7f1",
            activebackground="#a33b28",
            activeforeground="#fff7f1",
            relief="flat",
            padx=20,
            pady=12,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        self.window.grab_set()
        self.window.focus_force()
        winsound.MessageBeep(winsound.MB_ICONHAND)

    def _post_status(self, status: str) -> None:
        if str(self.config.get("role")) != "caregiver":
            return
        base_url = str(self.config.get("backend_base_url") or "").rstrip("/")
        session_id = str(self.session.get("session_id") or "")
        if not base_url or not session_id:
            return
        with httpx.Client(timeout=5.0) as client:
            client.post(
                f"{base_url}/api/call-sessions/{session_id}/status",
                json={"status": status},
            )

    def accept(self) -> None:
        try:
            if str(self.config.get("role")) == "caregiver":
                self._post_status("accepted")
        except Exception:
            pass
        open_room_window(self.config, self.session)
        self.close()

    def reject(self) -> None:
        try:
            self._post_status("rejected")
        except Exception:
            pass
        self.close()

    def dismiss(self) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.window.grab_release()
        except Exception:
            pass
        self.window.destroy()
        self.on_close()


class BackgroundAlertAgent:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Emergency Alert Agent")
        self.queue: Queue[tuple[dict[str, Any], dict[str, Any]]] = Queue()
        self.popup: EmergencyPopup | None = None
        self.seen_session_ids = load_seen_sessions()

    def start(self) -> None:
        save_background_agent_state(
            {
                "pid": os.getpid(),
                "started_at": utc_now_iso(),
                "mode": "background-agent",
                "last_error": "",
            }
        )
        threading.Thread(target=self.poll_loop, name="background-alert-poll", daemon=True).start()
        self.root.after(500, self.process_queue)
        self.root.mainloop()

    def process_queue(self) -> None:
        try:
            while True:
                session, config = self.queue.get_nowait()
                if self.popup is None:
                    self.popup = EmergencyPopup(self.root, session, config, self.on_popup_closed)
                else:
                    self.queue.put((session, config))
                    break
        except Empty:
            pass
        self.root.after(500, self.process_queue)

    def on_popup_closed(self) -> None:
        self.popup = None

    def poll_loop(self) -> None:
        while True:
            config = load_background_agent_config()
            save_background_agent_state(
                {
                    "pid": os.getpid(),
                    "last_heartbeat_at": utc_now_iso(),
                    "mode": str(config.get("role") or "caregiver"),
                }
            )
            try:
                for session in self.fetch_new_sessions(config):
                    session_id = str(session.get("session_id") or "")
                    if not session_id:
                        continue
                    self.seen_session_ids.add(session_id)
                    remember_seen_session(session_id)
                    save_background_agent_state({"last_alert_session_id": session_id, "last_error": ""})
                    self.queue.put((session, config))
            except Exception as exc:
                save_background_agent_state({"last_error": str(exc), "last_heartbeat_at": utc_now_iso()})
            time.sleep(POLL_SECONDS)

    def fetch_new_sessions(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        enabled = bool(config.get("enabled"))
        backend_base_url = str(config.get("backend_base_url") or "").strip().rstrip("/")
        role = str(config.get("role") or "caregiver").strip() or "caregiver"
        if not enabled or not backend_base_url:
            return []

        with httpx.Client(timeout=6.0) as client:
            if role == "device":
                response = client.get(f"{backend_base_url}/api/live-sessions")
                response.raise_for_status()
                items = response.json().get("items") or []
            else:
                app_user_id = str(config.get("app_user_id") or "").strip()
                if not app_user_id:
                    return []
                response = client.get(f"{backend_base_url}/api/app-users/{app_user_id}/pending-sessions")
                response.raise_for_status()
                items = response.json().get("items") or []

        fresh_items: list[dict[str, Any]] = []
        for session in sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True):
            session_id = str(session.get("session_id") or "").strip()
            if not session_id or session_id in self.seen_session_ids:
                continue
            fresh_items.append(session)
        return fresh_items


def run_background_alert_agent() -> int:
    lock_handle = None
    try:
        lock_handle = lock_background_agent()
    except RuntimeError:
        return 0 if background_agent_is_running() else 1

    try:
        agent = BackgroundAlertAgent()
        agent.start()
        return 0
    finally:
        clear_background_agent_state()
        if lock_handle:
            try:
                lock_handle.close()
            except Exception:
                pass
