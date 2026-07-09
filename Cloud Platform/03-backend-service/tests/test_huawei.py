from collections import deque
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

import app.main as app_module
import app.webrtc as webrtc_module
from app.call_service import EmergencyCallService
from app.config import Settings
from app.database import DatabaseRepository
from app.huawei import HuaweiFallDetector, extract_iotda_accel_samples, normalize_smn_message, parse_huawei_event
from app.wechat_mini import WeChatMiniNotifier


@pytest.fixture
def isolated_client(tmp_path: Path):
    original_repository = app_module.repository
    original_service = app_module.service
    original_notifier = app_module.notifier
    original_processed_ids = app_module.app.state.processed_ids
    original_recent_results = app_module.app.state.recent_results
    original_fall_detector = app_module.app.state.fall_detector

    test_repository = DatabaseRepository(str(tmp_path / "test.db"))
    test_service = EmergencyCallService(settings=app_module.settings, repository=test_repository)
    test_notifier = WeChatMiniNotifier(settings=app_module.settings, repository=test_repository)
    test_fall_detector = HuaweiFallDetector(settings=app_module.settings)

    app_module.repository = test_repository
    app_module.service = test_service
    app_module.notifier = test_notifier
    app_module.app.state.repository = test_repository
    app_module.app.state.notifier = test_notifier
    app_module.app.state.fall_detector = test_fall_detector
    app_module.app.state.processed_ids = test_repository.load_processed_ids()
    app_module.app.state.recent_results = deque(test_repository.list_recent_event_results(50), maxlen=50)

    client = TestClient(app_module.app)
    try:
        yield client, test_repository
    finally:
        app_module.repository = original_repository
        app_module.service = original_service
        app_module.notifier = original_notifier
        app_module.app.state.repository = original_repository
        app_module.app.state.notifier = original_notifier
        app_module.app.state.fall_detector = original_fall_detector
        app_module.app.state.processed_ids = original_processed_ids
        app_module.app.state.recent_results = original_recent_results


def test_normalize_smn_message_accepts_json_string() -> None:
    payload = {"message": '{"severity": "critical", "content": "fall detected"}'}
    normalized = normalize_smn_message(payload)
    assert normalized["severity"] == "critical"
    assert normalized["content"] == "fall detected"


def test_parse_huawei_event_extracts_external_key() -> None:
    payload = {
        "type": "Notification",
        "message_id": "mid-1",
        "subject": "SOS triggered",
        "message": '{"content":"Device reported an emergency.","device_id":"watch-1001"}',
    }
    event = parse_huawei_event(payload)
    assert event.event_id == "mid-1"
    assert event.severity == "critical"
    assert event.target_external_key == "watch-1001"


def test_extract_iotda_accel_samples_from_property_report() -> None:
    payload = {
        "resource": "device.property",
        "event": "report",
        "notify_data": {
            "header": {"device_id": "helmet"},
            "body": {
                "services": [
                    {
                        "service_id": "FALL",
                        "event_time": "2026-07-06T09:14:17Z",
                        "properties": {"accel": '"1.01"', "state": '"NORMAL"', "fall_count": 2},
                    }
                ]
            },
        },
    }
    samples = extract_iotda_accel_samples(payload)
    assert len(samples) == 1
    assert samples[0].device_key == "helmet"
    assert samples[0].accel_g == 1.01
    assert samples[0].state == "NORMAL"
    assert samples[0].fall_count == 2


def test_extract_iotda_accel_samples_reads_location() -> None:
    payload = {
        "resource": "device.property",
        "event": "report",
        "notify_data": {
            "header": {"device_id": "helmet"},
            "body": {
                "services": [
                    {
                        "service_id": "FALL",
                        "event_time": "2026-07-06T09:14:17Z",
                        "properties": {
                            "accel": "1.01",
                            "state": "NORMAL",
                            "fall_count": 2,
                            "latitude": "31.2304",
                            "longitude": "121.4737",
                            "address": "Shanghai test point",
                        },
                    }
                ]
            },
        },
    }
    samples = extract_iotda_accel_samples(payload)
    assert len(samples) == 1
    assert samples[0].location["latitude"] == 31.2304
    assert samples[0].location["longitude"] == 121.4737
    assert samples[0].location["label"] == "Shanghai test point"


def test_iotda_accel_freefall_then_impact_dispatches_fall_alert(isolated_client) -> None:
    client, repo = isolated_client
    profile = client.post(
        "/api/profiles",
        json={"external_key": "helmet", "display_name": "Helmet", "notes": ""},
    ).json()["profile"]
    repo.add_app_recipient(
        profile["id"],
        "Mini Receiver",
        "mini-helmet-user",
        "mini-helmet-token",
        "wechat_miniprogram",
        "critical",
        1,
    )

    def report(accel: float, timestamp: str) -> dict[str, object]:
        return {
            "resource": "device.property",
            "event": "report",
            "notify_data": {
                "header": {"device_id": "helmet"},
                "body": {
                    "services": [
                        {
                            "service_id": "FALL",
                            "event_time": timestamp,
                            "properties": {"accel": str(accel), "state": "NORMAL", "fall_count": 1},
                        }
                    ]
                },
            },
        }

    assert app_module.process_notification(report(0.40, "2026-07-06T09:14:17.000+00:00"))["status"] == "telemetry_accepted"
    assert app_module.process_notification(report(0.38, "2026-07-06T09:14:17.070+00:00"))["status"] == "telemetry_accepted"

    result = app_module.process_notification(report(3.10, "2026-07-06T09:14:17.500+00:00"))
    assert result is not None
    assert result["event"]["source"] == "huawei_iotda_fall"
    assert result["event"]["title"] == "跌倒告警"
    assert result["event"]["target_external_key"] == "helmet"
    assert "疑似跌倒" in result["event"]["body"]
    assert result["recipients"] == ["mini-helmet-user"]
    assert result["sessions"][0]["platform"] == "wechat_miniprogram"


def test_iotda_fall_count_increment_dispatches_fall_alert(isolated_client) -> None:
    client, repo = isolated_client
    profile = client.post(
        "/api/profiles",
        json={"external_key": "helmet", "display_name": "Helmet", "notes": ""},
    ).json()["profile"]
    repo.add_app_recipient(
        profile["id"],
        "Mini Receiver",
        "mini-helmet-user",
        "mini-helmet-token",
        "wechat_miniprogram",
        "critical",
        1,
    )

    def report(fall_count: int, timestamp: str, accel: float = 1.02) -> dict[str, object]:
        return {
            "resource": "device.property",
            "event": "report",
            "notify_data": {
                "header": {"device_id": "helmet"},
                "body": {
                    "services": [
                        {
                            "service_id": "FALL",
                            "event_time": timestamp,
                            "properties": {"accel": str(accel), "state": "NORMAL", "fall_count": fall_count},
                        }
                    ]
                },
            },
        }

    result = app_module.process_notification(report(2, "2026-07-06T09:21:00.000+00:00"))

    assert result is not None
    assert result["event"]["source"] == "huawei_iotda_fall_count"
    assert result["event"]["title"] == "跌倒告警"
    assert result["event"]["target_external_key"] == "helmet"
    assert "fall_count 0 -> 2" in result["event"]["body"]
    assert result["recipients"] == ["mini-helmet-user"]


