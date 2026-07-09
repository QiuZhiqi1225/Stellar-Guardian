from __future__ import annotations

from collections import deque
import logging
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.call_service import EmergencyCallService
from app.config import Settings
from app.database import DatabaseRepository
from app.huawei import (
    HuaweiFallDetector,
    confirm_subscription,
    extract_iotda_accel_samples,
    is_iotda_property_report,
    parse_huawei_event,
)
from app.models import utc_now_iso
from app.wechat_mini import WeChatMiniNotifier
from app.webrtc import resolve_webrtc_config
from desktop_runtime import (
    background_agent_is_running,
    install_startup_entry,
    launch_background_agent,
    load_background_agent_config,
    load_background_agent_state,
    remove_startup_entry,
    save_background_agent_config,
    startup_entry_exists,
)


settings = Settings.from_env()
static_dir = Path(__file__).resolve().parent / "static"
repository = DatabaseRepository(settings.database_path)
logger = logging.getLogger("emergency_call_backend")


def audit_log(message: str) -> None:
    line = f"[AUDIT] {message}"
    print(line, flush=True)
    logger.info(message)

app = FastAPI(title="Emergency Voice Call Backend", version="0.6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.state.settings = settings
app.state.repository = repository
app.state.processed_ids = repository.load_processed_ids()
app.state.recent_results = deque(repository.list_recent_event_results(50), maxlen=50)
service = EmergencyCallService(settings=settings, repository=repository)
notifier = WeChatMiniNotifier(settings=settings, repository=repository)
app.state.notifier = notifier
app.state.fall_detector = HuaweiFallDetector(settings=settings)
app.state.huawei_webhook_audit = deque(maxlen=20)


class TestAlertRequest(BaseModel):
    external_key: str | None = None
    subject: str = "跌倒告警"
    severity: str = "critical"
    content: str = "检测到老人疑似跌倒，请立即查看。"


class ProfilePayload(BaseModel):
    external_key: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=500)
    owner_user_id: str | None = Field(default=None, max_length=120)


class UserRegisterPayload(BaseModel):
    user_id: str = Field(min_length=3, max_length=120)
    display_name: str = Field(min_length=1, max_length=100)
    secret: str = Field(min_length=4, max_length=120)
    notes: str = Field(default="", max_length=500)


class UserLoginPayload(BaseModel):
    user_id: str = Field(min_length=3, max_length=120)
    secret: str = Field(min_length=4, max_length=120)


class UserContactPayload(BaseModel):
    contact_user_id: str = Field(min_length=3, max_length=120)
    relationship_label: str = Field(default="", max_length=100)


class AppRecipientPayload(BaseModel):
    profile_id: int
    recipient_name: str = Field(min_length=1, max_length=100)
    app_user_id: str = Field(min_length=1, max_length=120)
    device_token: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="android", max_length=20)
    severity_scope: str = Field(default="all", max_length=30)
    priority: int = Field(default=1, ge=1, le=99)


class SessionStatusPayload(BaseModel):
    status: str = Field(pattern="^(ringing|accepted|ended|missed|rejected)$")


class MobileDeviceRegistrationPayload(BaseModel):
    app_user_id: str = Field(min_length=1, max_length=120)
    recipient_name: str = Field(min_length=1, max_length=100)
    device_token: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="android", max_length=20)
    external_key: str | None = Field(default=None, max_length=100)


class WeChatMiniLoginPayload(BaseModel):
    app_user_id: str = Field(min_length=1, max_length=120)
    recipient_name: str = Field(min_length=1, max_length=100)
    device_token: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=255)
    external_key: str | None = Field(default=None, max_length=100)


class MobileSubscribePermissionPayload(BaseModel):
    app_user_id: str = Field(min_length=1, max_length=120)
    recipient_name: str = Field(min_length=1, max_length=100)
    device_token: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="wechat_miniprogram", max_length=20)
    permission_result: dict[str, str] = Field(default_factory=dict)
    external_key: str | None = Field(default=None, max_length=100)


