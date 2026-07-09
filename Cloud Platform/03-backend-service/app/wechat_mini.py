from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings


class _SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


@dataclass
class AccessTokenCache:
    token: str = ""
    expires_at: datetime | None = None

    def is_valid(self) -> bool:
        return bool(self.token) and self.expires_at is not None and datetime.now(timezone.utc) < self.expires_at


class WeChatMiniNotifier:
    def __init__(self, settings: Settings, repository: Any, http_client: Any = httpx) -> None:
        self.settings = settings
        self.repository = repository
        self.http_client = http_client
        self._token_cache = AccessTokenCache()
        self._templates_by_id = {
            str(item["id"]): item
            for item in self.settings.mini_program_subscribe_templates
            if str(item.get("id") or "").strip()
        }

    @property
    def login_ready(self) -> bool:
        return bool(self.settings.wechat_mini_app_id and self.settings.wechat_mini_app_secret)

    @property
    def subscribe_send_ready(self) -> bool:
        return self.login_ready and bool(self._templates_by_id)

    def exchange_code_for_session(self, code: str) -> dict[str, str]:
        if not self.login_ready:
            raise RuntimeError("WECHAT_MINI_APP_ID / WECHAT_MINI_APP_SECRET 未配置。")

        response = self.http_client.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": self.settings.wechat_mini_app_id,
                "secret": self.settings.wechat_mini_app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
            timeout=10.0,
        )
        payload = response.json()
        errcode = int(payload.get("errcode", 0) or 0)
        if errcode != 0:
            errmsg = str(payload.get("errmsg") or "unknown")
            raise RuntimeError(f"WeChat code2Session 失败: {errcode} {errmsg}")

        openid = str(payload.get("openid") or "").strip()
        if not openid:
            raise RuntimeError("WeChat code2Session 未返回 openid。")

        return {
            "openid": openid,
            "unionid": str(payload.get("unionid") or "").strip(),
            "session_key": str(payload.get("session_key") or "").strip(),
        }

    def dispatch_notifications(self, result: dict[str, Any]) -> dict[str, Any]:
        sessions = list(result.get("sessions") or [])
        if not sessions:
            return {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0, "reasons": []}

        reasons: list[str] = []
        if not self.settings.mini_program_subscribe_template_ids:
            reasons.append("subscribe_template_ids_missing")
        if not self.login_ready:
            reasons.append("wechat_credentials_missing")
        if self.settings.mini_program_subscribe_template_ids and not self._templates_by_id:
            reasons.append("subscribe_template_payloads_missing")

        summary = {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0, "reasons": reasons}
        event = dict(result.get("event") or {})

        for session in sessions:
            if str(session.get("platform") or "").lower() != "wechat_miniprogram":
                summary["skipped"] += 1
                continue

            summary["attempted"] += 1
            device = self.repository.get_mini_program_device(str(session["app_user_id"]))
            if device is None:
                summary["failed"] += 1
                self._append_reason(summary, "device_not_registered")
                self.repository.mark_mini_program_notification_result(
                    str(session["app_user_id"]),
                    template_id=None,
                    success=False,
                    error="device_not_registered",
                )
                continue

            granted_template_ids = list(device.get("granted_template_ids") or [])
            template_definition = self._select_template_definition(granted_template_ids)
            if not template_definition:
                error_reason = (
                    "active_template_grant_missing_reauthorize_required"
                    if granted_template_ids
                    else "notification_not_ready"
                )
                summary["failed"] += 1
                self._append_reason(summary, error_reason)
                self.repository.mark_mini_program_notification_result(
                    str(session["app_user_id"]),
                    template_id=None,
                    success=False,
                    error=error_reason,
                )
                continue

            context = self._build_context(event=event, session=session, device=device)
            template_id = str(template_definition["id"])
            try:
                self.send_subscribe_message(
                    openid=str(device["wechat_openid"]),
                    template_definition=template_definition,
                    context=context,
                )
            except RuntimeError as exc:
                summary["failed"] += 1
                self._append_reason(summary, str(exc))
                self.repository.mark_mini_program_notification_result(
                    str(session["app_user_id"]),
                    template_id=template_id,
                    success=False,
                    error=str(exc),
                )
                continue

            summary["sent"] += 1
            self.repository.mark_mini_program_notification_result(
                str(session["app_user_id"]),
                template_id=template_id,
                success=True,
                error="",
            )

        return summary

    def _append_reason(self, summary: dict[str, Any], reason: str) -> None:
        normalized = str(reason or "").strip()
        if not normalized:
            return
        reasons = summary.setdefault("reasons", [])
        if normalized not in reasons:
            reasons.append(normalized)

    def send_subscribe_message(self, openid: str, template_definition: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if not self.subscribe_send_ready:
            raise RuntimeError("微信订阅消息未完成配置。")

        template_id = str(template_definition.get("id") or "").strip()
        if not template_id:
            raise RuntimeError("缺少模板 ID。")

        data_definition = template_definition.get("data")
        if not isinstance(data_definition, dict) or not data_definition:
            raise RuntimeError(f"模板 {template_id} 未配置 data 字段。")

        payload = {
            "touser": openid,
            "template_id": template_id,
            "page": self._render_page(template_definition, context),
            "data": self._render_template_data(data_definition, context),
            "miniprogram_state": self.settings.wechat_mini_program_state,
            "lang": self.settings.wechat_mini_program_lang,
        }

        access_token = self._get_access_token()
        response = self.http_client.post(
            f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={access_token}",
            json=payload,
            timeout=10.0,
        )
        response_payload = response.json()
        errcode = int(response_payload.get("errcode", 0) or 0)
        if errcode != 0:
            errmsg = str(response_payload.get("errmsg") or "unknown")
            raise RuntimeError(f"WeChat subscribe send 失败: {errcode} {errmsg}")

        return {
            "template_id": template_id,
            "page": payload["page"],
            "msgid": response_payload.get("msgid"),
        }

    def _select_template_definition(self, granted_template_ids: list[str]) -> dict[str, Any] | None:
        for template_id in granted_template_ids:
            definition = self._templates_by_id.get(str(template_id))
            if definition:
                return definition
        return None

    def _build_context(self, event: dict[str, Any], session: dict[str, Any], device: dict[str, Any]) -> dict[str, Any]:
        event_severity = str(event.get("severity") or "")
        target_external_key = str(event.get("target_external_key") or "")
        profile_display_name = str(
            session.get("profile_display_name")
            or self._lookup_profile_display_name(target_external_key)
            or event.get("target_label")
            or ""
        )
        display_external_key = (
            profile_display_name
            or str(event.get("target_label") or "")
            or target_external_key
        )
        occurred_at_raw = str(event.get("occurred_at") or "")
        occurred_date, occurred_time = self._split_occurrence_time(occurred_at_raw)
        return {
            "app_user_id": str(session.get("app_user_id") or ""),
            "callback_phone": self.settings.emergency_call_number,
            "event_body": self._clip_text(str(event.get("body") or ""), 20),
            "event_occurred_at": occurred_at_raw,
            "event_occurred_date": occurred_date,
            "event_occurred_datetime": self._format_datetime_value(occurred_at_raw),
            "event_occurred_time": occurred_time,
            "event_severity": event_severity,
            "event_severity_label": self._format_severity_label(event_severity),
            "event_title": self._clip_text(str(event.get("title") or ""), 20),
            "external_key": self._clip_text(display_external_key, 20),
            "external_key_raw": target_external_key,
            "join_path": str(session.get("join_path") or ""),
            "join_url": str(session.get("join_url") or ""),
            "profile_display_name": self._clip_text(profile_display_name, 20),
            "recipient_name": self._clip_text(str(session.get("recipient_name") or device.get("recipient_name") or ""), 20),
            "session_id": str(session.get("session_id") or ""),
            "target_label": self._clip_text(str(event.get("target_label") or profile_display_name), 20),
        }

    def _lookup_profile_display_name(self, external_key: str) -> str:
        external_key = str(external_key or "").strip()
        if not external_key:
            return ""
        for profile in self.repository.list_profiles():
            if str(profile.get("external_key") or "").strip() == external_key:
                return str(profile.get("display_name") or "").strip()
        return ""

    def _render_page(self, template_definition: dict[str, Any], context: dict[str, Any]) -> str:
        page_template = str(template_definition.get("page") or "").strip()
        if not page_template:
            page_template = "pages/detail/detail?sessionId={session_id}"
        return page_template.format_map(_SafeFormatDict(context))

    def _render_template_data(self, data_definition: dict[str, Any], context: dict[str, Any]) -> dict[str, dict[str, str]]:
        rendered: dict[str, dict[str, str]] = {}
        for key, value_definition in data_definition.items():
            if isinstance(value_definition, dict):
                raw_value = value_definition.get("value", "")
            else:
                raw_value = value_definition
            text = str(raw_value).format_map(_SafeFormatDict(context))
            rendered[str(key)] = {"value": text}
        return rendered

    def _get_access_token(self) -> str:
        if self._token_cache.is_valid():
            return self._token_cache.token

        response = self.http_client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": self.settings.wechat_mini_app_id,
                "secret": self.settings.wechat_mini_app_secret,
            },
            timeout=10.0,
        )
        payload = response.json()
        errcode = int(payload.get("errcode", 0) or 0)
        if errcode != 0:
            errmsg = str(payload.get("errmsg") or "unknown")
            raise RuntimeError(f"WeChat access token 获取失败: {errcode} {errmsg}")

        access_token = str(payload.get("access_token") or "").strip()
        expires_in = max(60, int(payload.get("expires_in", 7200) or 7200))
        if not access_token:
            raise RuntimeError("WeChat access token 响应缺少 access_token。")

        self._token_cache = AccessTokenCache(
            token=access_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60),
        )
        return access_token

    def _format_severity_label(self, severity: str) -> str:
        normalized = severity.strip().lower()
        if normalized == "critical":
            return "紧急"
        if normalized == "warning":
            return "预警"
        if normalized == "info":
            return "提醒"
        return severity or "通知"

    def _split_occurrence_time(self, occurred_at: str) -> tuple[str, str]:
        raw = occurred_at.strip()
        if not raw:
            return "", ""
        try:
            normalized = raw.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return parsed.strftime("%Y-%m-%d"), parsed.strftime("%H:%M")
        except ValueError:
            if "T" in raw:
                date_part, time_part = raw.split("T", 1)
                return date_part[:10], time_part[:5]
            if " " in raw:
                date_part, time_part = raw.split(" ", 1)
                return date_part[:10], time_part[:5]
        return raw[:10], raw[:5]

    def _clip_text(self, value: str, limit: int) -> str:
        cleaned = value.strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 1)] + "…"

    def _format_datetime_value(self, occurred_at: str) -> str:
        raw = occurred_at.strip()
        if not raw:
            return ""
        try:
            normalized = raw.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            if "T" in raw:
                return raw.replace("T", " ")[:19]
            return raw[:19]
