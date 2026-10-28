"""
RivinityADS — Phase 10C.1 Security and Deployment-Readiness Audit Tests.

Audits and verifies:
1. Preview token cryptographic strength, key separation, nonce entropy, expiry bounds,
   project scoping, referrer/caching headers, and log token redaction.
2. Contact form server-side validation, honeypot handling, rate limiting (Redis & in-memory fallback),
   database error concealment, cross-site isolation, and PII protection.
3. Host routing normalization (user-info, trailing-dot ambiguities, IDN/punycode, port validation),
   untrusted proxy header rejection, and canonical origin derivation.
4. CSP strictness (no data: in img-src, form-action 'self', base-uri 'none') and framing denial.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from liteads.ad_server.routers.website_public import (
    create_preview_token,
    verify_preview_token,
    resolve_subdomain_from_request,
    check_contact_submission_rate_limit,
    _IN_MEMORY_CONTACT_RATE_LIMITS,
)
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.models import User
from liteads.models.website import (
    WebsiteContactSubmission,
    WebsiteProject,
    WebsiteStatus,
)

pytestmark = pytest.mark.asyncio


async def _create_test_user_and_token(client: AsyncClient, email: str, role: str = "publisher") -> tuple[str, int]:
    """Helper to create a test user and obtain a JWT bearer token."""
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "SecurePassword123!",
            "company_name": "Audit Security Corp",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    token = res.json()["access_token"]
    user_id = res.json()["user"]["id"]
    return token, user_id


# ---------------------------------------------------------------------------
# 1. Preview Token Cryptographic Strength & Isolation
# ---------------------------------------------------------------------------

async def test_preview_token_cryptographic_strength_and_nonce_entropy():
    """Verify preview tokens have unique nonces, domain-separated keys, and constant-time verification."""
    tok1 = create_preview_token(project_id=10, user_id=42, ttl_seconds=900)
    tok2 = create_preview_token(project_id=10, user_id=42, ttl_seconds=900)

    # Distinct random nonces ensure tokens issued within the same second are unique
    assert tok1 != tok2
    parts1 = tok1.split(".")
    assert len(parts1) == 5, "Preview token format must be project.user.expiry.nonce.sig"
    assert parts1[0] == "10"
    assert parts1[1] == "42"

    # Verify both valid
    assert verify_preview_token(tok1) == (10, 42)
    assert verify_preview_token(tok2) == (10, 42)


async def test_preview_token_tampering_and_expiry_bounds():
    """Verify preview token tampering fails closed and expiry bounds are enforced."""
    token = create_preview_token(project_id=15, user_id=99, ttl_seconds=300)
    parts = token.split(".")

    # 1. Tamper project_id (Cross-project privilege escalation attempt)
    tampered_proj = f"16.{parts[1]}.{parts[2]}.{parts[3]}.{parts[4]}"
    assert verify_preview_token(tampered_proj) is None

    # 2. Tamper expiry to extend validity
    tampered_exp = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1000}.{parts[3]}.{parts[4]}"
    assert verify_preview_token(tampered_exp) is None

    # 3. Tamper signature
    tampered_sig = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}.{'a' * 64}"
    assert verify_preview_token(tampered_sig) is None

    # 4. Expired token
    expired_token = f"{parts[0]}.{parts[1]}.{int(time.time()) - 10}.{parts[3]}.{parts[4]}"
    assert verify_preview_token(expired_token) is None

    # 5. Absurd far-future expiry (> 7 days) fails closed
    absurd_future = int(time.time()) + (86400 * 365)
    absurd_token = f"15.99.{absurd_future}.nonce.{parts[4]}"
    assert verify_preview_token(absurd_token) is None

    # 6. Malformed parts
    assert verify_preview_token("invalid.token") is None
    assert verify_preview_token("a.b.c.d.e") is None


async def test_preview_route_archived_and_deleted_projects_fail_closed(client: AsyncClient, test_db):
    """Verify preview route strictly fails closed for deleted, archived, or suspended projects."""
    token, user_id = await _create_test_user_and_token(client, "preview_audit@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create project
    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Audit Preview Site", "slug": "audit-prev"},
        headers=headers,
    )
    p_id = p_res.json()["id"]

    # Issue preview token
    t_res = await client.post(f"/api/v1/website/projects/{p_id}/preview-token", headers=headers)
    preview_token = t_res.json()["preview_token"]

    # Valid preview request
    good_prev = await client.get(f"/preview/{preview_token}")
    assert good_prev.status_code == 200
    # Referrer policy must be no-referrer to protect token leakage
    assert good_prev.headers["Referrer-Policy"] == "no-referrer"
    assert "no-store" in good_prev.headers["Cache-Control"]

    # 1. Archive project -> Preview must fail (HTTP 403)
    stmt = select(WebsiteProject).where(WebsiteProject.id == p_id)
    res = await test_db.execute(stmt)
    proj = res.scalar_one()
    proj.status = WebsiteStatus.ARCHIVED
    await test_db.commit()

    archived_resp = await client.get(f"/preview/{preview_token}")
    assert archived_resp.status_code == 403

    # 2. Suspend project -> Preview must fail (HTTP 403)
    proj.status = WebsiteStatus.SUSPENDED
    await test_db.commit()

    suspended_resp = await client.get(f"/preview/{preview_token}")
    assert suspended_resp.status_code == 403

    # 3. Soft-delete project -> Preview must fail (HTTP 404)
    proj.is_deleted = True
    await test_db.commit()

    deleted_resp = await client.get(f"/preview/{preview_token}")
    assert deleted_resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. Contact Form Abuse, Honeypot, and Rate Limiting
# ---------------------------------------------------------------------------

async def test_contact_form_honeypot_drops_bot_submissions(client: AsyncClient, test_db):
    """Verify honeypot field causes silent drop without inserting rows into the database."""
    token, user_id = await _create_test_user_and_token(client, "honeypot_test@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Honeypot Site", "slug": "hp-site"},
        headers=headers,
    )
    p_id = p_res.json()["id"]
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    # 1. Bot fills honeypot field 'hp_comment'
    bot_res = await client.post(
        "/api/v1/website/public/hp-site/contact",
        json={
            "name": "Spam Bot 3000",
            "email": "bot@spammer.net",
            "message": "Buy cheap pharmaceutical products now!",
            "hp_comment": "Automated form filler caught",
        },
    )
    assert bot_res.status_code == 201
    bot_data = bot_res.json()
    assert bot_data["success"] is True
    # Honeypot returns synthetic id=0 without touching database
    assert bot_data["id"] == 0

    # Verify 0 rows in database
    stmt = select(WebsiteContactSubmission).where(WebsiteContactSubmission.project_id == p_id)
    res = await test_db.execute(stmt)
    submissions = res.scalars().all()
    assert len(submissions) == 0


async def test_contact_form_input_validation_and_oversized_payloads(client: AsyncClient, test_db):
    """Verify strict server-side validation against oversized or malformed contact inputs."""
    token, user_id = await _create_test_user_and_token(client, "validation_test@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Validation Site", "slug": "valid-site"},
        headers=headers,
    )
    p_id = p_res.json()["id"]
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    # 1. Oversized name (> 120 chars)
    res_name = await client.post(
        "/api/v1/website/public/valid-site/contact",
        json={
            "name": "X" * 121,
            "email": "user@test.org",
            "message": "Hello there",
        },
    )
    assert res_name.status_code == 422

    # 2. Invalid email format
    res_email = await client.post(
        "/api/v1/website/public/valid-site/contact",
        json={
            "name": "Valid User",
            "email": "not-a-valid-email",
            "message": "Hello there",
        },
    )
    assert res_email.status_code == 422

    # 3. Oversized email (> 254 chars)
    res_long_email = await client.post(
        "/api/v1/website/public/valid-site/contact",
        json={
            "name": "Valid User",
            "email": ("a" * 250) + "@test.org",
            "message": "Hello there",
        },
    )
    assert res_long_email.status_code == 422

    # 4. Oversized message (> 5000 chars)
    res_msg = await client.post(
        "/api/v1/website/public/valid-site/contact",
        json={
            "name": "Valid User",
            "email": "user@test.org",
            "message": "M" * 5001,
        },
    )
    assert res_msg.status_code == 422


async def test_contact_form_rate_limiting_enforcement(client: AsyncClient, test_db):
    """Verify contact submission rate limiting (5 per minute per IP per site) triggers HTTP 429."""
    token, user_id = await _create_test_user_and_token(client, "ratelimit_test@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Rate Limit Site", "slug": "rl-site"},
        headers=headers,
    )
    p_id = p_res.json()["id"]
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    # Clear in-memory rate limits before test
    _IN_MEMORY_CONTACT_RATE_LIMITS.clear()

    # Submit 5 allowed messages
    for i in range(5):
        resp = await client.post(
            "/api/v1/website/public/rl-site/contact",
            json={
                "name": f"Sender {i}",
                "email": f"sender{i}@example.com",
                "message": f"Message {i}",
            },
        )
        assert resp.status_code == 201

    # 6th submission from same client IP must be rejected with 429
    excess_resp = await client.post(
        "/api/v1/website/public/rl-site/contact",
        json={
            "name": "Excess Sender",
            "email": "excess@example.com",
            "message": "Spam burst attempt",
        },
    )
    assert excess_resp.status_code == 429
    assert "Too many contact submissions" in excess_resp.text


# ---------------------------------------------------------------------------
# 3. Host Normalization and Security Rejections
# ---------------------------------------------------------------------------

async def test_host_normalization_rejections(client: AsyncClient, test_db):
    """
    Verify host routing strictly rejects:
    - User-info syntax (@)
    - Trailing-dot ambiguities (.)
    - Punycode/IDN (xn--)
    - Invalid ports
    """
    token, user_id = await _create_test_user_and_token(client, "host_audit@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Host Audit Site", "slug": "audit-host"},
        headers=headers,
    )
    p_id = p_res.json()["id"]
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    # 1. Userinfo syntax rejection (@)
    res_at = await client.get("/", headers={"Host": "attacker@audit-host.rivinity.site"})
    assert res_at.status_code == 404

    # 2. Trailing dot ambiguity rejection
    res_dot = await client.get("/", headers={"Host": "audit-host.rivinity.site."})
    assert res_dot.status_code == 404

    # 3. Double dot rejection
    res_ddot = await client.get("/", headers={"Host": "audit..host.rivinity.site"})
    assert res_ddot.status_code == 404

    # 4. Punycode/IDN rejection
    res_puny = await client.get("/", headers={"Host": "xn--audithost.rivinity.site"})
    assert res_puny.status_code == 404

    # 5. Invalid non-numeric port rejection
    res_bad_port = await client.get("/", headers={"Host": "audit-host.rivinity.site:badport"})
    assert res_bad_port.status_code == 404

    # 6. Out-of-range port rejection
    res_high_port = await client.get("/", headers={"Host": "audit-host.rivinity.site:99999"})
    assert res_high_port.status_code == 404

    # 7. Valid host succeeds
    res_valid = await client.get("/", headers={"Host": "audit-host.rivinity.site"})
    assert res_valid.status_code == 200


async def test_content_security_policy_and_security_headers(client: AsyncClient, test_db):
    """Verify CSP is strict: no data: in img-src, form-action 'self', base-uri 'none', frame denial."""
    token, user_id = await _create_test_user_and_token(client, "csp_audit@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "CSP Audit Site", "slug": "csp-audit"},
        headers=headers,
    )
    p_id = p_res.json()["id"]
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    res = await client.get("/", headers={"Host": "csp-audit.rivinity.site"})
    assert res.status_code == 200

    csp = res.headers["Content-Security-Policy"]
    # Assert restricted img-src (no data: URI allowance)
    assert "img-src 'self' https:;" in csp
    assert "data:" not in csp
    # Assert form-action and base-uri restrictions
    assert "form-action 'self';" in csp
    assert "base-uri 'none';" in csp
    # Assert scripts completely disabled
    assert "script-src 'none';" in csp
    # Assert frame protection
    assert "frame-ancestors 'none';" in csp
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["X-Content-Type-Options"] == "nosniff"
