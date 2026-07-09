from __future__ import annotations

import uuid
from typing import Any

from app.config import Settings
from app.models import AlarmEvent, utc_now_iso


class EmergencyCallService:
    def __init__(self, settings: Settings, repository: Any) -> None:
        self.settings = settings
        self.repository = repository

    def resolve_recipients(self, event: AlarmEvent) -> list[dict[str, Any]]:
        if event.target_external_key:
            recipients = self.repository.resolve_app_recipients(event.target_external_key, event.severity.lower())
            if recipients:
                return recipients
        return []

    def build_message(self, event: AlarmEvent) -> str:
        return (
            f"紧急告警。级别: {event.severity}. "
            f"标题: {event.title}. "
            f"时间: {event.occurred_at}. "
            f"内容: {event.body}"
        )[:280]

    def dispatch(self, event: AlarmEvent) -> dict[str, Any]:
        recipients = self.resolve_recipients(event)
        message = self.build_message(event)
        sessions: list[dict[str, Any]] = []
        now = utc_now_iso()

        for recipient in recipients:
            session_id = f"session-{uuid.uuid4().hex}"
            join_path = f"/app-call/{session_id}"
            sessions.append(
                {
                    "session_id": session_id,
                    "recipient_id": recipient["id"],
                    "recipient_name": recipient["recipient_name"],
                    "app_user_id": recipient["app_user_id"],
                    "device_token": recipient["device_token"],
                    "platform": recipient["platform"],
                    "status": "pending",
                    "channel": "app_voice_call",
                    "created_at": now,
                    "join_path": join_path,
                    "join_url": f"{self.settings.public_base_url}{join_path}",
                    "detail": f"已为 {recipient['recipient_name']} 创建实时语音会话。",
                }
            )

        return {
            "event": event.to_dict(),
            "provider": "app_call",
            "message": message,
            "recipients": [recipient["app_user_id"] for recipient in recipients],
            "sessions": sessions,
        }
