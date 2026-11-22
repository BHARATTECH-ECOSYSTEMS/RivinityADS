"""
Privacy and PII Guardrails for Machine Learning (Phase 8).

Enforces data-minimisation invariants:
1. Rejects raw PII identifiers (raw IP, email, phone, user-agent, raw device IDs, raw GPS coordinates).
2. Sanitizes feature vectors to ensure privacy compliance before inference or training dataset ingestion.
3. Ensures minor age masking and coarse geographic bucketing only.
"""

from typing import Any, Iterable
import re

from liteads.common.logger import get_logger

logger = get_logger(__name__)

PROHIBITED_PII_FIELDS: frozenset[str] = frozenset({
    "raw_ip",
    "ip_address",
    "client_ip",
    "remote_ip",
    "email",
    "user_email",
    "contact_email",
    "phone",
    "phone_number",
    "mobile_number",
    "user_agent",
    "raw_user_agent",
    "cookie",
    "cookies",
    "raw_device_id",
    "imei",
    "advertising_id",
    "idfa",
    "gaid",
    "gps_latitude",
    "gps_longitude",
    "latitude",
    "longitude",
})

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IPV4_REGEX = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")


class PIIViolationError(ValueError):
    """Raised when raw PII or prohibited identifiers are detected in ML data."""

    def __init__(self, message: str, violations: list[str] | None = None):
        super().__init__(message)
        self.violations = violations or []


def validate_privacy_compliance(
    features: dict[str, Any] | Iterable[str],
    check_values: bool = False,
) -> tuple[bool, list[str]]:
    """
    Validate that feature names (and optionally values) do not contain raw PII identifiers.

    Args:
        features: Feature dictionary or iterable of feature names.
        check_values: Whether to also scan string values for apparent emails or raw IPs.

    Returns:
        (is_compliant: bool, violations: list[str])
    """
    violations: list[str] = []

    if isinstance(features, dict):
        keys = list(features.keys())
    else:
        keys = list(features)

    for key in keys:
        norm_key = str(key).strip().lower()
        if norm_key in PROHIBITED_PII_FIELDS:
            violations.append(f"prohibited_key:{key}")
        elif any(norm_key.startswith(p) or norm_key.endswith(p) for p in ("email", "phone", "user_agent", "device_id")):
            if norm_key not in {"user_device_os", "landing_page_type"}:
                violations.append(f"suspicious_pii_key:{key}")

    if check_values and isinstance(features, dict):
        for k, v in features.items():
            if isinstance(v, str):
                v_clean = v.strip()
                if EMAIL_REGEX.match(v_clean):
                    violations.append(f"email_value_detected:{k}")
                elif IPV4_REGEX.match(v_clean) and k not in {"app_version", "model_version"}:
                    violations.append(f"ip_address_value_detected:{k}")

    return len(violations) == 0, violations


def assert_no_pii(
    features: dict[str, Any] | Iterable[str],
    check_values: bool = False,
) -> None:
    """
    Assert that features are free from raw PII; raises PIIViolationError if violations are found.
    """
    is_compliant, violations = validate_privacy_compliance(features, check_values=check_values)
    if not is_compliant:
        msg = f"ML Privacy Policy Violation: Prohibited PII detected: {violations}"
        logger.error(msg, violations=violations)
        raise PIIViolationError(msg, violations=violations)


def sanitize_features(features: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize a feature dictionary:
    - Drops any prohibited PII keys
    - Enforces minor protection (age < 18 -> age_bucket='restricted', user_gender='unspecified')
    - Ensures only coarse geo fields are present
    """
    sanitized: dict[str, Any] = {}

    for k, v in features.items():
        norm_k = str(k).strip().lower()
        if norm_k in PROHIBITED_PII_FIELDS:
            continue
        sanitized[k] = v

    # Minor privacy protection
    age = sanitized.get("user_age")
    if age is not None and isinstance(age, (int, float)) and age < 18:
        sanitized["user_age_bucket"] = "restricted"
        sanitized["user_gender"] = "unspecified"
        sanitized.pop("user_age", None)

    # Disallow raw coordinate leakage in nested keys
    for geo_key in ("gps_latitude", "gps_longitude", "latitude", "longitude"):
        sanitized.pop(geo_key, None)

    return sanitized
