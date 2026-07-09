from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv()


def _normalize_ice_servers(entries: list[Any] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        urls_raw = entry.get("urls")
        if isinstance(urls_raw, str):
            urls = [urls_raw]
        elif isinstance(urls_raw, list):
            urls = [str(item) for item in urls_raw if str(item).strip()]
        else:
            urls = []
        if not urls:
            continue

        normalized_entry: dict[str, Any] = {"urls": urls}
        if entry.get("username") is not None:
            normalized_entry["username"] = str(entry.get("username"))
        if entry.get("credential") is not None:
            normalized_entry["credential"] = str(entry.get("credential"))
        if entry.get("credentialType") is not None:
            normalized_entry["credentialType"] = str(entry.get("credentialType"))
        normalized.append(normalized_entry)

    return normalized


def _load_json_map(name: str, default: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    raw = os.getenv(name)
    if not raw:
        return default or {}
    data = json.loads(raw)
    return {str(key).lower(): [str(item) for item in value] for key, value in data.items()}


def _load_json_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default or []
    data = json.loads(raw)
    return [str(item) for item in data]


def _normalize_subscribe_templates(entries: list[Any] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        template_id = str(entry.get("id") or entry.get("template_id") or "").strip()
        if not template_id:
            continue
        page = str(entry.get("page") or "").strip()
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        normalized.append(
            {
                "id": template_id,
                "page": page,
                "data": data,
            }
        )

    return normalized


def _load_json_object_list(name: str, default: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    raw = os.getenv(name)
    if not raw:
        return default or []
    data = json.loads(raw)
    if not isinstance(data, list):
        return default or []
    return [item for item in data if isinstance(item, dict)]


def _load_ice_servers(name: str, default: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    raw = os.getenv(name)
    data = json.loads(raw) if raw else (default or [])
    return _normalize_ice_servers(data)


@dataclass(frozen=True)
class Settings:
    ingest_key: str
    provider: str
    public_base_url: str
    database_path: str
    emergency_call_number: str
    wechat_mini_app_id: str
    wechat_mini_app_secret: str
    mini_program_subscribe_template_ids: list[str]
    mini_program_subscribe_templates: list[dict[str, Any]]
    wechat_mini_program_state: str
    wechat_mini_program_lang: str
    default_contacts: list[str]
    contacts_by_severity: dict[str, list[str]]
    twilio_account_sid: str | None
    twilio_auth_token: str | None
    twilio_from_number: str | None
    twilio_turn_enabled: bool
    twilio_turn_ttl: int
    auto_confirm_smn_subscription: bool
    webrtc_ice_servers: list[dict[str, Any]]
    fall_freefall_threshold_g: float
    fall_freefall_min_ms: int
    fall_impact_threshold_g: float
    fall_impact_window_ms: int
    fall_count_dedup_ms: int

    @classmethod
    def from_env(cls) -> "Settings":
        subscribe_templates = _normalize_subscribe_templates(
            _load_json_object_list("WECHAT_MINI_SUBSCRIBE_TEMPLATES", [])
        )
        subscribe_template_ids = list(
            dict.fromkeys(
                [
                    *_load_json_list("MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS", []),
                    *[str(item["id"]) for item in subscribe_templates],
                ]
            )
        )
        return cls(
            ingest_key=os.getenv("INGEST_KEY", "change-me"),
            provider=os.getenv("CALL_PROVIDER", "mock").lower(),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
            database_path=os.getenv(
                "DATABASE_PATH",
                str(Path(__file__).resolve().parents[1] / "data" / "emergency_call.db"),
            ),
            emergency_call_number=os.getenv("EMERGENCY_CALL_NUMBER", "").strip(),
            wechat_mini_app_id=os.getenv("WECHAT_MINI_APP_ID", "").strip(),
            wechat_mini_app_secret=os.getenv("WECHAT_MINI_APP_SECRET", "").strip(),
            mini_program_subscribe_template_ids=subscribe_template_ids,
            mini_program_subscribe_templates=subscribe_templates,
            wechat_mini_program_state=os.getenv("WECHAT_MINI_PROGRAM_STATE", "formal").strip() or "formal",
            wechat_mini_program_lang=os.getenv("WECHAT_MINI_PROGRAM_LANG", "zh_CN").strip() or "zh_CN",
            default_contacts=_load_json_list(
                "DEFAULT_CONTACTS",
                ["+8613800000000"],
            ),
            contacts_by_severity=_load_json_map(
                "CONTACTS_BY_SEVERITY",
                {
                    "critical": ["+8613800000001", "+8613800000002"],
                    "warning": ["+8613800000003"],
                },
            ),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
            twilio_from_number=os.getenv("TWILIO_FROM_NUMBER"),
            twilio_turn_enabled=os.getenv("TWILIO_TURN_ENABLED", "false").lower() == "true",
            twilio_turn_ttl=max(60, int(os.getenv("TWILIO_TURN_TTL", "3600"))),
            auto_confirm_smn_subscription=os.getenv("AUTO_CONFIRM_SMN_SUBSCRIPTION", "true").lower() == "true",
            webrtc_ice_servers=_load_ice_servers(
                "WEBRTC_ICE_SERVERS",
                [
                    {
                        "urls": [
                            "stun:stun.l.google.com:19302",
                            "stun:stun1.l.google.com:19302",
                        ]
                    }
                ],
            ),
            fall_freefall_threshold_g=float(os.getenv("FALL_FREEFALL_THRESHOLD_G", "0.45")),
            fall_freefall_min_ms=max(1, int(os.getenv("FALL_FREEFALL_MIN_MS", "60"))),
            fall_impact_threshold_g=float(os.getenv("FALL_IMPACT_THRESHOLD_G", "2.5")),
            fall_impact_window_ms=max(1, int(os.getenv("FALL_IMPACT_WINDOW_MS", "1000"))),
            fall_count_dedup_ms=max(1000, int(os.getenv("FALL_COUNT_DEDUP_MS", "30000"))),
        )
