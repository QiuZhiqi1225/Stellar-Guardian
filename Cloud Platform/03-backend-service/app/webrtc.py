from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings, _normalize_ice_servers


logger = logging.getLogger(__name__)


def resolve_webrtc_config(settings: Settings) -> dict[str, Any]:
    ice_servers = settings.webrtc_ice_servers
    source = "static"
    warning = ""
    ttl_seconds: int | None = None

    if settings.twilio_turn_enabled:
        dynamic_ice_servers, warning, ttl_seconds = _load_twilio_ice_servers(settings)
        if dynamic_ice_servers:
            ice_servers = dynamic_ice_servers
            source = "twilio"
        else:
            source = "static_fallback"

    has_turn = any(
        str(url).startswith(("turn:", "turns:"))
        for item in ice_servers
        for url in item.get("urls", [])
    )
    return {
        "ice_servers": ice_servers,
        "has_turn": has_turn,
        "public_base_url": settings.public_base_url,
        "source": source,
        "warning": warning,
        "ttl_seconds": ttl_seconds,
    }


def _load_twilio_ice_servers(settings: Settings) -> tuple[list[dict[str, Any]], str, int | None]:
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.warning("TWILIO_TURN_ENABLED is true but Twilio credentials are incomplete.")
        return [], "twilio_credentials_incomplete", None

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Tokens.json"
    try:
        response = httpx.post(
            url,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data={"Ttl": settings.twilio_turn_ttl},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch Twilio TURN token: %s", exc)
        return [], "twilio_token_fetch_failed", None

    ice_servers = _normalize_ice_servers(payload.get("ice_servers"))
    if not ice_servers:
        logger.warning("Twilio TURN token response did not contain usable ice_servers.")
        return [], "twilio_token_missing_ice_servers", None

    return ice_servers, "", _coerce_ttl(payload.get("ttl"), settings.twilio_turn_ttl)


def _coerce_ttl(value: Any, fallback: int) -> int:
    try:
        ttl_value = int(value)
    except (TypeError, ValueError):
        ttl_value = fallback
    return max(60, ttl_value)
