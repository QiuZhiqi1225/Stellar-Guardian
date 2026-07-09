from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.models import AlarmEvent, utc_now_iso


SEVERITY_KEYWORDS = {
    "critical": ["critical", "fatal", "emergency", "urgent", "criticality", "紧急", "严重"],
    "warning": ["warning", "warn", "attention", "告警", "异常"],
    "info": ["info", "notice", "通知"],
}

EXTERNAL_KEY_FIELDS = [
    "external_key",
    "entity_key",
    "user_id",
    "userId",
    "device_id",
    "deviceId",
    "elder_id",
    "elderId",
    "patient_id",
    "patientId",
    "imei",
    "sn",
    "serial_number",
]

TARGET_LABEL_FIELDS = [
    "display_name",
    "user_name",
    "userName",
    "device_name",
    "deviceName",
    "elder_name",
    "patient_name",
    "name",
]

LATITUDE_FIELDS = [
    "latitude",
    "lat",
    "gps_latitude",
    "gpsLatitude",
]

LONGITUDE_FIELDS = [
    "longitude",
    "lng",
    "lon",
    "gps_longitude",
    "gpsLongitude",
]

LOCATION_LABEL_FIELDS = [
    "location_name",
    "locationName",
    "place_name",
    "placeName",
    "address",
    "addr",
]

NESTED_LOCATION_FIELDS = [
    "location",
    "gps",
    "coordinate",
    "coordinates",
    "position",
    "geo",
    "geolocation",
]


@dataclass
class AccelSample:
    device_key: str
    accel_g: float
    occurred_at: str
    occurred_at_ms: int
    state: str = ""
    fall_count: int | None = None
    service_id: str = ""
    target_label: str | None = None
    location: dict[str, Any] = field(default_factory=dict)
    raw_properties: dict[str, Any] | None = None


@dataclass
class _FreefallCandidate:
    started_at_ms: int
    last_low_at_ms: int
    min_accel_g: float