def test_iotda_fall_count_same_value_is_ignored(isolated_client) -> None:
    client, repo = isolated_client
    profile = client.post(
        "/api/profiles",
        json={"external_key": "helmet", "display_name": "Helmet", "notes": ""},
    ).json()["profile"]
    repo.add_app_recipient(
        profile["id"],
        "Mini Receiver",
        "mini-helmet-user",
        "mini-helmet-token",
        "wechat_miniprogram",
        "critical",
        1,
    )

    def report(fall_count: int, timestamp: str, accel: float = 1.01) -> dict[str, object]:
        return {
            "resource": "device.property",
            "event": "report",
            "notify_data": {
                "header": {"device_id": "helmet"},
                "body": {
                    "services": [
                        {
                            "service_id": "FALL",
                            "event_time": timestamp,
                            "properties": {"accel": str(accel), "state": "NORMAL", "fall_count": fall_count},
                        }
                    ]
                },
            },
        }

    first = app_module.process_notification(report(4, "2026-07-06T09:30:00.000+00:00"))
    duplicate = app_module.process_notification(report(4, "2026-07-06T09:30:02.000+00:00"))

    assert first is not None
    assert first["event"]["source"] == "huawei_iotda_fall_count"
    assert duplicate == {"status": "telemetry_accepted", "samples": 1}


def test_iotda_fall_count_zero_does_not_reset_counter(isolated_client) -> None:
    client, repo = isolated_client
    profile = client.post(
        "/api/profiles",
        json={"external_key": "helmet", "display_name": "Helmet", "notes": ""},
    ).json()["profile"]
    repo.add_app_recipient(
        profile["id"],
        "Mini Receiver",
        "mini-helmet-user",
        "mini-helmet-token",
        "wechat_miniprogram",
        "critical",
        1,
    )

    def report(fall_count: int, timestamp: str, accel: float = 1.01) -> dict[str, object]:
        return {
            "resource": "device.property",
            "event": "report",
            "notify_data": {
                "header": {"device_id": "helmet"},
                "body": {
                    "services": [
                        {
                            "service_id": "FALL",
                            "event_time": timestamp,
                            "properties": {"accel": str(accel), "state": "NORMAL", "fall_count": fall_count},
                        }
                    ]
                },
            },
        }

    first = app_module.process_notification(report(2, "2026-07-06T09:40:00.000+00:00"))
    reset = app_module.process_notification(report(0, "2026-07-06T09:40:10.000+00:00"))
    repeated_after_reset = app_module.process_notification(report(2, "2026-07-06T09:41:10.000+00:00"))
    next_real = app_module.process_notification(report(3, "2026-07-06T09:41:20.000+00:00"))

    assert first is not None
    assert first["event"]["source"] == "huawei_iotda_fall_count"
    assert reset == {"status": "telemetry_accepted", "samples": 1}
    assert repeated_after_reset == {"status": "telemetry_accepted", "samples": 1}
    assert next_real is not None
    assert next_real["event"]["source"] == "huawei_iotda_fall_count"
    assert "fall_count 2 -> 3" in next_real["event"]["body"]


def test_iotda_fall_count_same_positive_value_after_zero_is_ignored(isolated_client) -> None:
    client, repo = isolated_client
    profile = client.post(
        "/api/profiles",
        json={"external_key": "helmet", "display_name": "Helmet", "notes": ""},
    ).json()["profile"]
    repo.add_app_recipient(
        profile["id"],
        "Mini Receiver",
        "mini-helmet-user",
        "mini-helmet-token",
        "wechat_miniprogram",
        "critical",
        1,
    )

    def report(fall_count: int, timestamp: str, accel: float = 1.01) -> dict[str, object]:
        return {
            "resource": "device.property",
            "event": "report",
            "notify_data": {
                "header": {"device_id": "helmet"},
                "body": {
                    "services": [
                        {
                            "service_id": "FALL",
                            "event_time": timestamp,
                            "properties": {"accel": str(accel), "state": "NORMAL", "fall_count": fall_count},
                        }
                    ]
                },
            },
        }

    first = app_module.process_notification(report(4, "2026-07-06T09:50:00.000+00:00"))
    reset = app_module.process_notification(report(0, "2026-07-06T09:50:02.000+00:00"))
    duplicate_after_reset = app_module.process_notification(report(4, "2026-07-06T09:50:04.000+00:00"))
    next_increase = app_module.process_notification(report(5, "2026-07-06T09:50:06.000+00:00"))

    assert first is not None
    assert first["event"]["source"] == "huawei_iotda_fall_count"
    assert reset == {"status": "telemetry_accepted", "samples": 1}
    assert duplicate_after_reset == {"status": "telemetry_accepted", "samples": 1}
    assert next_increase is not None
    assert next_increase["event"]["source"] == "huawei_iotda_fall_count"
    assert "fall_count 4 -> 5" in next_increase["event"]["body"]


def test_session_payload_includes_location_from_fall_event(isolated_client) -> None:
    client, repo = isolated_client
    profile = client.post(
        "/api/profiles",
        json={"external_key": "helmet", "display_name": "Helmet", "notes": ""},
    ).json()["profile"]
    repo.add_app_recipient(
        profile["id"],
        "Mini Receiver",
        "mini-helmet-user",
        "mini-helmet-token",
        "wechat_miniprogram",
        "critical",
        1,
    )

    payload = {
        "resource": "device.property",
        "event": "report",
        "notify_data": {
            "header": {"device_id": "helmet"},
            "body": {
                "services": [
                    {
                        "service_id": "FALL",
                        "event_time": "2026-07-06T09:21:00.000+00:00",
                        "properties": {
                            "accel": "1.02",
                            "state": "NORMAL",
                            "fall_count": 2,
                            "latitude": "31.2304",
                            "longitude": "121.4737",
                            "address": "Shanghai test point",
                        },
                    }
                ]
            },
        },
    }

    result = app_module.process_notification(payload)
    assert result is not None
    session_id = result["sessions"][0]["session_id"]

    response = client.get(f"/api/call-sessions/{session_id}")
    assert response.status_code == 200
    location = response.json()["item"]["location"]
    assert location["latitude"] == 31.2304
    assert location["longitude"] == 121.4737
    assert location["label"] == "Shanghai test point"


def test_dispatch_uses_db_app_recipients(tmp_path: Path) -> None:
    settings = Settings.from_env()
    repository = DatabaseRepository(str(tmp_path / "dispatch.db"))
    profile = repository.create_profile("watch-2001", "Watch 2001", "")
    repository.add_app_recipient(
        profile["id"],
        "Daughter",
        "daughter_app_01",
        "token-01",
        "android",
        "critical",
        1,
    )
    service = EmergencyCallService(settings=settings, repository=repository)
    event = parse_huawei_event(
        {
            "type": "Notification",
            "message_id": "mid-2",
            "subject": "Critical alarm",
            "message": '{"content":"patient fall detected","device_id":"watch-2001"}',
        }
    )
    result = service.dispatch(event)
    assert result["provider"] == "app_call"
    assert result["recipients"] == ["daughter_app_01"]
    assert result["sessions"][0]["recipient_name"] == "Daughter"
    assert result["sessions"][0]["channel"] == "app_voice_call"