class SessionJoinPayload(BaseModel):
    participant_id: str = Field(min_length=1, max_length=120)
    role: str = Field(pattern="^(caregiver|device|operator|observer)$")
    label: str = Field(min_length=1, max_length=120)


class SessionLeavePayload(BaseModel):
    participant_id: str = Field(min_length=1, max_length=120)


class SessionSignalPayload(BaseModel):
    sender_participant_id: str = Field(min_length=1, max_length=120)
    sender_role: str = Field(pattern="^(caregiver|device|operator|observer)$")
    signal_type: str = Field(pattern="^(offer|answer|ice-candidate|hangup|ready)$")
    payload: dict[str, Any] = Field(default_factory=dict)
    target_participant_id: str | None = Field(default=None, max_length=120)


class SessionCleanupPayload(BaseModel):
    mode: str = Field(pattern="^(clear_all|keep_latest)$")


class DesktopAlertAgentConfigPayload(BaseModel):
    enabled: bool = True
    role: str = Field(pattern="^(caregiver|device)$")
    backend_base_url: str = Field(default="", max_length=255)
    app_user_id: str = Field(default="", max_length=120)
    recipient_name: str = Field(default="", max_length=100)
    participant_id: str = Field(default="", max_length=120)
    label: str = Field(default="", max_length=120)
    platform: str = Field(default="web", max_length=20)
    auto_startup: bool = False


class DesktopAlertAgentStartupPayload(BaseModel):
    enabled: bool


def refresh_runtime_cache() -> None:
    app.state.processed_ids = repository.load_processed_ids()
    app.state.recent_results = deque(repository.list_recent_event_results(50), maxlen=50)


def ensure_local_request(request: Request) -> None:
    client_host = (request.client.host if request.client and request.client.host else "").lower()
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="This endpoint is available only on the local desktop app.")


def process_alarm_event(event: Any) -> dict[str, Any] | None:
    if event.event_id in app.state.processed_ids:
        audit_log(f"Skipped duplicate event event_id={event.event_id} source={getattr(event, 'source', '')}")
        return None
    app.state.processed_ids.add(event.event_id)
    result = service.dispatch(event)
    repository.save_dispatch_result(result)
    current_notifier: WeChatMiniNotifier = app.state.notifier
    result["notification_summary"] = current_notifier.dispatch_notifications(result)
    audit_log(
        "Processed alarm event "
        f"event_id={event.event_id} "
        f"source={getattr(event, 'source', '')} "
        f"external_key={getattr(event, 'target_external_key', '')} "
        f"sessions={len(result.get('sessions') or [])} "
        f"notification_summary={result.get('notification_summary')}"
    )
    app.state.recent_results.appendleft(result)
    return result


def process_notification(payload: dict[str, Any]) -> dict[str, Any] | None:
    accel_samples = extract_iotda_accel_samples(payload)
    if accel_samples:
        detector: HuaweiFallDetector = app.state.fall_detector
        results: list[dict[str, Any]] = []
        for sample in sorted(accel_samples, key=lambda item: item.occurred_at_ms):
            event = detector.observe(sample, payload)
            if event is None:
                continue
            result = process_alarm_event(event)
            if result is not None:
                result["telemetry_summary"] = {
                    "samples": len(accel_samples),
                    "device_key": sample.device_key,
                    "impact_accel_g": sample.accel_g,
                }
                results.append(result)
        if results:
            return results[-1]
        audit_log(f"Accepted telemetry without alarm samples={len(accel_samples)}")
        return {"status": "telemetry_accepted", "samples": len(accel_samples)}

    if is_iotda_property_report(payload):
        audit_log("Accepted property report without accel samples")
        return {"status": "telemetry_accepted", "samples": 0}

    event = parse_huawei_event(payload)
    return process_alarm_event(event)


