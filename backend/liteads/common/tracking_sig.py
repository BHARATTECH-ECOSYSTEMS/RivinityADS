"""
HMAC signatures on tracking URLs and click tokens so events cannot be forged.
"""

import hashlib
import hmac
import time
from typing import Any
from fastapi import HTTPException
from liteads.common.config import get_settings


def sign_tracking(request_id: str, ad_id: str, slot_id: str) -> str:
    """Generate signed timestamp token for impression/click URLs."""
    ts = str(int(time.time()))
    msg = f"{request_id}|{ad_id}|{slot_id}|{ts}".encode()
    sig = hmac.new(get_settings().tracking.secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return f"{ts}.{sig}"


def verify_tracking(request_id: str, ad_id: str, slot_id: str, signed: str) -> None:
    """Verify signed timestamp token on impression/click URLs."""
    try:
        ts, sig = signed.split(".", 1)
        ts_int = int(ts)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Bad signature")
    ttl = get_settings().tracking.signature_ttl_seconds
    if abs(int(time.time()) - ts_int) > ttl:
        raise HTTPException(status_code=400, detail="Expired signature")
    msg = f"{request_id}|{ad_id}|{slot_id}|{ts}".encode()
    expected = hmac.new(get_settings().tracking.secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Bad signature")


def sign_click_token(
    request_id: str,
    campaign_id: int,
    creative_id: int,
    timestamp: int | None = None,
    secret: str | None = None,
) -> str:
    """
    Generate a durable HMAC-signed click attribution token.
    Used by landing pages and advertiser pixels/postbacks to attribute conversions.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    signing_secret = (secret or get_settings().tracking.secret).encode()
    payload = f"{request_id}:{campaign_id}:{creative_id}:{ts}".encode()
    sig = hmac.new(signing_secret, payload, hashlib.sha256).hexdigest()[:32]
    return f"{request_id}.{campaign_id}.{creative_id}.{ts}.{sig}"


def verify_click_token(
    token: str,
    max_age_seconds: int = 2592000,  # 30 days
    secret: str | None = None,
) -> dict[str, Any]:
    """
    Verify and unpack a durable click attribution token.
    Returns parsed dictionary with request_id, campaign_id, creative_id, click_time, and is_within_window.
    Raises HTTPException(400) if signature or format is invalid.
    """
    if not token or not isinstance(token, str):
        raise HTTPException(status_code=400, detail="Invalid click token format")

    parts = token.split(".")
    if len(parts) != 5:
        raise HTTPException(status_code=400, detail="Invalid click token format")

    req_id, camp_str, creat_str, ts_str, sig = parts
    try:
        camp_id = int(camp_str)
        creat_id = int(creat_str)
        ts_int = int(ts_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid click token data")

    signing_secret = (secret or get_settings().tracking.secret).encode()
    payload = f"{req_id}:{camp_id}:{creat_id}:{ts_int}".encode()
    expected = hmac.new(signing_secret, payload, hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid click token signature")

    now = int(time.time())
    age = now - ts_int
    is_within_window = 0 <= age <= max_age_seconds

    return {
        "request_id": req_id,
        "campaign_id": camp_id,
        "creative_id": creat_id,
        "click_time": ts_int,
        "age_seconds": age,
        "is_within_window": is_within_window,
    }


def sign_conversion_sig(
    campaign_id: int,
    order_id: str,
    value: float | str,
    currency: str,
    timestamp: int,
    secret: str | None = None,
) -> str:
    """Generate HMAC-SHA256 signature for conversion payload."""
    signing_secret = (secret or get_settings().tracking.secret).encode()
    val_str = f"{float(value):.4f}" if value is not None else "0.0000"
    msg = f"{campaign_id}|{order_id}|{val_str}|{currency.upper()}|{timestamp}".encode()
    return hmac.new(signing_secret, msg, hashlib.sha256).hexdigest()


def verify_conversion_sig(
    campaign_id: int,
    order_id: str,
    value: float | str,
    currency: str,
    timestamp: int,
    signature: str,
    secret: str | None = None,
    ttl_seconds: int = 86400,  # 24 hours timestamp drift allowed for S2S
) -> None:
    """Verify HMAC signature for conversion payload."""
    if not signature:
        raise HTTPException(status_code=400, detail="Missing conversion signature")
    now = int(time.time())
    if abs(now - timestamp) > ttl_seconds:
        raise HTTPException(status_code=400, detail="Expired conversion signature timestamp")
    expected = sign_conversion_sig(campaign_id, order_id, value, currency, timestamp, secret)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid conversion signature")