def test_settings_can_load_custom_webrtc_ice_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "WEBRTC_ICE_SERVERS",
        '[{"urls":["stun:stun.example.com:3478"]},{"urls":["turn:turn.example.com:3478"],"username":"demo","credential":"secret"}]',
    )
    monkeypatch.setenv("EMERGENCY_CALL_NUMBER", "+8613900000000")
    monkeypatch.setenv("MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS", '["tmpl-a","tmpl-b"]')
    monkeypatch.setenv("WECHAT_MINI_SUBSCRIBE_TEMPLATES", "[]")
    monkeypatch.setenv("TWILIO_TURN_ENABLED", "true")
    monkeypatch.setenv("TWILIO_TURN_TTL", "1200")
    settings = Settings.from_env()
    assert settings.emergency_call_number == "+8613900000000"
    assert settings.mini_program_subscribe_template_ids == ["tmpl-a", "tmpl-b"]
    assert settings.webrtc_ice_servers[0]["urls"] == ["stun:stun.example.com:3478"]
    assert settings.webrtc_ice_servers[1]["urls"] == ["turn:turn.example.com:3478"]
    assert settings.webrtc_ice_servers[1]["username"] == "demo"
    assert settings.webrtc_ice_servers[1]["credential"] == "secret"
    assert settings.twilio_turn_enabled is True
    assert settings.twilio_turn_ttl == 1200