def _summarize_accel_sample(sample: Any) -> dict[str, Any]:
    return {
        "device_key": sample.device_key,
        "accel_g": sample.accel_g,
        "occurred_at": sample.occurred_at,
        "occurred_at_ms": sample.occurred_at_ms,
        "state": sample.state,
        "fall_count": sample.fall_count,
        "service_id": sample.service_id,
        "target_label": sample.target_label,
        "raw_properties": sample.raw_properties,
    }


def build_huawei_webhook_audit_entry(
    payload: dict[str, Any],
    request_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    accel_samples = extract_iotda_accel_samples(payload)
    property_report_detected = is_iotda_property_report(payload)
    entry: dict[str, Any] = {
        "received_at": utc_now_iso(),
        "request": request_meta or {},
        "message_type": str(payload.get("type") or payload.get("Type") or "").strip() or "custom",
        "message_id": str(payload.get("message_id") or payload.get("MessageId") or "").strip(),
        "property_report_detected": property_report_detected,
        "accel_sample_count": len(accel_samples),
        "accel_samples": [_summarize_accel_sample(sample) for sample in accel_samples],
        "payload": payload,
        "result": None,
        "status": "received",
    }
    if not property_report_detected and not accel_samples:
        event_preview = parse_huawei_event(payload)
        entry["event_preview"] = {
            "event_id": event_preview.event_id,
            "source": event_preview.source,
            "severity": event_preview.severity,
            "title": event_preview.title,
            "occurred_at": event_preview.occurred_at,
            "target_external_key": event_preview.target_external_key,
        }
    return entry


def process_huawei_webhook_payload(payload: dict[str, Any], request_meta: dict[str, Any]) -> None:
    entry = build_huawei_webhook_audit_entry(payload, request_meta)
    try:
        entry["result"] = process_notification(payload)
        entry["status"] = "processed"
        audit_log(
            "Huawei webhook processed "
            f"status={entry['status']} "
            f"message_type={entry.get('message_type', '')} "
            f"accel_sample_count={entry.get('accel_sample_count', 0)} "
            f"result={entry.get('result')}"
        )
    except Exception as exc:  # pragma: no cover
        entry["status"] = "failed"
        entry["error"] = str(exc)
        audit_log(f"Huawei webhook processing failed error={exc}")
        logger.exception("Huawei webhook processing failed error=%s", exc)
        raise
    finally:
        entry["processed_at"] = utc_now_iso()
        app.state.huawei_webhook_audit.appendleft(entry)


def build_dashboard_payload() -> dict[str, Any]:
    current_settings: Settings = app.state.settings
    return {
        "name": app.title,
        "status": "running",
        "dispatch_mode": "app_voice_call",
        "recent_events": len(app.state.recent_results),
        "public_base_url": current_settings.public_base_url,
        "health_url": "/health",
        "events_url": "/events",
        "webhook_url": f"/webhooks/huawei/{current_settings.ingest_key}",
        "users": repository.list_users(),
        "profiles": repository.list_profiles(),
        "items": list(app.state.recent_results),
        "active_sessions": repository.list_active_sessions(),
    }


def build_webrtc_payload() -> dict[str, Any]:
    current_settings: Settings = app.state.settings
    return resolve_webrtc_config(current_settings)


def build_mobile_app_config() -> dict[str, Any]:
    current_settings: Settings = app.state.settings
    current_notifier: WeChatMiniNotifier = app.state.notifier
    return {
        "public_base_url": current_settings.public_base_url,
        "emergency_call_number": current_settings.emergency_call_number,
        "subscribe_template_ids": current_settings.mini_program_subscribe_template_ids,
        "mini_program_state": current_settings.wechat_mini_program_state,
        "wechat_login_ready": current_notifier.login_ready,
        "subscribe_send_ready": current_notifier.subscribe_send_ready,
        "subscribe_template_payload_ready": bool(current_settings.mini_program_subscribe_templates),
        "supports_phone_call": bool(current_settings.emergency_call_number),
        "supports_voice_room": True,
    }


def decorate_session_payload(session: dict[str, Any]) -> dict[str, Any]:
    current_settings: Settings = app.state.settings
    decorated = dict(session)
    decorated["callback_phone"] = current_settings.emergency_call_number
    decorated["can_make_phone_call"] = bool(current_settings.emergency_call_number)
    return decorated


def decorate_session_collection(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [decorate_session_payload(item) for item in items]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/caregiver-demo")
def caregiver_demo() -> FileResponse:
    return FileResponse(static_dir / "caregiver.html")


@app.get("/device-demo")
def device_demo() -> FileResponse:
    return FileResponse(static_dir / "device.html")


@app.get("/backend-results")
def backend_results_page() -> FileResponse:
    return FileResponse(static_dir / "backend-results.html")


@app.get("/app-call/{session_id}")
def app_call_room(session_id: str) -> FileResponse:
    return FileResponse(static_dir / "call-room.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "dispatch_mode": "app_voice_call",
        "recent_events": len(app.state.recent_results),
        "database_path": settings.database_path,
    }


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return build_dashboard_payload()


@app.get("/api/webrtc-config")
def get_webrtc_config() -> dict[str, Any]:
    return build_webrtc_payload()


@app.get("/api/mobile/app-config")
def get_mobile_app_config() -> dict[str, Any]:
    return build_mobile_app_config()


@app.get("/api/local/debug/huawei-recent")
def get_recent_huawei_webhooks(request: Request) -> dict[str, Any]:
    ensure_local_request(request)
    current_settings: Settings = app.state.settings
    return {
        "items": list(app.state.huawei_webhook_audit),
        "webhook_url": f"/webhooks/huawei/{current_settings.ingest_key}",
        "public_base_url": current_settings.public_base_url,
    }


@app.get("/api/profiles")
def list_profiles() -> dict[str, Any]:
    return {"items": repository.list_profiles()}


@app.get("/api/users")
def list_users() -> dict[str, Any]:
    return {"items": repository.list_users()}


@app.post("/api/users/register")
def register_user(payload: UserRegisterPayload) -> dict[str, Any]:
    try:
        user = repository.create_user(
            payload.user_id.strip(),
            payload.display_name.strip(),
            payload.secret,
            payload.notes.strip(),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="User ID already exists.") from exc
    return {"status": "created", "user": user}


@app.post("/api/users/login")
def login_user(payload: UserLoginPayload) -> dict[str, Any]:
    try:
        user = repository.verify_user_login(payload.user_id.strip(), payload.secret)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Invalid user ID or secret.") from exc
    return {"status": "verified", "user": user}


@app.post("/api/users/{user_id}/contacts")
def add_user_contact(user_id: str, payload: UserContactPayload) -> dict[str, Any]:
    try:
        user = repository.add_user_contact_link(
            owner_user_id=user_id.strip(),
            contact_user_id=payload.contact_user_id.strip(),
            relationship_label=payload.relationship_label.strip(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "linked", "user": user}


@app.delete("/api/user-contact-links/{link_id}")
def delete_user_contact(link_id: int) -> dict[str, str]:
    repository.delete_user_contact_link(link_id)
    return {"status": "deleted"}


@app.post("/api/profiles")
def create_profile(payload: ProfilePayload) -> dict[str, Any]:
    try:
        profile = repository.create_profile(
            payload.external_key.strip(),
            payload.display_name.strip(),
            payload.notes.strip(),
            payload.owner_user_id.strip() if payload.owner_user_id else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Profile external_key already exists.") from exc
    return {"status": "created", "profile": profile}


@app.put("/api/profiles/{profile_id}")
def update_profile(profile_id: int, payload: ProfilePayload) -> dict[str, Any]:
    try:
        profile = repository.update_profile(
            profile_id,
            payload.external_key.strip(),
            payload.display_name.strip(),
            payload.notes.strip(),
            payload.owner_user_id.strip() if payload.owner_user_id else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Profile external_key already exists.") from exc
    return {"status": "updated", "profile": profile}


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int) -> dict[str, str]:
    repository.delete_profile(profile_id)
    return {"status": "deleted"}


@app.post("/api/app-recipients")
def create_app_recipient(payload: AppRecipientPayload) -> dict[str, Any]:
    recipient = repository.add_app_recipient(
        profile_id=payload.profile_id,
        recipient_name=payload.recipient_name.strip(),
        app_user_id=payload.app_user_id.strip(),
        device_token=payload.device_token.strip(),
        platform=payload.platform.strip().lower(),
        severity_scope=payload.severity_scope.strip().lower(),
        priority=payload.priority,
    )
    return {"status": "created", "app_recipient": recipient}


@app.put("/api/app-recipients/{recipient_id}")
def update_app_recipient(recipient_id: int, payload: AppRecipientPayload) -> dict[str, Any]:
    try:
        recipient = repository.update_app_recipient(
            recipient_id=recipient_id,
            profile_id=payload.profile_id,
            recipient_name=payload.recipient_name.strip(),
            app_user_id=payload.app_user_id.strip(),
            device_token=payload.device_token.strip(),
            platform=payload.platform.strip().lower(),
            severity_scope=payload.severity_scope.strip().lower(),
            priority=payload.priority,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "updated", "app_recipient": recipient}


@app.delete("/api/app-recipients/{recipient_id}")
def delete_app_recipient(recipient_id: int) -> dict[str, str]:
    try:
        repository.delete_app_recipient(recipient_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "deleted"}


@app.get("/api/app-users/{app_user_id}/pending-sessions")
def list_pending_sessions(app_user_id: str) -> dict[str, Any]:
    return {"items": decorate_session_collection(repository.list_pending_sessions_for_user(app_user_id))}


@app.get("/api/app-users/{app_user_id}/sessions")
def list_sessions(app_user_id: str) -> dict[str, Any]:
    return {"items": decorate_session_collection(repository.list_sessions_for_user(app_user_id))}


@app.get("/api/live-sessions")
def list_live_sessions() -> dict[str, Any]:
    return {"items": decorate_session_collection(repository.list_active_sessions())}


@app.post("/api/sessions/cleanup")
def cleanup_sessions(payload: SessionCleanupPayload) -> dict[str, Any]:
    if payload.mode == "clear_all":
        summary = repository.clear_all_session_data()
    else:
        summary = repository.keep_latest_dispatch_only()
    refresh_runtime_cache()
    return {"status": "ok", "mode": payload.mode, "summary": summary}


@app.post("/api/mobile/register-device")
def register_mobile_device(payload: MobileDeviceRegistrationPayload) -> dict[str, Any]:
    try:
        registration = repository.register_device_for_user(
            app_user_id=payload.app_user_id.strip(),
            recipient_name=payload.recipient_name.strip(),
            device_token=payload.device_token.strip(),
            platform=payload.platform.strip().lower(),
            external_key=payload.external_key.strip() if payload.external_key else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "registered", "registration": registration}


@app.get("/api/mobile/devices/{app_user_id}/status")
def get_mobile_device_status(app_user_id: str) -> dict[str, Any]:
    return {"item": repository.get_mini_program_device(app_user_id.strip())}


@app.post("/api/mobile/wechat/login")
def bind_wechat_mini_account(payload: WeChatMiniLoginPayload) -> dict[str, Any]:
    current_notifier: WeChatMiniNotifier = app.state.notifier
    try:
        repository.register_device_for_user(
            app_user_id=payload.app_user_id.strip(),
            recipient_name=payload.recipient_name.strip(),
            device_token=payload.device_token.strip(),
            platform="wechat_miniprogram",
            external_key=payload.external_key.strip() if payload.external_key else None,
        )
        session_info = current_notifier.exchange_code_for_session(payload.code.strip())
        device = repository.bind_mini_program_openid(
            app_user_id=payload.app_user_id.strip(),
            recipient_name=payload.recipient_name.strip(),
            device_token=payload.device_token.strip(),
            platform="wechat_miniprogram",
            openid=session_info["openid"],
            unionid=session_info["unionid"],
            external_key=payload.external_key.strip() if payload.external_key else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "bound", "device": device}


@app.post("/api/mobile/subscribe-permission")
def save_mobile_subscribe_permission(payload: MobileSubscribePermissionPayload) -> dict[str, Any]:
    try:
        repository.register_device_for_user(
            app_user_id=payload.app_user_id.strip(),
            recipient_name=payload.recipient_name.strip(),
            device_token=payload.device_token.strip(),
            platform=payload.platform.strip().lower(),
            external_key=payload.external_key.strip() if payload.external_key else None,
        )
        device = repository.save_mini_program_subscription(
            app_user_id=payload.app_user_id.strip(),
            recipient_name=payload.recipient_name.strip(),
            device_token=payload.device_token.strip(),
            platform=payload.platform.strip().lower(),
            permission_result={str(key): str(value) for key, value in payload.permission_result.items()},
            external_key=payload.external_key.strip() if payload.external_key else None,
            active_template_ids=app.state.settings.mini_program_subscribe_template_ids,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "saved", "device": device}


@app.get("/api/local/desktop-alert-agent")
def get_desktop_alert_agent_state(request: Request) -> dict[str, Any]:
    ensure_local_request(request)
    return {
        "status": "ok",
        "config": load_background_agent_config(),
        "startup_enabled": startup_entry_exists(),
        "running": background_agent_is_running(),
        "runtime": load_background_agent_state(),
    }


@app.post("/api/local/desktop-alert-agent/config")
def save_desktop_alert_agent_state(request: Request, payload: DesktopAlertAgentConfigPayload) -> dict[str, Any]:
    ensure_local_request(request)
    config = save_background_agent_config(
        {
            "enabled": payload.enabled,
            "role": payload.role.strip(),
            "backend_base_url": payload.backend_base_url.strip().rstrip("/"),
            "app_user_id": payload.app_user_id.strip(),
            "recipient_name": payload.recipient_name.strip(),
            "participant_id": payload.participant_id.strip(),
            "label": payload.label.strip(),
            "platform": payload.platform.strip().lower(),
            "auto_startup": payload.auto_startup,
        }
    )
    if payload.auto_startup:
        install_startup_entry()
    else:
        remove_startup_entry()
    return {
        "status": "saved",
        "config": config,
        "startup_enabled": startup_entry_exists(),
        "running": background_agent_is_running(),
    }


@app.post("/api/local/desktop-alert-agent/startup")
def toggle_desktop_alert_agent_startup(request: Request, payload: DesktopAlertAgentStartupPayload) -> dict[str, Any]:
    ensure_local_request(request)
    config = load_background_agent_config()
    config["auto_startup"] = payload.enabled
    save_background_agent_config(config)
    if payload.enabled:
        script_path = install_startup_entry()
    else:
        remove_startup_entry()
        script_path = None
    return {
        "status": "updated",
        "startup_enabled": startup_entry_exists(),
        "script_path": str(script_path) if script_path else "",
        "running": background_agent_is_running(),
    }


@app.post("/api/local/desktop-alert-agent/start")
def start_desktop_alert_agent(request: Request) -> dict[str, Any]:
    ensure_local_request(request)
    launch_background_agent()
    return {
        "status": "started",
        "startup_enabled": startup_entry_exists(),
        "running": True,
        "config": load_background_agent_config(),
    }


@app.get("/api/call-sessions/{session_id}")
def get_call_session(session_id: str) -> dict[str, Any]:
    try:
        session = repository.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"item": decorate_session_payload(session)}


@app.post("/api/call-sessions/{session_id}/status")
def update_session_status(session_id: str, payload: SessionStatusPayload) -> dict[str, Any]:
    try:
        session = repository.update_session_status(session_id, payload.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "updated", "session": decorate_session_payload(session)}


@app.get("/api/call-sessions/{session_id}/participants")
def list_call_session_participants(session_id: str) -> dict[str, Any]:
    try:
        session = repository.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"session": session, "items": session["participants"]}


@app.post("/api/call-sessions/{session_id}/join")
def join_call_session(session_id: str, payload: SessionJoinPayload) -> dict[str, Any]:
    try:
        joined = repository.join_session(
            session_id=session_id,
            participant_id=payload.participant_id.strip(),
            role=payload.role.strip(),
            label=payload.label.strip(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "joined", **joined}


@app.post("/api/call-sessions/{session_id}/leave")
def leave_call_session(session_id: str, payload: SessionLeavePayload) -> dict[str, Any]:
    try:
        left = repository.leave_session(
            session_id=session_id,
            participant_id=payload.participant_id.strip(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "left", **left}


@app.get("/api/call-sessions/{session_id}/signals")
def list_call_session_signals(session_id: str, participant_id: str, since_id: int = 0) -> dict[str, Any]:
    try:
        signals = repository.list_signals(session_id, participant_id=participant_id.strip(), since_id=since_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": signals}


@app.post("/api/call-sessions/{session_id}/signals")
def create_call_session_signal(session_id: str, payload: SessionSignalPayload) -> dict[str, Any]:
    try:
        signal = repository.save_signal(
            session_id=session_id,
            sender_participant_id=payload.sender_participant_id.strip(),
            sender_role=payload.sender_role.strip(),
            signal_type=payload.signal_type.strip(),
            payload=payload.payload,
            target_participant_id=payload.target_participant_id.strip() if payload.target_participant_id else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "created", "signal": signal}


@app.get("/events")
def events() -> dict[str, Any]:
    return {"items": list(app.state.recent_results)}


@app.post("/api/test-alert")
def test_alert(payload: TestAlertRequest) -> dict[str, Any]:
    result = process_notification(
        {
            "subject": payload.subject,
            "message": {
                "severity": payload.severity,
                "content": payload.content,
                "external_key": payload.external_key,
            },
        }
    )
    if result is None:
        raise HTTPException(status_code=409, detail="Duplicate test alert.")
    return {"status": "triggered", "result": result}


@app.post("/webhooks/huawei/{ingest_key}")
async def huawei_webhook(ingest_key: str, request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    if ingest_key != settings.ingest_key:
        raise HTTPException(status_code=403, detail="Invalid ingest key.")

    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail="Request body must be JSON.") from exc

    message_type = str(payload.get("type") or payload.get("Type") or "").strip()

    if message_type == "SubscriptionConfirmation":
        if settings.auto_confirm_smn_subscription:
            subscribe_url = payload.get("subscribe_url") or payload.get("SubscribeURL")
            if not subscribe_url:
                raise HTTPException(status_code=400, detail="subscribe_url is missing.")
            status_code = confirm_subscription(str(subscribe_url))
            return JSONResponse(
                {
                    "status": "subscription_confirmed",
                    "subscribe_url": subscribe_url,
                    "confirmation_status_code": status_code,
                }
            )
        return JSONResponse({"status": "subscription_confirmation_received", "payload": payload})

    if message_type == "UnsubscribeConfirmation":
        return JSONResponse({"status": "unsubscribe_confirmation_received", "payload": payload})

    request_meta = {
        "client_host": request.client.host if request.client else "",
        "path": str(request.url.path),
        "user_agent": str(request.headers.get("user-agent") or ""),
    }
    background_tasks.add_task(process_huawei_webhook_payload, payload, request_meta)
    return JSONResponse({"status": "accepted", "message_type": message_type or "custom"})