class HuaweiFallDetector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._candidates: dict[str, _FreefallCandidate] = {}
        self._last_fall_counts: dict[str, int] = {}
        self._last_alarm_event_ms: dict[str, int] = {}
        self._last_states: dict[str, str] = {}

    def observe(self, sample: AccelSample, raw_payload: dict[str, Any]) -> AlarmEvent | None:
        count_event = self._observe_fall_count(sample, raw_payload)
        if count_event is not None:
            return count_event

        state_event = self._observe_alarm_state(sample, raw_payload)
        if state_event is not None:
            return state_event

        candidate = self._candidates.get(sample.device_key)
        is_low = sample.accel_g < self.settings.fall_freefall_threshold_g

        if is_low:
            if candidate is None or sample.occurred_at_ms - candidate.last_low_at_ms > self.settings.fall_impact_window_ms:
                candidate = _FreefallCandidate(
                    started_at_ms=sample.occurred_at_ms,
                    last_low_at_ms=sample.occurred_at_ms,
                    min_accel_g=sample.accel_g,
                )
            else:
                candidate.last_low_at_ms = sample.occurred_at_ms
                candidate.min_accel_g = min(candidate.min_accel_g, sample.accel_g)
            self._candidates[sample.device_key] = candidate
            return None

        if candidate is None:
            return None

        freefall_ms = max(0, candidate.last_low_at_ms - candidate.started_at_ms)
        impact_delay_ms = sample.occurred_at_ms - candidate.last_low_at_ms
        if impact_delay_ms > self.settings.fall_impact_window_ms:
            self._candidates.pop(sample.device_key, None)
            return None

        if (
            freefall_ms >= self.settings.fall_freefall_min_ms
            and sample.accel_g > self.settings.fall_impact_threshold_g
        ):
            self._candidates.pop(sample.device_key, None)
            return self._build_fall_event(sample, candidate, freefall_ms, impact_delay_ms, raw_payload)

        return None

    def _observe_alarm_state(self, sample: AccelSample, raw_payload: dict[str, Any]) -> AlarmEvent | None:
        state = str(sample.state or "").strip().upper()
        previous_state = self._last_states.get(sample.device_key, "")
        self._last_states[sample.device_key] = state

        if state != "ALARM":
            return None

        last_alarm_at = self._last_alarm_event_ms.get(sample.device_key)
        is_transition = previous_state != "ALARM"
        within_dedup_window = (
            last_alarm_at is not None
            and sample.occurred_at_ms - last_alarm_at < self.settings.fall_count_dedup_ms
        )
        if not is_transition and within_dedup_window:
            return None

        self._last_alarm_event_ms[sample.device_key] = sample.occurred_at_ms
        return self._build_alarm_state_event(sample=sample, raw_payload=raw_payload)

    def _observe_fall_count(self, sample: AccelSample, raw_payload: dict[str, Any]) -> AlarmEvent | None:
        if sample.fall_count is None:
            return None

        previous = self._last_fall_counts.get(sample.device_key)
        if sample.fall_count == 0:
            return None

        if sample.accel_g < self.settings.fall_freefall_threshold_g and (previous is None or previous == 0):
            self._last_fall_counts[sample.device_key] = sample.fall_count
            return None

        self._last_fall_counts[sample.device_key] = sample.fall_count

        if previous is None:
            return self._build_fall_count_event(
                sample=sample,
                previous=0,
                raw_payload=raw_payload,
            )
        if sample.fall_count <= previous:
            return None

        return self._build_fall_count_event(sample=sample, previous=previous, raw_payload=raw_payload)

    def _build_alarm_state_event(
        self,
        sample: AccelSample,
        raw_payload: dict[str, Any],
    ) -> AlarmEvent:
        title = "跌倒告警"
        body = (
            "设备上报了 ALARM 跌倒状态："
            f"accel={sample.accel_g:.2f}g，state={sample.state or 'ALARM'}。"
        )
        if sample.fall_count is not None:
            body = f"{body} fall_count={sample.fall_count}。"
        return AlarmEvent(
            event_id=f"fall-state-{sample.device_key}-{sample.occurred_at_ms}",
            source="huawei_iotda_fall_state",
            severity="critical",
            title=title,
            body=body,
            occurred_at=sample.occurred_at,
            target_external_key=sample.device_key,
            target_label=sample.target_label or sample.device_key,
            location=sample.location,
            raw_payload={
                "fall_detection": {
                    "device_key": sample.device_key,
                    "impact_accel_g": sample.accel_g,
                    "state": sample.state,
                    "fall_count": sample.fall_count,
                    "trigger": "alarm_state",
                    "location": sample.location,
                },
                "location": sample.location,
                "payload": raw_payload,
            },
        )

    def _build_fall_count_event(
        self,
        sample: AccelSample,
        previous: int,
        raw_payload: dict[str, Any],
    ) -> AlarmEvent:
        title = "跌倒告警"
        body = (
            "设备已上报跌倒次数增加："
            f"fall_count {previous} -> {sample.fall_count}。"
            f"当前 accel={sample.accel_g:.2f}g。"
        )
        return AlarmEvent(
            event_id=f"fall-count-{sample.device_key}-{sample.occurred_at_ms}-{sample.fall_count}",
            source="huawei_iotda_fall_count",
            severity="critical",
            title=title,
            body=body,
            occurred_at=sample.occurred_at,
            target_external_key=sample.device_key,
            target_label=sample.target_label or sample.device_key,
            location=sample.location,
            raw_payload={
                "fall_detection": {
                    "device_key": sample.device_key,
                    "fall_count_previous": previous,
                    "fall_count_current": sample.fall_count,
                    "impact_accel_g": sample.accel_g,
                    "state": sample.state,
                    "location": sample.location,
                },
                "location": sample.location,
                "payload": raw_payload,
            },
        )

    def _build_fall_event(
        self,
        sample: AccelSample,
        candidate: _FreefallCandidate,
        freefall_ms: int,
        impact_delay_ms: int,
        raw_payload: dict[str, Any],
    ) -> AlarmEvent:
        title = "跌倒告警"
        body = (
            "检测到疑似跌倒："
            f"先出现 {freefall_ms}ms 低加速度失重 "
            f"({candidate.min_accel_g:.2f}g < {self.settings.fall_freefall_threshold_g:g}g)，"
            f"随后在 {impact_delay_ms}ms 内出现 {sample.accel_g:.2f}g 冲击 "
            f"(> {self.settings.fall_impact_threshold_g:g}g)。"
        )
        if sample.fall_count is not None:
            body = f"{body} fall_count={sample.fall_count}。"
        return AlarmEvent(
            event_id=f"fall-{sample.device_key}-{sample.occurred_at_ms}-{uuid.uuid4().hex[:8]}",
            source="huawei_iotda_fall",
            severity="critical",
            title=title,
            body=body,
            occurred_at=sample.occurred_at,
            target_external_key=sample.device_key,
            target_label=sample.target_label or sample.device_key,
            location=sample.location,
            raw_payload={
                "fall_detection": {
                    "device_key": sample.device_key,
                    "freefall_ms": freefall_ms,
                    "impact_delay_ms": impact_delay_ms,
                    "min_accel_g": candidate.min_accel_g,
                    "impact_accel_g": sample.accel_g,
                    "state": sample.state,
                    "fall_count": sample.fall_count,
                    "location": sample.location,
                },
                "location": sample.location,
                "payload": raw_payload,
            },
        )


