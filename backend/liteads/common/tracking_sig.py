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
    advertiser_id: int | None = None,
    click_id: str | None = None,
) -> str:
    """Generate HMAC-SHA256 signature for conversion payload.

    Binds the signature to (advertiser_id, campaign_id, click_id, order_id, value, currency, timestamp)
    when advertiser_id or click_id is provided, or legacy 5-tuple for backward compatibility.
    """
    signing_secret = (secret or get_settings().tracking.secret).encode()
    try:
        val_str = f"{float(value):.4f}" if value is not None else "0.0000"
    except (ValueError, TypeError):
        val_str = "0.0000"

    if advertiser_id is not None or click_id is not None:
        msg = f"{advertiser_id or 0}|{campaign_id}|{click_id or ''}|{order_id}|{val_str}|{currency.upper()}|{timestamp}".encode()
    else:
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
    secrets: list[str] | None = None,
    ttl_seconds: int = 86400,  # 24 hours timestamp drift allowed for S2S
    advertiser_id: int | None = None,
    click_id: str | None = None,
) -> None:
    """Verify HMAC signature for conversion payload against allowed secrets and window."""
    if not signature or not isinstance(signature, str) or not signature.strip():
        raise HTTPException(status_code=400, detail="Missing conversion signature")

    try:
        ts_int = int(timestamp)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid timestamp format")

    now = int(time.time())
    if abs(now - ts_int) > ttl_seconds:
        raise HTTPException(status_code=400, detail="Expired conversion signature timestamp")

    # Assemble candidate secrets
    candidate_secrets: list[str] = []
    if secrets:
        candidate_secrets.extend(secrets)
    if secret and secret not in candidate_secrets:
        candidate_secrets.append(secret)
    if not candidate_secrets:
        candidate_secrets.append(get_settings().tracking.secret)

    clean_sig = signature.strip()
    is_valid = False
    for s in candidate_secrets:
        # Check 7-field binding first if advertiser_id or click_id is present
        if advertiser_id is not None or click_id is not None:
            expected_7 = sign_conversion_sig(
                campaign_id=campaign_id,
                order_id=order_id,
                value=value,
                currency=currency,
                timestamp=ts_int,
                secret=s,
                advertiser_id=advertiser_id,
                click_id=click_id,
            )
            if hmac.compare_digest(expected_7, clean_sig):
                is_valid = True
                break

        # Check legacy 5-field binding
        expected_5 = sign_conversion_sig(
            campaign_id=campaign_id,
            order_id=order_id,
            value=value,
            currency=currency,
            timestamp=ts_int,
            secret=s,
        )
        if hmac.compare_digest(expected_5, clean_sig):
            is_valid = True
            break

    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid conversion signature")


def sign_consent_token(
    site_slug: str,
    project_id: int,
    is_granted: bool,
    version: str = "v1.0",
    ttl_seconds: int = 604800,  # 7 days
    secret: str | None = None,
) -> str:
    """Generate a tamper-evident HMAC-signed site-scoped visitor consent proof."""
    ts = int(time.time())
    exp = ts + ttl_seconds
    granted_flag = "1" if is_granted else "0"
    signing_secret = (secret or get_settings().tracking.secret).encode()
    payload = f"{site_slug}:{project_id}:{granted_flag}:{version}:{ts}:{exp}".encode()
    sig = hmac.new(signing_secret, payload, hashlib.sha256).hexdigest()[:32]
    return f"{site_slug}.{project_id}.{granted_flag}.{version}.{ts}.{exp}.{sig}"


def verify_consent_token(
    token: str,
    expected_site_slug: str | None = None,
    expected_project_id: int | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Verify and unpack a site-scoped visitor consent proof.
    
    Raises HTTPException(400 or 403) if invalid, expired, or tampered.
    """
    if not token or not isinstance(token, str):
        raise HTTPException(status_code=400, detail="Invalid consent token format")

    parts = token.split(".")
    if len(parts) < 7:
        raise HTTPException(status_code=400, detail="Invalid consent token format")

    site_slug = parts[0]
    proj_str = parts[1]
    granted_flag = parts[2]
    version = ".".join(parts[3:-3])
    ts_str = parts[-3]
    exp_str = parts[-2]
    sig = parts[-1]
    try:
        proj_id = int(proj_str)
        ts = int(ts_str)
        exp = int(exp_str)
        is_granted = granted_flag == "1"
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid consent token data")

    signing_secret = (secret or get_settings().tracking.secret).encode()
    payload = f"{site_slug}:{proj_id}:{granted_flag}:{version}:{ts}:{exp}".encode()
    expected_sig = hmac.new(signing_secret, payload, hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected_sig, sig):
        raise HTTPException(status_code=403, detail="Invalid consent token signature: token tampered")

    now = int(time.time())
    if now > exp:
        raise HTTPException(status_code=400, detail="Consent token expired")

    # Anti-tampering check: cross-site or cross-project verification
    if expected_site_slug and site_slug != expected_site_slug:
        raise HTTPException(
            status_code=403,
            detail=f"Consent token site mismatch: token for '{site_slug}', expected '{expected_site_slug}'",
        )
    if expected_project_id and proj_id != expected_project_id:
        raise HTTPException(
            status_code=403,
            detail=f"Consent token project mismatch: token for project {proj_id}, expected {expected_project_id}",
        )

    return {
        "site_slug": site_slug,
        "project_id": proj_id,
        "is_granted": is_granted,
        "version": version,
        "timestamp": ts,
        "expires_at": exp,
    }


