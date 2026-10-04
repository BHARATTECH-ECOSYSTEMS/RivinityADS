"""
HMAC signatures on tracking URLs so events can't be forged.
"""

import hashlib
import hmac
import time
from fastapi import HTTPException
from liteads.common.config import get_settings

def sign_tracking(request_id: str, ad_id: str, slot_id: str) -> str:
    ts = str(int(time.time()))
    msg = f"{request_id}|{ad_id}|{slot_id}|{ts}".encode()
    sig = hmac.new(get_settings().tracking.secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return f"{ts}.{sig}"

def verify_tracking(request_id: str, ad_id: str, slot_id: str, signed: str) -> None:
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
