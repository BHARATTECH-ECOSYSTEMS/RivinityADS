"""
Resolve client IP to country/state/city/pincode. Cached in Redis 7 days.
"""

import ipaddress
from typing import Any

try:
    import geoip2.database
except ImportError:
    geoip2 = None

from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.logger import get_logger

logger = get_logger(__name__)

_reader: Any = None


def _get_reader() -> Any:
    global _reader
    if geoip2 is None:
        return None
    if _reader is None:
        path = get_settings().geo.mmdb_path
        try:
            _reader = geoip2.database.Reader(path)
        except Exception as e:
            logger.error("GeoIP database missing", path=path, error=str(e))
            return None
    return _reader


def _is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


async def resolve_ip(ip: str | None) -> dict:
    """Returns {'country','state','city','pincode','lat','lon'} — all None on failure."""
    empty = {"country": None, "state": None, "city": None, "pincode": None, "lat": None, "lon": None}
    
    if not ip or not _is_public(ip):
        return empty

    key = f"geo:ip:{ip}"
    cached = await redis_client.hgetall(key)
    if cached:
        # Convert cached string values back to native types if necessary, though strings are mostly fine
        # except lat/lon which might be needed as floats, but the schema allows strings or floats.
        # It's safer to ensure they match our expected types.
        if "lat" in cached and cached["lat"] != "None":
            cached["lat"] = float(cached["lat"])
        else:
            cached["lat"] = None
            
        if "lon" in cached and cached["lon"] != "None":
            cached["lon"] = float(cached["lon"])
        else:
            cached["lon"] = None
            
        # Any string 'None' should be actually None
        for k, v in cached.items():
            if v == "None":
                cached[k] = None
                
        return cached

    reader = _get_reader()
    if reader is None:
        return empty

    try:
        r = reader.city(ip)
        out = {
            "country": r.country.iso_code,
            "state": (r.subdivisions.most_specific.iso_code or r.subdivisions.most_specific.name or "").lower() or None,
            "city": (r.city.name or "").lower() or None,
            "pincode": r.postal.code or None,
            "lat": r.location.latitude if r.location else None,
            "lon": r.location.longitude if r.location else None,
        }
    except Exception:
        out = empty

    # Save to cache if we resolved something or even empty to prevent hammering on missing?
    # Usually cache valid results. We'll cache all to prevent repeating MaxMind lookups.
    mapping = {k: str(v) for k, v in out.items() if v is not None}
    if mapping:
        await redis_client.hmset(key, mapping=mapping)
        await redis_client.expire(key, 7 * 24 * 3600)

    return out
