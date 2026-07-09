from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import CallAttempt


class CallProvider(ABC):
    name: str

    @abstractmethod
    def place_call(self, contact: str, message: str, event_id: str) -> CallAttempt:
        raise NotImplementedError
