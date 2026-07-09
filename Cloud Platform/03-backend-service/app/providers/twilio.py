from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.models import CallAttempt
from app.providers.base import CallProvider


class TwilioCallProvider(CallProvider):
    name = "twilio"

    def __init__(self, settings: Settings) -> None:
        if not settings.twilio_account_sid or not settings.twilio_auth_token or not settings.twilio_from_number:
            raise ValueError("Twilio credentials are incomplete.")
        self._account_sid = settings.twilio_account_sid
        self._auth_token = settings.twilio_auth_token
        self._from_number = settings.twilio_from_number
        self._public_base_url = settings.public_base_url

    def place_call(self, contact: str, message: str, event_id: str) -> CallAttempt:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/Calls.json"
        twiml_query = urlencode({"message": message, "event_id": event_id})
        twiml_url = f"{self._public_base_url}/providers/twilio/twiml?{twiml_query}"

        response = httpx.post(
            url,
            auth=(self._account_sid, self._auth_token),
            data={
                "To": contact,
                "From": self._from_number,
                "Url": twiml_url,
                "Method": "GET",
            },
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
        return CallAttempt(
            contact=contact,
            provider=self.name,
            accepted=True,
            reference=str(payload.get("sid", "")),
            detail=f"Twilio accepted call request with status {payload.get('status', 'queued')}.",
        )
