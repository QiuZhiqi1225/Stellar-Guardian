from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AlarmEvent:
    event_id: str
    source: str
    severity: str
    title: str
    body: str
    occurred_at: str
    target_external_key: str | None = None
    target_label: str | None = None
    location: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CallAttempt:
    contact: str
    provider: str
    accepted: bool
    reference: str
    detail: str
    contact_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
