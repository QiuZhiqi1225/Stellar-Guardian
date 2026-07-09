from __future__ import annotations

import uuid

from app.models import CallAttempt
from app.providers.base import CallProvider


class MockCallProvider(CallProvider):
    name = "mock"

    def place_call(self, contact: str, message: str, event_id: str) -> CallAttempt:
        reference = f"mock-{uuid.uuid4().hex[:12]}"
        return CallAttempt(
            contact=contact,
            provider=self.name,
            accepted=True,
            reference=reference,
            detail=f"Mock call queued for {contact}: {message[:80]}",
        )