def test_profile_and_app_recipient_api_roundtrip(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile_response = client.post(
        "/api/profiles",
        json={"external_key": f"elder-app-{suffix}", "display_name": "Elder App", "notes": "test"},
    )
    assert profile_response.status_code == 200
    profile_id = profile_response.json()["profile"]["id"]

    recipient_response = client.post(
        "/api/app-recipients",
        json={
            "profile_id": profile_id,
            "recipient_name": "Son",
            "app_user_id": f"son-user-{suffix}",
            "device_token": f"token-{suffix}",
            "platform": "android",
            "severity_scope": "critical",
            "priority": 1,
        },
    )
    assert recipient_response.status_code == 200

    dashboard_response = client.get("/api/dashboard")
    assert dashboard_response.status_code == 200
    assert any(item["external_key"] == f"elder-app-{suffix}" for item in dashboard_response.json()["profiles"])


def test_webrtc_config_endpoint_returns_ice_servers(isolated_client) -> None:
    client, _ = isolated_client
    response = client.get("/api/webrtc-config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ice_servers"]
    assert "has_turn" in payload
    assert "public_base_url" in payload
    assert payload["source"] in {"static", "static_fallback", "twilio"}


def test_backend_results_page_is_available(isolated_client) -> None:
    client, _ = isolated_client
    response = client.get("/backend-results")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_mobile_app_config_endpoint_returns_phone_and_subscribe_templates(
    isolated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = isolated_client
    original_settings = app_module.app.state.settings
    original_notifier = app_module.app.state.notifier

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://mini.example.com")
    monkeypatch.setenv("EMERGENCY_CALL_NUMBER", "120")
    monkeypatch.setenv("MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS", '["tmpl-alert-1","tmpl-alert-2"]')
    monkeypatch.setenv("WECHAT_MINI_APP_ID", "wx-demo-app")
    monkeypatch.setenv("WECHAT_MINI_APP_SECRET", "demo-secret")
    monkeypatch.setenv(
        "WECHAT_MINI_SUBSCRIBE_TEMPLATES",
        '[{"id":"tmpl-alert-1","page":"pages/detail/detail?sessionId={session_id}","data":{"thing1":{"value":"{event_title}"}}}]',
    )
    app_module.app.state.settings = Settings.from_env()
    app_module.notifier = WeChatMiniNotifier(settings=app_module.app.state.settings, repository=app_module.repository)
    app_module.app.state.notifier = app_module.notifier

    try:
        response = client.get("/api/mobile/app-config")
    finally:
        app_module.app.state.settings = original_settings
        app_module.notifier = original_notifier
        app_module.app.state.notifier = original_notifier

    assert response.status_code == 200
    payload = response.json()
    assert payload["public_base_url"] == "https://mini.example.com"
    assert payload["emergency_call_number"] == "120"
    assert payload["supports_phone_call"] is True
    assert payload["subscribe_template_ids"] == ["tmpl-alert-1", "tmpl-alert-2"]
    assert payload["wechat_login_ready"] is True
    assert payload["subscribe_send_ready"] is True
    assert payload["subscribe_template_payload_ready"] is True


def test_webrtc_config_endpoint_can_fetch_dynamic_twilio_turn_servers(
    isolated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = isolated_client
    original_settings = app_module.app.state.settings

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.trycloudflare.com")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_TURN_ENABLED", "true")
    monkeypatch.setenv("TWILIO_TURN_TTL", "1200")

    app_module.app.state.settings = Settings.from_env()

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "ttl": 1200,
                "ice_servers": [
                    {"urls": "stun:global.stun.twilio.com:3478"},
                    {
                        "urls": "turn:global.turn.twilio.com:3478?transport=udp",
                        "username": "user",
                        "credential": "pass",
                    },
                ],
            }

    def fake_post(url: str, auth: tuple[str, str], data: dict[str, int], timeout: float) -> DummyResponse:
        assert url.endswith("/Accounts/AC123/Tokens.json")
        assert auth == ("AC123", "secret")
        assert data == {"Ttl": 1200}
        assert timeout == 10.0
        return DummyResponse()

    monkeypatch.setattr(webrtc_module.httpx, "post", fake_post)

    try:
        response = client.get("/api/webrtc-config")
    finally:
        app_module.app.state.settings = original_settings

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "twilio"
    assert payload["has_turn"] is True
    assert payload["ttl_seconds"] == 1200
    assert payload["ice_servers"][1]["urls"] == ["turn:global.turn.twilio.com:3478?transport=udp"]
    assert payload["warning"] == ""


def test_webrtc_config_endpoint_falls_back_to_static_servers_when_twilio_fetch_fails(
    isolated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = isolated_client
    original_settings = app_module.app.state.settings

    monkeypatch.setenv(
        "WEBRTC_ICE_SERVERS",
        '[{"urls":["stun:stun.example.com:3478"]},{"urls":["turn:turn.example.com:3478"],"username":"demo","credential":"secret"}]',
    )
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_TURN_ENABLED", "true")

    app_module.app.state.settings = Settings.from_env()

    def fake_post(url: str, auth: tuple[str, str], data: dict[str, int], timeout: float) -> None:
        raise httpx.HTTPError("network down")

    monkeypatch.setattr(webrtc_module.httpx, "post", fake_post)

    try:
        response = client.get("/api/webrtc-config")
    finally:
        app_module.app.state.settings = original_settings

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "static_fallback"
    assert payload["warning"] == "twilio_token_fetch_failed"
    assert payload["ice_servers"][0]["urls"] == ["stun:stun.example.com:3478"]
    assert payload["ice_servers"][1]["urls"] == ["turn:turn.example.com:3478"]


def test_duplicate_profile_returns_409(isolated_client) -> None:
    client, _ = isolated_client
    payload = {"external_key": "147852369", "display_name": "Primary Device", "notes": "demo"}
    assert client.post("/api/profiles", json=payload).status_code == 200
    duplicate = client.post("/api/profiles", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Profile external_key already exists."


def test_webhook_accepts_custom_payload(isolated_client) -> None:
    client, _ = isolated_client
    response = client.post(
        f"/webhooks/huawei/{app_module.app.state.settings.ingest_key}",
        json={"subject": "Custom Alert", "message": {"severity": "warning", "content": "Battery low"}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_root_serves_dashboard_page(isolated_client) -> None:
    client, _ = isolated_client
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "华为云告警语音控制台" in response.text


def test_caregiver_device_and_room_pages_serve(isolated_client) -> None:
    client, repo = isolated_client
    profile = repo.create_profile("room-device-1", "Room Device", "")
    repo.add_app_recipient(profile["id"], "Caregiver", "caregiver-room", "token-room", "web", "all", 1)
    triggered = client.post(
        "/api/test-alert",
        json={
            "external_key": "room-device-1",
            "subject": "Room test",
            "severity": "critical",
            "content": "Need to open the room.",
        },
    )
    session_id = triggered.json()["result"]["sessions"][0]["session_id"]

    caregiver = client.get("/caregiver-demo")
    device = client.get("/device-demo")
    room = client.get(f"/app-call/{session_id}")

    assert caregiver.status_code == 200
    assert device.status_code == 200
    assert room.status_code == 200
    assert "家属端语音通话 Demo" in caregiver.text
    assert "设备端语音通话 Demo" in device.text
    assert "实时语音通话房间" in room.text


def test_user_registration_login_and_profile_binding(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]

    register = client.post(
        "/api/users/register",
        json={
            "user_id": f"owner-{suffix}",
            "display_name": "Owner User",
            "secret": "pass1234",
            "notes": "owner",
        },
    )
    assert register.status_code == 200
    assert register.json()["user"]["user_id"] == f"owner-{suffix}"

    duplicate = client.post(
        "/api/users/register",
        json={
            "user_id": f"owner-{suffix}",
            "display_name": "Owner User",
            "secret": "pass1234",
            "notes": "",
        },
    )
    assert duplicate.status_code == 409

    login = client.post(
        "/api/users/login",
        json={"user_id": f"owner-{suffix}", "secret": "pass1234"},
    )
    assert login.status_code == 200
    assert login.json()["status"] == "verified"

    wrong_login = client.post(
        "/api/users/login",
        json={"user_id": f"owner-{suffix}", "secret": "wrong"},
    )
    assert wrong_login.status_code == 401

    profile = client.post(
        "/api/profiles",
        json={
            "external_key": f"owned-{suffix}",
            "display_name": "Owned Profile",
            "notes": "bound",
            "owner_user_id": f"owner-{suffix}",
        },
    )
    assert profile.status_code == 200
    assert profile.json()["profile"]["owner_user_id"] == f"owner-{suffix}"


def test_linked_contact_sync_and_dispatch(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    owner_user_id = f"owner-{suffix}"
    contact_user_id = f"contact-{suffix}"

    assert (
        client.post(
            "/api/users/register",
            json={
                "user_id": owner_user_id,
                "display_name": "Owner",
                "secret": "pass1234",
                "notes": "",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/users/register",
            json={
                "user_id": contact_user_id,
                "display_name": "Contact",
                "secret": "pass1234",
                "notes": "",
            },
        ).status_code
        == 200
    )

    registration = client.post(
        "/api/mobile/register-device",
        json={
            "app_user_id": contact_user_id,
            "recipient_name": "Contact Device",
            "device_token": f"real-token-{suffix}",
            "platform": "web",
        },
    )
    assert registration.status_code == 200
    assert registration.json()["registration"]["linked_profiles"] == 0

    profile = client.post(
        "/api/profiles",
        json={
            "external_key": f"watch-linked-{suffix}",
            "display_name": "Linked Watch",
            "notes": "",
            "owner_user_id": owner_user_id,
        },
    )
    assert profile.status_code == 200

    link = client.post(
        f"/api/users/{owner_user_id}/contacts",
        json={"contact_user_id": contact_user_id, "relationship_label": "daughter"},
    )
    assert link.status_code == 200
    assert link.json()["user"]["contacts"][0]["contact_user_id"] == contact_user_id

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    bound_profile = next(
        item for item in dashboard.json()["profiles"] if item["external_key"] == f"watch-linked-{suffix}"
    )
    linked_recipient = next(
        item for item in bound_profile["app_recipients"] if item["app_user_id"] == contact_user_id
    )
    assert linked_recipient["source_type"] == "linked"
    assert linked_recipient["device_token"] == f"real-token-{suffix}"

    alert = client.post(
        "/api/test-alert",
        json={
            "external_key": f"watch-linked-{suffix}",
            "subject": "Linked dispatch",
            "severity": "critical",
            "content": "Need linked contact.",
        },
    )
    assert alert.status_code == 200
    assert alert.json()["result"]["sessions"][0]["app_user_id"] == contact_user_id


def test_legacy_demo_recipients_are_removed_and_linked_recipients_take_over(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-demo.db"
    repo = DatabaseRepository(str(db_path))

    owner_user = repo.create_user("qiu_01", "邱智齐", "pass1234", "")
    repo.create_user("qiuba_01", "邱智齐爸爸", "pass1234", "")
    repo.add_user_contact_link(owner_user["user_id"], "qiuba_01", "父子")

    profile = repo.create_profile("147852369", "邱智齐", "legacy demo profile")
    repo.add_app_recipient(
        profile["id"],
        "邱智齐爸爸",
        "qiu_father_001",
        "web-demo-father",
        "web",
        "all",
        1,
    )
    repo.add_app_recipient(
        profile["id"],
        "邱智齐妈妈",
        "qiu_mother_001",
        "web-demo-mother",
        "web",
        "all",
        2,
    )
    repo.register_device_for_user("qiuba_01", "邱智齐爸爸", "real-linked-token", "web")

    reloaded = DatabaseRepository(str(db_path))
    migrated_profile = next(item for item in reloaded.list_profiles() if item["external_key"] == "147852369")

    assert migrated_profile["owner_user_id"] == "qiu_01"
    assert all(item["app_user_id"] not in {"qiu_father_001", "qiu_mother_001"} for item in migrated_profile["app_recipients"])

    recipients = reloaded.resolve_app_recipients("147852369", "critical")
    assert [item["app_user_id"] for item in recipients] == ["qiuba_01"]
    assert recipients[0]["source_type"] == "linked"


def test_manual_test_alert_creates_app_call_sessions(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile_response = client.post(
        "/api/profiles",
        json={"external_key": f"elder-api-{suffix}", "display_name": "Elder API", "notes": ""},
    )
    profile_id = profile_response.json()["profile"]["id"]
    client.post(
        "/api/app-recipients",
        json={
            "profile_id": profile_id,
            "recipient_name": "Daughter",
            "app_user_id": f"daughter-{suffix}",
            "device_token": f"token-{suffix}",
            "platform": "ios",
            "severity_scope": "warning",
            "priority": 1,
        },
    )

    response = client.post(
        "/api/test-alert",
        json={
            "external_key": f"elder-api-{suffix}",
            "subject": "Manual test",
            "severity": "warning",
            "content": "Battery is below threshold.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "triggered"
    assert body["result"]["sessions"][0]["app_user_id"] == f"daughter-{suffix}"


def test_pending_sessions_and_status_update(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile_response = client.post(
        "/api/profiles",
        json={"external_key": f"elder-session-{suffix}", "display_name": "Elder Session", "notes": ""},
    )
    profile_id = profile_response.json()["profile"]["id"]
    client.post(
        "/api/app-recipients",
        json={
            "profile_id": profile_id,
            "recipient_name": "Caregiver",
            "app_user_id": f"caregiver-{suffix}",
            "device_token": f"token-{suffix}",
            "platform": "android",
            "severity_scope": "critical",
            "priority": 1,
        },
    )
    trigger = client.post(
        "/api/test-alert",
        json={
            "external_key": f"elder-session-{suffix}",
            "subject": "Session test",
            "severity": "critical",
            "content": "Need call session.",
        },
    )
    session_id = trigger.json()["result"]["sessions"][0]["session_id"]

    pending = client.get(f"/api/app-users/caregiver-{suffix}/pending-sessions")
    assert pending.status_code == 200
    assert any(item["session_id"] == session_id for item in pending.json()["items"])

    status = client.post(f"/api/call-sessions/{session_id}/status", json={"status": "accepted"})
    assert status.status_code == 200
    assert status.json()["session"]["status"] == "accepted"


def test_pending_sessions_include_alert_context_and_callback_phone(
    isolated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = isolated_client
    original_settings = app_module.app.state.settings
    suffix = uuid4().hex[:8]

    monkeypatch.setenv("EMERGENCY_CALL_NUMBER", "120")
    app_module.app.state.settings = Settings.from_env()

    try:
        profile_response = client.post(
            "/api/profiles",
            json={"external_key": f"mini-{suffix}", "display_name": "Mini Device", "notes": "mini"},
        )
        profile_id = profile_response.json()["profile"]["id"]
        client.post(
            "/api/app-recipients",
            json={
                "profile_id": profile_id,
                "recipient_name": "Mini User",
                "app_user_id": f"mini-user-{suffix}",
                "device_token": f"mini-token-{suffix}",
                "platform": "wechat_miniprogram",
                "severity_scope": "critical",
                "priority": 1,
            },
        )
        trigger = client.post(
            "/api/test-alert",
            json={
                "external_key": f"mini-{suffix}",
                "subject": "Mini Alert",
                "severity": "critical",
                "content": "Need phone callback.",
            },
        )
        session_id = trigger.json()["result"]["sessions"][0]["session_id"]

        pending = client.get(f"/api/app-users/mini-user-{suffix}/pending-sessions")
        assert pending.status_code == 200
        item = pending.json()["items"][0]
        assert item["session_id"] == session_id
        assert item["callback_phone"] == "120"
        assert item["can_make_phone_call"] is True
        assert item["event_title"] == "Mini Alert"
        assert item["event_body"] == "Need phone callback."
        assert item["event_severity"] == "critical"
        assert item["target_external_key"] == f"mini-{suffix}"
        assert item["profile_display_name"] == "Mini Device"

        session = client.get(f"/api/call-sessions/{session_id}")
        assert session.status_code == 200
        assert session.json()["item"]["callback_phone"] == "120"
        assert session.json()["item"]["event"]["title"] == "Mini Alert"
    finally:
        app_module.app.state.settings = original_settings


def test_mobile_registration_and_session_queries(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile = client.post(
        "/api/profiles",
        json={"external_key": f"watch-{suffix}", "display_name": "Watch Demo", "notes": ""},
    ).json()["profile"]
    client.post(
        "/api/app-recipients",
        json={
            "profile_id": profile["id"],
            "recipient_name": "Father",
            "app_user_id": f"father-{suffix}",
            "device_token": "placeholder-token",
            "platform": "android",
            "severity_scope": "all",
            "priority": 1,
        },
    )

    registration = client.post(
        "/api/mobile/register-device",
        json={
            "app_user_id": f"father-{suffix}",
            "recipient_name": "Father Updated",
            "device_token": f"real-token-{suffix}",
            "platform": "android",
        },
    )
    assert registration.status_code == 200
    assert registration.json()["registration"]["device_token"] == f"real-token-{suffix}"

    trigger = client.post(
        "/api/test-alert",
        json={
            "external_key": f"watch-{suffix}",
            "subject": "Demo",
            "severity": "critical",
            "content": "Need caregiver attention.",
        },
    )
    session_id = trigger.json()["result"]["sessions"][0]["session_id"]

    session = client.get(f"/api/call-sessions/{session_id}")
    assert session.status_code == 200
    assert session.json()["item"]["app_user_id"] == f"father-{suffix}"

    history = client.get(f"/api/app-users/father-{suffix}/sessions")
    assert history.status_code == 200
    assert any(item["session_id"] == session_id for item in history.json()["items"])


def test_mobile_registration_can_create_custom_app_user_on_first_use(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile = client.post(
        "/api/profiles",
        json={"external_key": f"self-create-{suffix}", "display_name": "Self Create Demo", "notes": ""},
    ).json()["profile"]

    registration = client.post(
        "/api/mobile/register-device",
        json={
            "app_user_id": f"custom-user-{suffix}",
            "recipient_name": "Custom User",
            "device_token": f"custom-token-{suffix}",
            "platform": "android",
            "external_key": profile["external_key"],
        },
    )
    assert registration.status_code == 200
    assert registration.json()["registration"]["app_user_id"] == f"custom-user-{suffix}"
    assert registration.json()["registration"]["linked_profiles"] == 1

    dashboard = client.get("/api/dashboard").json()
    matched = [
        recipient
        for item in dashboard["profiles"]
        if item["external_key"] == profile["external_key"]
        for recipient in item["app_recipients"]
        if recipient["app_user_id"] == f"custom-user-{suffix}"
    ]
    assert matched


def test_mobile_registration_can_bind_existing_registered_user_to_external_key(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]

    user = client.post(
        "/api/users/register",
        json={
            "user_id": f"bound-user-{suffix}",
            "display_name": "Bound User",
            "secret": "pass1234",
            "notes": "",
        },
    )
    assert user.status_code == 200

    profile = client.post(
        "/api/profiles",
        json={"external_key": f"bind-watch-{suffix}", "display_name": "Bind Watch", "notes": ""},
    ).json()["profile"]

    registration = client.post(
        "/api/mobile/register-device",
        json={
            "app_user_id": f"bound-user-{suffix}",
            "recipient_name": "Bound User",
            "device_token": f"bound-token-{suffix}",
            "platform": "wechat_miniprogram",
            "external_key": profile["external_key"],
        },
    )
    assert registration.status_code == 200
    assert registration.json()["registration"]["linked_profiles"] == 1

    trigger = client.post(
        "/api/test-alert",
        json={
            "external_key": profile["external_key"],
            "subject": "Bound User Alert",
            "severity": "critical",
            "content": "Please dispatch to the bound user.",
        },
    )
    assert trigger.status_code == 200
    assert trigger.json()["result"]["sessions"][0]["app_user_id"] == f"bound-user-{suffix}"


def test_wechat_login_and_subscribe_permission_persist_device_state(
    isolated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = isolated_client
    original_settings = app_module.app.state.settings
    original_notifier = app_module.app.state.notifier
    suffix = uuid4().hex[:8]

    monkeypatch.setenv("WECHAT_MINI_APP_ID", "wx-test-app")
    monkeypatch.setenv("WECHAT_MINI_APP_SECRET", "wx-test-secret")
    monkeypatch.setenv("MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS", '["tmpl-alert-1","tmpl-alert-2"]')
    monkeypatch.setenv(
        "WECHAT_MINI_SUBSCRIBE_TEMPLATES",
        '[{"id":"tmpl-alert-1","page":"pages/detail/detail?sessionId={session_id}","data":{"thing1":{"value":"{event_title}"}}}]',
    )
    app_module.app.state.settings = Settings.from_env()
    app_module.notifier = WeChatMiniNotifier(settings=app_module.app.state.settings, repository=app_module.repository)
    app_module.app.state.notifier = app_module.notifier

    class DummyResponse:
        def __init__(self, payload: dict[str, str]) -> None:
            self.payload = payload

        def json(self) -> dict[str, str]:
            return self.payload

    class DummyWeChatClient:
        def get(self, url: str, params: dict[str, str], timeout: float) -> DummyResponse:
            assert timeout == 10.0
            assert "jscode2session" in url
            assert params["appid"] == "wx-test-app"
            assert params["secret"] == "wx-test-secret"
            assert params["js_code"] == "test-code"
            return DummyResponse({"openid": f"openid-{suffix}", "session_key": "session-key"})

    app_module.notifier.http_client = DummyWeChatClient()
    app_module.app.state.notifier.http_client = app_module.notifier.http_client

    try:
        profile = client.post(
            "/api/profiles",
            json={"external_key": f"wx-{suffix}", "display_name": "WeChat Device", "notes": ""},
        ).json()["profile"]

        login = client.post(
            "/api/mobile/wechat/login",
            json={
                "app_user_id": f"mini-user-{suffix}",
                "recipient_name": "Mini User",
                "device_token": f"mini-token-{suffix}",
                "code": "test-code",
                "external_key": profile["external_key"],
            },
        )
        assert login.status_code == 200
        assert login.json()["device"]["has_wechat_openid"] is True

        permission = client.post(
            "/api/mobile/subscribe-permission",
            json={
                "app_user_id": f"mini-user-{suffix}",
                "recipient_name": "Mini User",
                "device_token": f"mini-token-{suffix}",
                "platform": "wechat_miniprogram",
                "permission_result": {
                    "tmpl-alert-1": "accept",
                    "tmpl-alert-2": "reject",
                },
                "external_key": profile["external_key"],
            },
        )
        assert permission.status_code == 200
        assert permission.json()["device"]["granted_template_ids"] == ["tmpl-alert-1"]
        assert permission.json()["device"]["granted_template_count"] == 1

        second_permission = client.post(
            "/api/mobile/subscribe-permission",
            json={
                "app_user_id": f"mini-user-{suffix}",
                "recipient_name": "Mini User",
                "device_token": f"mini-token-{suffix}",
                "platform": "wechat_miniprogram",
                "permission_result": {
                    "tmpl-alert-1": "accept",
                },
                "external_key": profile["external_key"],
            },
        )
        assert second_permission.status_code == 200
        assert second_permission.json()["device"]["granted_template_ids"] == ["tmpl-alert-1", "tmpl-alert-1"]
        assert second_permission.json()["device"]["granted_template_count"] == 2
        assert second_permission.json()["device"]["granted_template_counts"] == {"tmpl-alert-1": 2}

        status = client.get(f"/api/mobile/devices/mini-user-{suffix}/status")
        assert status.status_code == 200
        assert status.json()["item"]["notification_enabled"] is True
        assert status.json()["item"]["granted_template_ids"] == ["tmpl-alert-1", "tmpl-alert-1"]
        assert status.json()["item"]["granted_template_count"] == 2
    finally:
        app_module.app.state.settings = original_settings
        app_module.notifier = original_notifier
        app_module.app.state.notifier = original_notifier


def test_test_alert_dispatch_sends_wechat_subscribe_notification_and_consumes_one_grant(
    isolated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, repo = isolated_client
    original_settings = app_module.app.state.settings
    original_notifier = app_module.app.state.notifier
    suffix = uuid4().hex[:8]

    monkeypatch.setenv("WECHAT_MINI_APP_ID", "wx-send-app")
    monkeypatch.setenv("WECHAT_MINI_APP_SECRET", "wx-send-secret")
    monkeypatch.setenv("MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS", '["tmpl-alert-1"]')
    monkeypatch.setenv(
        "WECHAT_MINI_SUBSCRIBE_TEMPLATES",
        '[{"id":"tmpl-alert-1","page":"pages/detail/detail?sessionId={session_id}","data":{"thing1":{"value":"{event_title}"},"thing2":{"value":"{external_key}"}}}]',
    )
    app_module.app.state.settings = Settings.from_env()
    app_module.notifier = WeChatMiniNotifier(settings=app_module.app.state.settings, repository=repo)
    app_module.app.state.notifier = app_module.notifier

    class DummyResponse:
        def __init__(self, payload: dict[str, str | int]) -> None:
            self.payload = payload

        def json(self) -> dict[str, str | int]:
            return self.payload

    class DummyWeChatClient:
        def __init__(self) -> None:
            self.sent_payloads: list[dict[str, object]] = []

        def get(self, url: str, params: dict[str, str], timeout: float) -> DummyResponse:
            assert timeout == 10.0
            if "cgi-bin/token" in url:
                return DummyResponse({"access_token": "access-token", "expires_in": 7200})
            raise AssertionError(f"Unexpected GET URL: {url}")

        def post(self, url: str, json: dict[str, object], timeout: float) -> DummyResponse:
            assert timeout == 10.0
            assert "message/subscribe/send" in url
            self.sent_payloads.append(json)
            return DummyResponse({"errcode": 0, "errmsg": "ok", "msgid": 123})

    dummy_client = DummyWeChatClient()
    app_module.notifier.http_client = dummy_client
    app_module.app.state.notifier.http_client = dummy_client

    try:
        profile = client.post(
            "/api/profiles",
            json={"external_key": f"notify-{suffix}", "display_name": "Notify Device", "notes": ""},
        ).json()["profile"]
        client.post(
            "/api/app-recipients",
            json={
                "profile_id": profile["id"],
                "recipient_name": "Mini Receiver",
                "app_user_id": f"mini-receiver-{suffix}",
                "device_token": f"mini-token-{suffix}",
                "platform": "wechat_miniprogram",
                "severity_scope": "critical",
                "priority": 1,
            },
        )
        repo.bind_mini_program_openid(
            app_user_id=f"mini-receiver-{suffix}",
            recipient_name="Mini Receiver",
            device_token=f"mini-token-{suffix}",
            platform="wechat_miniprogram",
            openid=f"openid-{suffix}",
        )
        repo.save_mini_program_subscription(
            app_user_id=f"mini-receiver-{suffix}",
            recipient_name="Mini Receiver",
            device_token=f"mini-token-{suffix}",
            platform="wechat_miniprogram",
            permission_result={"tmpl-alert-1": "accept"},
            active_template_ids=["tmpl-alert-1"],
        )
        repo.save_mini_program_subscription(
            app_user_id=f"mini-receiver-{suffix}",
            recipient_name="Mini Receiver",
            device_token=f"mini-token-{suffix}",
            platform="wechat_miniprogram",
            permission_result={"tmpl-alert-1": "accept"},
            active_template_ids=["tmpl-alert-1"],
        )

        response = client.post(
            "/api/test-alert",
            json={
                "external_key": profile["external_key"],
                "subject": "Mini Push",
                "severity": "critical",
                "content": "Need immediate callback.",
            },
        )
        assert response.status_code == 200
        payload = response.json()["result"]
        assert payload["notification_summary"]["sent"] == 1
        assert dummy_client.sent_payloads[0]["template_id"] == "tmpl-alert-1"
        assert dummy_client.sent_payloads[0]["page"]
        assert dummy_client.sent_payloads[0]["data"]["thing1"]["value"] == "Mini Push"
        assert dummy_client.sent_payloads[0]["data"]["thing2"]["value"] == "Notify Device"

        device = repo.get_mini_program_device(f"mini-receiver-{suffix}")
        assert device is not None
        assert device["notification_enabled"] is True
        assert device["granted_template_ids"] == ["tmpl-alert-1"]
        assert device["granted_template_count"] == 1
        assert device["last_notification_status"] == "sent"
    finally:
        app_module.app.state.settings = original_settings
        app_module.notifier = original_notifier
        app_module.app.state.notifier = original_notifier


def test_subscribe_permission_filters_stale_template_ids_to_current_settings(isolated_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, repo = isolated_client
    original_settings = app_module.app.state.settings
    original_notifier = app_module.app.state.notifier
    suffix = uuid4().hex[:8]

    monkeypatch.setenv("WECHAT_MINI_APP_ID", "wx-filter-app")
    monkeypatch.setenv("WECHAT_MINI_APP_SECRET", "wx-filter-secret")
    monkeypatch.setenv("MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS", '["tmpl-active"]')
    monkeypatch.setenv(
        "WECHAT_MINI_SUBSCRIBE_TEMPLATES",
        '[{"id":"tmpl-active","page":"pages/detail/detail?sessionId={session_id}","data":{"thing1":{"value":"{event_title}"}}}]',
    )
    app_module.app.state.settings = Settings.from_env()
    app_module.notifier = WeChatMiniNotifier(settings=app_module.app.state.settings, repository=repo)
    app_module.app.state.notifier = app_module.notifier

    try:
        profile = client.post(
            "/api/profiles",
            json={"external_key": f"filter-{suffix}", "display_name": "Filter Device", "notes": ""},
        ).json()["profile"]
        repo.bind_mini_program_openid(
            app_user_id=f"mini-filter-{suffix}",
            recipient_name="Mini Filter",
            device_token=f"mini-token-{suffix}",
            platform="wechat_miniprogram",
            openid=f"openid-{suffix}",
            external_key=profile["external_key"],
        )
        repo.save_mini_program_subscription(
            app_user_id=f"mini-filter-{suffix}",
            recipient_name="Mini Filter",
            device_token=f"mini-token-{suffix}",
            platform="wechat_miniprogram",
            permission_result={"tmpl-legacy": "accept"},
        )

        response = client.post(
            "/api/mobile/subscribe-permission",
            json={
                "app_user_id": f"mini-filter-{suffix}",
                "recipient_name": "Mini Filter",
                "device_token": f"mini-token-{suffix}",
                "platform": "wechat_miniprogram",
                "permission_result": {"tmpl-legacy": "accept"},
                "external_key": profile["external_key"],
            },
        )
        assert response.status_code == 200
        device = response.json()["device"]
        assert device["granted_template_ids"] == []
        assert device["granted_template_count"] == 0
        assert device["notification_enabled"] is False
    finally:
        app_module.app.state.settings = original_settings
        app_module.notifier = original_notifier
        app_module.app.state.notifier = original_notifier


def test_test_alert_dispatch_reports_reauthorize_when_only_stale_template_grants_exist(
    isolated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, repo = isolated_client
    original_settings = app_module.app.state.settings
    original_notifier = app_module.app.state.notifier
    suffix = uuid4().hex[:8]

    monkeypatch.setenv("WECHAT_MINI_APP_ID", "wx-stale-app")
    monkeypatch.setenv("WECHAT_MINI_APP_SECRET", "wx-stale-secret")
    monkeypatch.setenv("MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS", '["tmpl-active"]')
    monkeypatch.setenv(
        "WECHAT_MINI_SUBSCRIBE_TEMPLATES",
        '[{"id":"tmpl-active","page":"pages/detail/detail?sessionId={session_id}","data":{"thing1":{"value":"{event_title}"}}}]',
    )
    app_module.app.state.settings = Settings.from_env()
    app_module.notifier = WeChatMiniNotifier(settings=app_module.app.state.settings, repository=repo)
    app_module.app.state.notifier = app_module.notifier

    try:
        profile = client.post(
            "/api/profiles",
            json={"external_key": f"stale-{suffix}", "display_name": "Stale Device", "notes": ""},
        ).json()["profile"]
        client.post(
            "/api/app-recipients",
            json={
                "profile_id": profile["id"],
                "recipient_name": "Mini Receiver",
                "app_user_id": f"mini-stale-{suffix}",
                "device_token": f"mini-token-{suffix}",
                "platform": "wechat_miniprogram",
                "severity_scope": "critical",
                "priority": 1,
            },
        )
        repo.bind_mini_program_openid(
            app_user_id=f"mini-stale-{suffix}",
            recipient_name="Mini Receiver",
            device_token=f"mini-token-{suffix}",
            platform="wechat_miniprogram",
            openid=f"openid-{suffix}",
        )
        repo.save_mini_program_subscription(
            app_user_id=f"mini-stale-{suffix}",
            recipient_name="Mini Receiver",
            device_token=f"mini-token-{suffix}",
            platform="wechat_miniprogram",
            permission_result={"tmpl-legacy": "accept"},
        )

        response = client.post(
            "/api/test-alert",
            json={
                "external_key": profile["external_key"],
                "subject": "Mini Push",
                "severity": "critical",
                "content": "Need immediate callback.",
            },
        )
        assert response.status_code == 200
        payload = response.json()["result"]
        assert payload["notification_summary"]["sent"] == 0
        assert payload["notification_summary"]["failed"] == 1
        assert "active_template_grant_missing_reauthorize_required" in payload["notification_summary"]["reasons"]

        device = repo.get_mini_program_device(f"mini-stale-{suffix}")
        assert device is not None
        assert device["last_notification_status"] == "failed"
        assert device["last_notification_error"] == "active_template_grant_missing_reauthorize_required"
    finally:
        app_module.app.state.settings = original_settings
        app_module.notifier = original_notifier
        app_module.app.state.notifier = original_notifier


def test_session_join_and_signal_roundtrip(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile = client.post(
        "/api/profiles",
        json={"external_key": f"webrtc-{suffix}", "display_name": "WebRTC Device", "notes": ""},
    ).json()["profile"]
    client.post(
        "/api/app-recipients",
        json={
            "profile_id": profile["id"],
            "recipient_name": "Mother",
            "app_user_id": f"mother-{suffix}",
            "device_token": f"token-{suffix}",
            "platform": "web",
            "severity_scope": "all",
            "priority": 1,
        },
    )
    trigger = client.post(
        "/api/test-alert",
        json={
            "external_key": f"webrtc-{suffix}",
            "subject": "Voice session",
            "severity": "critical",
            "content": "Join the voice room.",
        },
    )
    session_id = trigger.json()["result"]["sessions"][0]["session_id"]

    caregiver_join = client.post(
        f"/api/call-sessions/{session_id}/join",
        json={"participant_id": f"caregiver-{suffix}", "role": "caregiver", "label": "Caregiver"},
    )
    device_join = client.post(
        f"/api/call-sessions/{session_id}/join",
        json={"participant_id": f"device-{suffix}", "role": "device", "label": "Device"},
    )
    assert caregiver_join.status_code == 200
    assert device_join.status_code == 200

    participants = client.get(f"/api/call-sessions/{session_id}/participants")
    assert participants.status_code == 200
    assert len(participants.json()["items"]) == 2

    offer = client.post(
        f"/api/call-sessions/{session_id}/signals",
        json={
            "sender_participant_id": f"caregiver-{suffix}",
            "sender_role": "caregiver",
            "signal_type": "offer",
            "payload": {"type": "offer", "sdp": "fake-offer"},
            "target_participant_id": f"device-{suffix}",
        },
    )
    assert offer.status_code == 200

    device_signals = client.get(
        f"/api/call-sessions/{session_id}/signals",
        params={"participant_id": f"device-{suffix}", "since_id": 0},
    )
    assert device_signals.status_code == 200
    assert device_signals.json()["items"][0]["payload"]["sdp"] == "fake-offer"

    answer = client.post(
        f"/api/call-sessions/{session_id}/signals",
        json={
            "sender_participant_id": f"device-{suffix}",
            "sender_role": "device",
            "signal_type": "answer",
            "payload": {"type": "answer", "sdp": "fake-answer"},
            "target_participant_id": f"caregiver-{suffix}",
        },
    )
    assert answer.status_code == 200

    caregiver_signals = client.get(
        f"/api/call-sessions/{session_id}/signals",
        params={"participant_id": f"caregiver-{suffix}", "since_id": 0},
    )
    payloads = [item["payload"]["sdp"] for item in caregiver_signals.json()["items"] if "sdp" in item["payload"]]
    assert "fake-answer" in payloads


def test_session_leave_removes_participant_and_ends_last_member_session(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile = client.post(
        "/api/profiles",
        json={"external_key": f"leave-{suffix}", "display_name": "Leave Device", "notes": ""},
    ).json()["profile"]
    client.post(
        "/api/app-recipients",
        json={
            "profile_id": profile["id"],
            "recipient_name": "Leave User",
            "app_user_id": f"leave-user-{suffix}",
            "device_token": f"token-{suffix}",
            "platform": "web",
            "severity_scope": "all",
            "priority": 1,
        },
    )
    trigger = client.post(
        "/api/test-alert",
        json={
            "external_key": f"leave-{suffix}",
            "subject": "Leave session",
            "severity": "critical",
            "content": "Leave the room.",
        },
    )
    session_id = trigger.json()["result"]["sessions"][0]["session_id"]

    client.post(
        f"/api/call-sessions/{session_id}/join",
        json={"participant_id": f"caregiver-{suffix}", "role": "caregiver", "label": "Caregiver"},
    )
    client.post(
        f"/api/call-sessions/{session_id}/join",
        json={"participant_id": f"device-{suffix}", "role": "device", "label": "Device"},
    )

    first_leave = client.post(
        f"/api/call-sessions/{session_id}/leave",
        json={"participant_id": f"caregiver-{suffix}"},
    )
    assert first_leave.status_code == 200
    assert first_leave.json()["remaining_participants"] == 1
    assert first_leave.json()["session"]["status"] == "ringing"

    second_leave = client.post(
        f"/api/call-sessions/{session_id}/leave",
        json={"participant_id": f"device-{suffix}"},
    )
    assert second_leave.status_code == 200
    assert second_leave.json()["remaining_participants"] == 0
    assert second_leave.json()["session"]["status"] == "ended"


def test_keep_latest_cleanup_removes_older_sessions(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile = client.post(
        "/api/profiles",
        json={"external_key": f"cleanup-{suffix}", "display_name": "Cleanup Device", "notes": ""},
    ).json()["profile"]
    client.post(
        "/api/app-recipients",
        json={
            "profile_id": profile["id"],
            "recipient_name": "Cleanup User",
            "app_user_id": f"cleanup-user-{suffix}",
            "device_token": f"token-{suffix}",
            "platform": "web",
            "severity_scope": "all",
            "priority": 1,
        },
    )

    first = client.post(
        "/api/test-alert",
        json={
            "external_key": f"cleanup-{suffix}",
            "subject": "First",
            "severity": "critical",
            "content": "First event",
        },
    ).json()["result"]
    second = client.post(
        "/api/test-alert",
        json={
            "external_key": f"cleanup-{suffix}",
            "subject": "Second",
            "severity": "critical",
            "content": "Second event",
        },
    ).json()["result"]

    response = client.post("/api/sessions/cleanup", json={"mode": "keep_latest"})
    assert response.status_code == 200
    assert response.json()["summary"]["kept_event_id"] == second["event"]["event_id"]

    live = client.get("/api/live-sessions").json()["items"]
    session_ids = [item["session_id"] for item in live]
    assert second["sessions"][0]["session_id"] in session_ids
    assert first["sessions"][0]["session_id"] not in session_ids


def test_clear_all_cleanup_removes_everything(isolated_client) -> None:
    client, _ = isolated_client
    suffix = uuid4().hex[:8]
    profile = client.post(
        "/api/profiles",
        json={"external_key": f"cleanup-all-{suffix}", "display_name": "Cleanup All", "notes": ""},
    ).json()["profile"]
    client.post(
        "/api/app-recipients",
        json={
            "profile_id": profile["id"],
            "recipient_name": "Cleanup All User",
            "app_user_id": f"cleanup-all-user-{suffix}",
            "device_token": f"token-all-{suffix}",
            "platform": "web",
            "severity_scope": "all",
            "priority": 1,
        },
    )
    client.post(
        "/api/test-alert",
        json={
            "external_key": f"cleanup-all-{suffix}",
            "subject": "To clear",
            "severity": "critical",
            "content": "Clear all event",
        },
    )

    response = client.post("/api/sessions/cleanup", json={"mode": "clear_all"})
    assert response.status_code == 200
    assert client.get("/api/live-sessions").json()["items"] == []
    assert client.get("/events").json()["items"] == []