def confirm_subscription(subscribe_url: str) -> int:
    response = httpx.get(subscribe_url, timeout=10.0)
    response.raise_for_status()
    return response.status_code


def normalize_smn_message(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message") or payload.get("Message")
    if isinstance(message, str):
        try:
            parsed = json.loads(message)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"text": message}
    if isinstance(message, dict):
        return message
    return {}


def infer_severity(subject: str, message_payload: dict[str, Any], fallback_text: str) -> str:
    candidates = [
        str(message_payload.get("severity", "")),
        str(message_payload.get("level", "")),
        str(message_payload.get("status", "")),
        subject,
        fallback_text,
    ]
    combined = " ".join(item.lower() for item in candidates if item)
    for level, keywords in SEVERITY_KEYWORDS.items():
        if any(keyword in combined for keyword in keywords):
            return level
    return "critical"


def _extract_first(payload: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _parse_accel_value(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int_value(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value).strip().strip('"').strip("'")))
    except ValueError:
        return None


def _parse_coordinate_value(value: Any, *, min_value: float, max_value: float) -> float | None:
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if parsed < min_value or parsed > max_value:
        return None
    return parsed


def _parse_time_to_ms(value: Any) -> tuple[str, int]:
    raw = str(value or "").strip()
    if raw:
        try:
            numeric = float(raw)
            if numeric > 1_000_000_000_000:
                timestamp_ms = int(numeric)
                dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            else:
                dt = datetime.fromtimestamp(numeric, tz=timezone.utc)
                timestamp_ms = int(numeric * 1000)
            return dt.isoformat(), timestamp_ms
        except ValueError:
            pass
        for candidate in (raw, raw.replace("Z", "+00:00")):
            try:
                dt = datetime.fromisoformat(candidate)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat(), int(dt.timestamp() * 1000)
            except ValueError:
                pass
        for fmt in ("%Y/%m/%d %H:%M:%S GMT%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat(), int(dt.timestamp() * 1000)
            except ValueError:
                pass

    now = datetime.now(timezone.utc)
    return now.isoformat(), int(now.timestamp() * 1000)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_location_from_mapping(mapping: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}

    latitude = None
    longitude = None

    for key in LATITUDE_FIELDS:
        if key in mapping:
            latitude = _parse_coordinate_value(mapping.get(key), min_value=-90.0, max_value=90.0)
            if latitude is not None:
                break

    for key in LONGITUDE_FIELDS:
        if key in mapping:
            longitude = _parse_coordinate_value(mapping.get(key), min_value=-180.0, max_value=180.0)
            if longitude is not None:
                break

    if latitude is None or longitude is None:
        for nested_key in NESTED_LOCATION_FIELDS:
            nested_value = mapping.get(nested_key)
            if isinstance(nested_value, dict):
                nested_location = _extract_location_from_mapping(nested_value)
                if nested_location:
                    return nested_location

    if latitude is None or longitude is None:
        return {}

    return {
        "latitude": latitude,
        "longitude": longitude,
        "label": _extract_first(mapping, LOCATION_LABEL_FIELDS) or "",
    }


def extract_huawei_location(payload: dict[str, Any]) -> dict[str, Any]:
    message_payload = normalize_smn_message(payload)
    notify_data = _as_dict(payload.get("notify_data"))
    roots = [
        payload,
        message_payload,
        _as_dict(notify_data.get("header")),
        _as_dict(notify_data.get("body")),
    ]

    for service, header in _candidate_services(payload):
        properties = service.get("properties") if isinstance(service.get("properties"), dict) else service
        for candidate in (
            properties if isinstance(properties, dict) else {},
            service,
            header,
            message_payload,
            payload,
        ):
            location = _extract_location_from_mapping(candidate)
            if location:
                return location

    for root in roots:
        location = _extract_location_from_mapping(root)
        if location:
            return location

    return {}


def _candidate_services(payload: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    roots = [payload, normalize_smn_message(payload)]
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for root in roots:
        if not isinstance(root, dict):
            continue
        header = _as_dict(_as_dict(root.get("notify_data")).get("header"))
        body = _as_dict(_as_dict(root.get("notify_data")).get("body"))
        service_lists = [
            root.get("services"),
            root.get("service"),
            body.get("services"),
            body.get("service"),
            _as_dict(root.get("body")).get("services"),
            _as_dict(root.get("message")).get("services"),
        ]
        for services in service_lists:
            if isinstance(services, dict):
                pairs.append((services, header or root))
            elif isinstance(services, list):
                for service in services:
                    if isinstance(service, dict):
                        pairs.append((service, header or root))
        if "accel" in root:
            pairs.append((root, root))
        properties = root.get("properties")
        if isinstance(properties, dict) and "accel" in properties:
            pairs.append(({"properties": properties, "service_id": root.get("service_id") or root.get("serviceId")}, root))
    return pairs


def extract_iotda_accel_samples(payload: dict[str, Any]) -> list[AccelSample]:
    message_payload = normalize_smn_message(payload)
    root_location = extract_huawei_location(payload)
    samples: list[AccelSample] = []
    seen: set[tuple[str, int, float]] = set()

    for service, header in _candidate_services(payload):
        properties = service.get("properties") if isinstance(service.get("properties"), dict) else service
        accel_g = _parse_accel_value(properties.get("accel") if isinstance(properties, dict) else None)
        if accel_g is None:
            continue

        device_key = (
            _extract_first(header, EXTERNAL_KEY_FIELDS)
            or _extract_first(message_payload, EXTERNAL_KEY_FIELDS)
            or _extract_first(payload, EXTERNAL_KEY_FIELDS)
            or _extract_first(properties, EXTERNAL_KEY_FIELDS)
            or ""
        ).strip()
        if not device_key:
            continue

        event_time = (
            service.get("event_time")
            or service.get("eventTime")
            or properties.get("event_time")
            or properties.get("timestamp")
            or message_payload.get("event_time")
            or message_payload.get("timestamp")
            or payload.get("event_time")
            or payload.get("timestamp")
            or payload.get("Timestamp")
        )
        occurred_at, occurred_at_ms = _parse_time_to_ms(event_time)
        fingerprint = (device_key, occurred_at_ms, accel_g)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        samples.append(
            AccelSample(
                device_key=device_key,
                accel_g=accel_g,
                occurred_at=occurred_at,
                occurred_at_ms=occurred_at_ms,
                state=str(properties.get("state") or "").strip().strip('"').strip("'") if isinstance(properties, dict) else "",
                fall_count=_parse_int_value(properties.get("fall_count") if isinstance(properties, dict) else None),
                service_id=str(service.get("service_id") or service.get("serviceId") or "").strip(),
                target_label=_extract_first(message_payload, TARGET_LABEL_FIELDS) or _extract_first(payload, TARGET_LABEL_FIELDS),
                location=(
                    _extract_location_from_mapping(properties if isinstance(properties, dict) else {})
                    or _extract_location_from_mapping(service)
                    or _extract_location_from_mapping(header)
                    or root_location
                ),
                raw_properties=dict(properties) if isinstance(properties, dict) else {},
            )
        )

    return samples


def is_iotda_property_report(payload: dict[str, Any]) -> bool:
    message_payload = normalize_smn_message(payload)
    resource = str(payload.get("resource") or message_payload.get("resource") or "").strip().lower()
    event = str(payload.get("event") or message_payload.get("event") or "").strip().lower()
    if resource == "device.property" or event in {"report", "property_report"}:
        return True
    return bool(_candidate_services(payload))


def parse_huawei_event(payload: dict[str, Any]) -> AlarmEvent:
    message_payload = normalize_smn_message(payload)
    location = extract_huawei_location(payload)
    target_external_key = _extract_first(message_payload, EXTERNAL_KEY_FIELDS) or _extract_first(payload, EXTERNAL_KEY_FIELDS)
    target_label = _extract_first(message_payload, TARGET_LABEL_FIELDS) or _extract_first(payload, TARGET_LABEL_FIELDS)
    subject = str(
        payload.get("subject")
        or payload.get("Subject")
        or message_payload.get("subject")
        or message_payload.get("Subject")
        or "Huawei Cloud Alert"
    )
    body = str(
        message_payload.get("message")
        or message_payload.get("Message")
        or message_payload.get("alarm_content")
        or message_payload.get("content")
        or message_payload.get("text")
        or payload.get("message")
        or payload.get("Message")
        or "Received a Huawei Cloud notification."
    )
    severity = infer_severity(subject, message_payload, body)
    occurred_at = str(
        message_payload.get("occurred_at")
        or message_payload.get("event_time")
        or payload.get("timestamp")
        or payload.get("Timestamp")
        or utc_now_iso()
    )
    event_id = str(
        payload.get("message_id")
        or payload.get("MessageId")
        or message_payload.get("event_id")
        or message_payload.get("alarm_id")
        or f"evt-{uuid.uuid4().hex}"
    )

    return AlarmEvent(
        event_id=event_id,
        source="huawei_smn" if payload.get("type") or payload.get("Type") else "huawei_custom",
        severity=severity,
        title=subject,
        body=body,
        occurred_at=occurred_at,
        target_external_key=target_external_key,
        target_label=target_label,
        location=location,
        raw_payload={
            "location": location,
            "payload": payload,
        },
    )
