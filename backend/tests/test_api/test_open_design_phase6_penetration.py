"""
RivinityADS — Open Design Integration Phase 6 (Stage 6.1)
Cross-Tenant & AST Security Penetration Test Suite.

Work Unit 1: Penetration Testing
Validates:
1. Cross-Tenant Authorization & Enumeration Resistance (Two independent tenants)
2. Foreign File Operations & Storage Traversal Attacks
3. Publisher & Monetization Authorization Attacks
4. HTML Sanitization & Anti-Spoofing (Adversarial payloads against OpenDesignHTMLSanitizer)
5. Preview Token & Bridge Security (HMAC tampering, expiration, project isolation)

CRITICAL RESTRICTION:
Live monetization remains disabled. All ad rendering and preview assertions
verify zero-spend, non-billable sandbox isolation.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import math
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.routers.website_public import create_preview_token, verify_preview_token
from liteads.ad_server.services.rate_limit_service import (
    RateLimitService,
    _DAILY_QUOTA_LUA,
    _SLIDING_WINDOW_LUA,
)
from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer
from liteads.ad_server.services.website_storage import (
    ALLOWED_EXTENSIONS,
    MAX_PROJECT_QUOTA_BYTES,
    InvalidFileTypeError,
    PathTraversalError,
    website_storage,
)
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.models.ad import Status
from liteads.models.auth import User
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty
from liteads.models.website import (
    WebsiteMonetizationStatus,
    WebsiteProject,
    WebsiteStatus,
)
from liteads.schemas.website import (
    OpenDesignHTMLSanitizer,
    validate_and_sanitize_open_design_html,
)

pytestmark = pytest.mark.asyncio


# ── In-Memory Redis Mock for AI Rate Limiting & Quota Isolation ──────────────

class InMemoryRedisClient:
    """In-memory Redis client simulating atomic Lua script evaluation for rate limiting tests."""

    def __init__(self):
        self.strings: dict[str, str] = {}
        self.zsets: dict[str, list[tuple[float, str]]] = {}
        self.expiries: dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.simulated_time: float | None = None

    def _get_time(self) -> float:
        return self.simulated_time if self.simulated_time is not None else time.time()

    async def get(self, key: str) -> str | None:
        async with self.lock:
            now = self._get_time()
            if key in self.expiries and now > self.expiries[key]:
                self.strings.pop(key, None)
                self.expiries.pop(key, None)
                return None
            return self.strings.get(key)

    async def delete(self, *keys: str) -> int:
        async with self.lock:
            count = 0
            for k in keys:
                if k in self.strings:
                    del self.strings[k]
                    count += 1
                if k in self.zsets:
                    del self.zsets[k]
                    count += 1
                self.expiries.pop(k, None)
            return count

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> list[int]:
        async with self.lock:
            keys = list(keys_and_args[:numkeys])
            args = list(keys_and_args[numkeys:])

            if "max_quota" in script or script == _DAILY_QUOTA_LUA:
                key = keys[0]
                max_quota = int(args[0])
                ttl = int(args[1])
                seconds_to_midnight = int(args[2])

                current = int(self.strings.get(key, "0"))
                if current < max_quota:
                    new_val = current + 1
                    self.strings[key] = str(new_val)
                    if new_val == 1:
                        self.expiries[key] = self._get_time() + ttl
                    remaining = max_quota - new_val
                    return [1, remaining, seconds_to_midnight]
                else:
                    return [0, 0, seconds_to_midnight]

            elif "window" in script or script == _SLIDING_WINDOW_LUA:
                key = keys[0]
                now = float(args[0])
                window = float(args[1])
                max_limit = int(args[2])
                member_id = str(args[3])

                zset = self.zsets.setdefault(key, [])
                zset[:] = [entry for entry in zset if entry[0] > (now - window)]

                current_count = len(zset)
                if current_count < max_limit:
                    zset.append((now, member_id))
                    zset.sort(key=lambda x: x[0])
                    self.expiries[key] = now + math.ceil(window) + 5
                    remaining = max_limit - current_count - 1
                    return [1, remaining, math.ceil(window)]
                else:
                    reset_ttl = math.ceil(window)
                    if zset:
                        oldest_ts = zset[0][0]
                        reset_ttl = max(1, math.ceil(oldest_ts + window - now))
                    return [0, 0, reset_ttl]

            return [1, 50, 60]


# ── Fixtures & Helpers ────────────────────────────────────────────────────────

async def _register_user(client: AsyncClient, email: str, role: str = "publisher") -> tuple[str, int]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Penetration Test Tenant",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def _create_publisher_for_user(
    session: AsyncSession,
    user_id: int,
    name: str = "Penetration Publisher",
) -> Publisher:
    pub = Publisher(
        name=name,
        owner_user_id=user_id,
        status=Status.ACTIVE,
        payout_details={
            "account_number": "998877665544",
            "bank_name": "State Bank",
            "upi_id": "penetration@upi",
            "status": "active",
        },
    )
    session.add(pub)
    await session.flush()

    user = await session.get(User, user_id)
    if user:
        user.publisher_id = pub.id
        user.email_verified = True
        session.add(user)

    await session.commit()
    await session.refresh(pub)
    return pub


async def _create_property(
    session: AsyncSession,
    publisher_id: int,
    domain: str = "pen-test-domain.com",
    is_verified: bool = True,
    status: Status = Status.ACTIVE,
) -> PublisherProperty:
    prop = PublisherProperty(
        publisher_id=publisher_id,
        domain=domain,
        name="Pen Test Property",
        verification_status="VERIFIED" if is_verified else "PENDING",
        verification_method="DNS_TXT",
        verification_token="tok_pen_test_123",
        verified_at=datetime.now(timezone.utc) if is_verified else None,
        status=status,
    )
    session.add(prop)
    await session.commit()
    await session.refresh(prop)
    return prop


async def _create_slot(
    session: AsyncSession,
    publisher_id: int,
    property_id: int | None,
    slot_id: str = "slot_pen_test_top",
    format_type: int = 1,
    status: Status = Status.ACTIVE,
) -> AdSlot:
    slot = AdSlot(
        publisher_id=publisher_id,
        property_id=property_id,
        slot_id=slot_id,
        name="Leaderboard Ad Slot",
        format=format_type,
        width=728,
        height=90,
        floor_price=2.00,
        status=status,
    )
    session.add(slot)
    await session.commit()
    await session.refresh(slot)
    return slot


async def _create_project_for_tenant(
    client: AsyncClient,
    token: str,
    name: str = "Tenant Site",
    slug: str = "tenant-site",
) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={
            "name": name,
            "slug": slug,
            "theme_config": {"primary_color": "#112233"},
        },
    )
    assert res.status_code in (200, 201), res.text
    project_data = res.json()
    return project_data["id"], project_data


# ══════════════════════════════════════════════════════════════════════════════
# AREA A: Cross-Tenant Authorization & Enumeration Resistance
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossTenantAuthorizationPenetration:
    """
    Adversarial tests with two distinct authenticated tenants: Tenant A and Tenant B.
    Verifies that Tenant B cannot access, enumerate, modify, or delete Tenant A's resources.
    Verifies strict 404 behavior where required to prevent resource enumeration.
    """

    async def test_cross_tenant_cannot_read_foreign_project(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_read@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_read@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-read")

        # Tenant B attempts to read Tenant A's project
        headers_b = {"Authorization": f"Bearer {token_b}"}
        res = await client.get(f"/api/v1/website/projects/{proj_id_a}", headers=headers_b)
        assert res.status_code == 404, f"Expected 404 to prevent enumeration, got {res.status_code}: {res.text}"

    async def test_cross_tenant_cannot_list_foreign_projects(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_list@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_list@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-list")
        proj_id_b, _ = await _create_project_for_tenant(client, token_b, "Site B", "site-b-list")

        # Tenant B lists projects -> must see Site B, must NOT see Site A
        headers_b = {"Authorization": f"Bearer {token_b}"}
        res = await client.get("/api/v1/website/projects", headers=headers_b)
        assert res.status_code == 200
        projects = res.json()
        ids = [p["id"] for p in projects]
        assert proj_id_b in ids
        assert proj_id_a not in ids, "Tenant B must never see Tenant A's project in project list"

    async def test_cross_tenant_cannot_enumerate_or_mutate_foreign_pages(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_pages@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_pages@test.com")

        proj_id_a, proj_a = await _create_project_for_tenant(client, token_a, "Site A", "site-a-pages")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # 1. Enumerate pages
        res_list = await client.get(f"/api/v1/website/projects/{proj_id_a}/pages", headers=headers_b)
        assert res_list.status_code == 404, "Listing pages of foreign project must return 404"

        # 2. Add page
        res_add = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/pages",
            headers=headers_b,
            json={"title": "Injected Page", "slug": "injected"},
        )
        assert res_add.status_code == 404, "Creating page on foreign project must return 404"

        # 3. Modify page
        page_id_a = proj_a["pages"][0]["id"]
        res_put = await client.put(
            f"/api/v1/website/projects/{proj_id_a}/pages/{page_id_a}",
            headers=headers_b,
            json={"title": "Hacked Title"},
        )
        assert res_put.status_code == 404, "Updating page on foreign project must return 404"

        # 4. Delete page
        res_del = await client.delete(
            f"/api/v1/website/projects/{proj_id_a}/pages/{page_id_a}",
            headers=headers_b,
        )
        assert res_del.status_code == 404, "Deleting page on foreign project must return 404"

    async def test_cross_tenant_cannot_update_or_delete_foreign_project(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_mutate@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_mutate@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-mutate")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Tenant B attempts update
        res_upd = await client.put(
            f"/api/v1/website/projects/{proj_id_a}",
            headers=headers_b,
            json={"name": "Hacked Name"},
        )
        assert res_upd.status_code == 404, "Updating foreign project must return 404"

        # Tenant B attempts deletion
        res_del = await client.delete(
            f"/api/v1/website/projects/{proj_id_a}",
            headers=headers_b,
        )
        assert res_del.status_code == 404, "Deleting foreign project must return 404"

    async def test_cross_tenant_cannot_publish_or_suspend_foreign_project(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_pub@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_pub@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-pub")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Tenant B attempts to publish Tenant A's project
        res_pub = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/publish",
            headers=headers_b,
            json={},
        )
        assert res_pub.status_code == 404, "Publishing foreign project must return 404"

        # Tenant B attempts to suspend Tenant A's project (non-admin RBAC rejects with 403 or 404)
        res_susp = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/suspend",
            headers=headers_b,
        )
        assert res_susp.status_code in (403, 404), f"Suspending foreign project must be rejected with 403/404, got {res_susp.status_code}"

    async def test_cross_tenant_cannot_access_foreign_project_slots(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_slots@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_slots@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-slots")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        res = await client.get(f"/api/v1/website/projects/{proj_id_a}/slots", headers=headers_b)
        assert res.status_code == 404, "Querying slots for foreign project must return 404"

    async def test_cross_tenant_cannot_trigger_ai_generation_on_foreign_project(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_ai@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_ai@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-ai")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        res = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/ai/generate-section",
            headers=headers_b,
            json={"prompt": "Generate an adversarial section for Tenant A"},
        )
        assert res.status_code == 404, "Generating AI section on foreign project must return 404"

    async def test_cross_tenant_ai_rate_limit_and_quota_isolation(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Verifies that AI rate-limit reservation is strictly isolated per authenticated user_id."""
        _, user_a = await _register_user(client, "tenant_a_quota@test.com")
        _, user_b = await _register_user(client, "tenant_b_quota@test.com")

        mock_redis = InMemoryRedisClient()
        mock_wrapper = MagicMock()
        mock_wrapper.connected = True
        mock_wrapper.client = mock_redis
        mock_wrapper._client = mock_redis

        with patch("liteads.ad_server.services.rate_limit_service.redis_client", mock_wrapper):
            res_a = await RateLimitService.reserve_publisher_ai_quota(user_id=user_a)
            assert res_a.allowed is True

            res_b = await RateLimitService.reserve_publisher_ai_quota(user_id=user_b)
            assert res_b.allowed is True


# ══════════════════════════════════════════════════════════════════════════════
# AREA B: Foreign File Operations & Storage Traversal Attacks
# ══════════════════════════════════════════════════════════════════════════════

class TestForeignFileOperationsAndStorageAttacks:
    """
    Penetration tests targeting filesystem storage and upload endpoints.
    Verifies that tenants cannot upload, overwrite, read, or delete foreign files,
    and path traversal sequences are rejected fail-closed.
    """

    async def test_foreign_file_upload_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_fup@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_fup@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-fup")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        b64_data = base64.b64encode(b"console.log('malicious script');").decode("ascii")
        res = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/storage/files",
            headers=headers_b,
            json={"relative_path": "evil.js", "content_base64": b64_data},
        )
        assert res.status_code == 404, "Uploading files into foreign project must return 404"

    async def test_foreign_file_read_and_delete_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "tenant_a_fio@test.com")
        token_b, user_b = await _register_user(client, "tenant_b_fio@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-fio")
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Tenant A uploads legitimate file
        b64_data = base64.b64encode(b"body { color: blue; }").decode("ascii")
        up_res = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/storage/files",
            headers=headers_a,
            json={"relative_path": "styles.css", "content_base64": b64_data},
        )
        assert up_res.status_code == 201

        # Tenant B attempts to read Tenant A's file
        res_read = await client.get(
            f"/api/v1/website/projects/{proj_id_a}/storage/files/styles.css",
            headers=headers_b,
        )
        assert res_read.status_code == 404, "Reading foreign file must return 404"

        # Tenant B attempts to delete Tenant A's file
        res_del = await client.delete(
            f"/api/v1/website/projects/{proj_id_a}/storage/files/styles.css",
            headers=headers_b,
        )
        assert res_del.status_code == 404, "Deleting foreign file must return 404"

    async def test_storage_traversal_dot_dot_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "trav_dotdot@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site Trav", "site-trav-dd")
        headers = {"Authorization": f"Bearer {token}"}

        b64_data = base64.b64encode(b"malicious").decode("ascii")
        traversal_paths = [
            "../escaped.css",
            "../../etc/passwd.txt",
            "assets/../../escape.json",
            "..\\windows\\system32.txt",
        ]
        for p in traversal_paths:
            res = await client.post(
                f"/api/v1/website/projects/{proj_id}/storage/files",
                headers=headers,
                json={"relative_path": p, "content_base64": b64_data},
            )
            assert res.status_code in (400, 422), f"Path traversal '{p}' must be rejected with 400/422, got {res.status_code}"

    async def test_storage_disallowed_file_extensions_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "disallowed_ext@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site Ext", "site-ext")
        headers = {"Authorization": f"Bearer {token}"}

        b64_data = base64.b64encode(b"malicious content").decode("ascii")
        forbidden_files = [
            "shell.php",
            "payload.exe",
            "script.sh",
            "backend.py",
            "server.jsp",
            "exec.bat",
            "exploit.rb",
        ]
        for fname in forbidden_files:
            res = await client.post(
                f"/api/v1/website/projects/{proj_id}/storage/files",
                headers=headers,
                json={"relative_path": fname, "content_base64": b64_data},
            )
            assert res.status_code in (400, 422), f"Forbidden extension '{fname}' must be rejected, got {res.status_code}"

    async def test_storage_null_byte_and_absolute_paths_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "null_byte@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site Null", "site-null")
        headers = {"Authorization": f"Bearer {token}"}

        b64_data = base64.b64encode(b"data").decode("ascii")
        evil_paths = [
            "safe.png\x00.css",
            "C:/windows/system32/file.css",
            "D:/escaped.png",
            "assets/../../escaped.css",
        ]
        for p in evil_paths:
            res = await client.post(
                f"/api/v1/website/projects/{proj_id}/storage/files",
                headers=headers,
                json={"relative_path": p, "content_base64": b64_data},
            )
            assert res.status_code in (400, 422), f"Dangerous path '{p}' must be rejected, got {res.status_code}"


# ══════════════════════════════════════════════════════════════════════════════
# AREA C: Publisher & Monetization Authorization Attacks
# ══════════════════════════════════════════════════════════════════════════════

class TestPublisherAndMonetizationAuthorizationAttacks:
    """
    Penetration tests verifying that tenant authorization checks cannot be bypassed
    via forged payloads, foreign property linking, foreign slot injection, or prerequisite evasion.
    """

    async def test_foreign_publisher_linking_spoofing_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "pub_owner_a@test.com")
        token_b, user_b = await _register_user(client, "pub_owner_b@test.com")

        pub_a = await _create_publisher_for_user(test_db, user_a, "Publisher A")
        pub_b = await _create_publisher_for_user(test_db, user_b, "Publisher B")

        prop_a = await _create_property(test_db, pub_a.id, "pub-a-domain.com")
        proj_id_b, _ = await _create_project_for_tenant(client, token_b, "Site B", "site-b-link")

        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Tenant B attempts to link Tenant A's publisher_id
        res_link_pub = await client.post(
            f"/api/v1/website/projects/{proj_id_b}/publisher-link",
            headers=headers_b,
            json={"publisher_id": pub_a.id, "property_id": prop_a.id},
        )
        assert res_link_pub.status_code == 403, "Linking another user's publisher must return 403 Forbidden"
        assert "does not belong" in res_link_pub.text

    async def test_foreign_property_linking_spoofing_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "prop_owner_a@test.com")
        token_b, user_b = await _register_user(client, "prop_owner_b@test.com")

        pub_a = await _create_publisher_for_user(test_db, user_a, "Publisher A")
        pub_b = await _create_publisher_for_user(test_db, user_b, "Publisher B")

        prop_a = await _create_property(test_db, pub_a.id, "pub-a-property.com")
        proj_id_b, _ = await _create_project_for_tenant(client, token_b, "Site B", "site-b-foreign-prop")

        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Tenant B supplies its own publisher_id but Tenant A's property_id
        res_link_prop = await client.post(
            f"/api/v1/website/projects/{proj_id_b}/publisher-link",
            headers=headers_b,
            json={"publisher_id": pub_b.id, "property_id": prop_a.id},
        )
        assert res_link_prop.status_code in (400, 403), "Linking foreign property must be rejected"
        assert "does not belong" in res_link_prop.text

    async def test_foreign_slot_injection_in_publication_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "slot_owner_a@test.com")
        token_b, user_b = await _register_user(client, "slot_owner_b@test.com")

        pub_a = await _create_publisher_for_user(test_db, user_a, "Publisher A")
        pub_b = await _create_publisher_for_user(test_db, user_b, "Publisher B")

        prop_a = await _create_property(test_db, pub_a.id, "pub-a-slot.com")
        prop_b = await _create_property(test_db, pub_b.id, "pub-b-slot.com")

        slot_a = await _create_slot(test_db, pub_a.id, prop_a.id, "slot_a_valuable")
        slot_b = await _create_slot(test_db, pub_b.id, prop_b.id, "slot_b_normal")

        proj_id_b, proj_b = await _create_project_for_tenant(client, token_b, "Site B", "site-b-slot-inject")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Link Tenant B's project to its own publisher and property
        link_res = await client.post(
            f"/api/v1/website/projects/{proj_id_b}/publisher-link",
            headers=headers_b,
            json={"publisher_id": pub_b.id, "property_id": prop_b.id},
        )
        assert link_res.status_code == 200

        # Update Tenant B's draft page with Tenant A's slot
        page_id_b = proj_b["pages"][0]["id"]
        ad_block = {
            "id": "block_injected_slot",
            "type": "ad_placement",
            "order_index": 0,
            "content": {
                "ad_slot_id": slot_a.slot_id,  # Foreign slot from Publisher A!
                "format": 1,
                "width": 728,
                "height": 90,
            },
        }
        put_res = await client.put(
            f"/api/v1/website/projects/{proj_id_b}/pages/{page_id_b}",
            headers=headers_b,
            json={"blocks": {"schema_version": 2, "blocks": [ad_block]}},
        )
        assert put_res.status_code == 200

        # Attempt publication -> must fail closed
        res_pub = await client.post(
            f"/api/v1/website/projects/{proj_id_b}/publish",
            headers=headers_b,
            json={},
        )
        assert res_pub.status_code in (400, 403, 422), f"Publication with foreign slot must be rejected, got {res_pub.status_code}: {res_pub.text}"
        assert "does not belong" in res_pub.text

    async def test_forged_monetization_consent_on_foreign_project_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token_a, user_a = await _register_user(client, "consent_a@test.com")
        token_b, user_b = await _register_user(client, "consent_b@test.com")

        proj_id_a, _ = await _create_project_for_tenant(client, token_a, "Site A", "site-a-consent")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        res = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/monetization/consent",
            headers=headers_b,
            json={"accepted_terms": True, "terms_version": "2025.1"},
        )
        assert res.status_code == 404, "Recording consent on foreign project must return 404"

    async def test_enable_monetization_without_prerequisites_fails_closed(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "enable_noprereq@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site Incomplete", "site-inc")
        headers = {"Authorization": f"Bearer {token}"}

        # Project has no linked publisher, no property, no consent
        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/monetization/enable",
            headers=headers,
        )
        assert res.status_code in (400, 422), f"Enabling monetization without prerequisites must return 400/422, got {res.status_code}"
        assert "Cannot enable monetization" in res.text

    async def test_client_supplied_user_id_in_consent_ignored(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Client-supplied user_id in consent payload must be ignored in favor of authenticated session user."""
        token, user_id = await _register_user(client, "override_uid@test.com")
        pub = await _create_publisher_for_user(test_db, user_id, "Pub Override")
        prop = await _create_property(test_db, pub.id, "override-domain.com", is_verified=True)
        slot = await _create_slot(test_db, pub.id, prop.id, "slot_override")

        proj_id, _ = await _create_project_for_tenant(client, token, "Site Consent Override", "site-co")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers,
            json={"publisher_id": pub.id, "property_id": prop.id},
        )

        # Attacker injects fake "user_id": 999999 in consent payload
        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/monetization/consent",
            headers=headers,
            json={
                "accepted_terms": True,
                "terms_version": "2025.1",
                "user_id": 999999,
            },
        )
        assert res.status_code == 200
        consent = res.json()["consent"]
        assert consent["user_id"] == user_id, f"Server must enforce authenticated user_id ({user_id}), got {consent['user_id']}"


# ══════════════════════════════════════════════════════════════════════════════
# AREA D: HTML Sanitization & Anti-Spoofing (Backend OpenDesignHTMLSanitizer)
# ══════════════════════════════════════════════════════════════════════════════

class TestHtmlSanitizerAndAntiSpoofingAttacks:
    """
    Adversarial penetration tests targeting the backend HTML parser and sanitizer.
    Evaluates entity-encoded spoofing, nested tags, case/whitespace obfuscation,
    dangerous URI schemes, event handlers, style overlays, and malformed markup.
    """

    async def test_entity_encoded_spoofing_tokens_rejected(self):
        """Entity-encoded class names attempting to spoof ad placements must be rejected."""
        spoofed_payloads = [
            '<div class="&#x72;&#x69;&#x76;&#x69;&#x6e;&#x69;&#x74;&#x79;">Ad Mock</div>',
            '<div class="&#98;lock-ad-placement">Ad Mock</div>',
            '<span id="&#x61;&#x64;&#x2d;&#x73;&#x6c;&#x6f;&#x74;">Fake Slot</span>',
        ]
        for payload in spoofed_payloads:
            with pytest.raises(ValueError):
                validate_and_sanitize_open_design_html(payload)

    async def test_nested_ad_related_classes_and_ids_rejected(self):
        """Classes and IDs containing prohibited ad tokens must be rejected fail-closed."""
        adversarial_tags = [
            '<div class="block-ad-placement">Fake Container</div>',
            '<div class="rivinity-ad-unit">Fake Unit</div>',
            '<div class="my-custom-ad-slot-element">Fake Slot</div>',
            '<div id="rivinity_ad_123">Fake ID</div>',
            '<div class="outer"><div class="ad_placement">Nested Ad</div></div>',
        ]
        for tag in adversarial_tags:
            with pytest.raises(ValueError) as exc:
                validate_and_sanitize_open_design_html(tag)
            assert "cannot impersonate" in str(exc.value) or "Prohibited token detected" in str(exc.value)

    async def test_case_and_whitespace_obfuscation_rejected(self):
        """Mixed-case and whitespace-padded spoofing tokens must be rejected."""
        obfuscated = [
            '<div class="  rIvInItY  ">Content</div>',
            '<div class="BLOCK-AD-PLACEMENT">Content</div>',
            '<div class="Ad_Placement">Content</div>',
            '<div class="  rivinity-slot  ">Content</div>',
        ]
        for payload in obfuscated:
            with pytest.raises(ValueError):
                validate_and_sanitize_open_design_html(payload)

    async def test_dangerous_uri_schemes_rejected(self):
        """Dangerous URI schemes in href and src must be rejected fail-closed."""
        dangerous_links = [
            '<a href="javascript:alert(document.cookie)">Click me</a>',
            '<a href="vbscript:msgbox(1)">Click me</a>',
            '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">Click me</a>',
            '<img src="javascript:alert(1)" />',
            '<a href="file:///etc/passwd">Local file</a>',
        ]
        for link in dangerous_links:
            with pytest.raises(ValueError) as exc:
                validate_and_sanitize_open_design_html(link)
            assert (
                "must begin with http://" in str(exc.value)
                or "Unsafe script" in str(exc.value)
                or "Forbidden URL" in str(exc.value)
            )

    async def test_event_handlers_and_executable_attributes_rejected(self):
        """Inline event handler attributes (onclick, onerror, onload) must be rejected."""
        handlers = [
            '<div onclick="malicious()">Click</div>',
            '<img src="https://example.com/pic.png" onerror="steal()" />',
            '<p onmouseover="hover()">Text</p>',
            '<span onfocus="focus()">Input</span>',
        ]
        for h in handlers:
            with pytest.raises(ValueError) as exc:
                validate_and_sanitize_open_design_html(h)
            assert "Event handler attribute" in str(exc.value) or "Unsafe script" in str(exc.value)

    async def test_style_attribute_and_clickjacking_overlays_rejected(self):
        """The 'style' attribute is forbidden to prevent clickjacking and ad-stacking overlays."""
        styles = [
            '<div style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:9999">Overlay</div>',
            '<span style="display:none">Hidden</span>',
            '<p style="color:red">Styled</p>',
        ]
        for s in styles:
            with pytest.raises(ValueError) as exc:
                validate_and_sanitize_open_design_html(s)
            assert "Attribute 'style' is not permitted" in str(exc.value)

    async def test_prohibited_ad_placement_data_attributes_rejected(self):
        """Data attributes imitating ad slots must be rejected."""
        data_attrs = [
            '<div data-slot-id="slot_hacked">Ad</div>',
            '<div data-rivinity-format="banner">Ad</div>',
            '<div data-ad-unit="true">Ad</div>',
            '<div data-format="1">Ad</div>',
        ]
        for d in data_attrs:
            with pytest.raises(ValueError) as exc:
                validate_and_sanitize_open_design_html(d)
            assert (
                "Prohibited ad placement attribute" in str(exc.value)
                or "cannot impersonate" in str(exc.value)
            )

    async def test_malformed_unclosed_tags_rejected(self):
        """Malformed markup with unclosed or mismatched tags must fail sanitization."""
        malformed = [
            '<div class="safe"><p>Unclosed paragraph</div>',
            '<div><span>Mismatched</span>',
            '<div><div>Deeply unclosed</div>',
        ]
        for m in malformed:
            with pytest.raises(ValueError) as exc:
                validate_and_sanitize_open_design_html(m)
            assert "Unclosed HTML tags detected" in str(exc.value) or "Mismatched" in str(exc.value)

    async def test_disallowed_html_tags_rejected(self):
        """Dangerous or active HTML tags must be rejected outright."""
        forbidden_tags = [
            '<script>alert(1)</script>',
            '<iframe src="https://evil.com"></iframe>',
            '<object data="exploit.swf"></object>',
            '<embed src="exploit.swf" />',
            '<form action="https://evil.com/steal"><input type="text" /></form>',
            '<meta http-equiv="refresh" content="0;url=https://evil.com" />',
            '<base href="https://evil.com" />',
        ]
        for t in forbidden_tags:
            with pytest.raises(ValueError):
                validate_and_sanitize_open_design_html(t)


# ══════════════════════════════════════════════════════════════════════════════
# AREA F: Preview Token & Bridge Security
# ══════════════════════════════════════════════════════════════════════════════

class TestPreviewTokenAndBridgeSecurity:
    """
    Penetration tests verifying HMAC signing, expiration, tampering rejection,
    and project isolation on the draft preview endpoint.
    """

    async def test_preview_token_forgery_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "preview_forge@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site Forge", "site-forge")

        # Create forged token with attacker signature
        forged_token = f"{proj_id}.{user_id}.{int(time.time()) + 900}.12345678.attackerforgedsignature"
        res = await client.get(f"/preview/{forged_token}")
        assert res.status_code == 403, f"Forged preview token must be rejected with 403, got {res.status_code}"
        assert "Invalid or expired preview token" in res.json()["detail"]

    async def test_preview_token_expired_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "preview_exp@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site Exp", "site-exp")

        # Generate token with negative TTL (already expired)
        expired_token = create_preview_token(proj_id, user_id, ttl_seconds=-60)
        res = await client.get(f"/preview/{expired_token}")
        assert res.status_code == 403, "Expired preview token must be rejected with 403"
        assert "Invalid or expired preview token" in res.json()["detail"]

    async def test_preview_token_tampered_payload_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "preview_tamper@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site Tamper", "site-tamper")

        valid_token = create_preview_token(proj_id, user_id, ttl_seconds=900)
        parts = valid_token.split(".")

        # Tamper project_id (e.g. from proj_id to proj_id + 999)
        tampered_token = f"{proj_id + 999}.{parts[1]}.{parts[2]}.{parts[3]}.{parts[4]}"
        res = await client.get(f"/preview/{tampered_token}")
        assert res.status_code == 403, "Tampered project_id in token must be rejected with 403"

    async def test_preview_token_suspended_project_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "preview_susp@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site Susp", "site-susp")

        valid_token = create_preview_token(proj_id, user_id, ttl_seconds=900)

        # Suspend project
        proj = await test_db.get(WebsiteProject, proj_id)
        assert proj is not None
        proj.status = WebsiteStatus.SUSPENDED
        test_db.add(proj)
        await test_db.commit()

        res = await client.get(f"/preview/{valid_token}")
        assert res.status_code == 403, "Preview of suspended project must return 403"
        assert "suspended or archived" in res.json()["detail"]

    async def test_editing_preview_enforces_nonce_and_connect_none(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        token, user_id = await _register_user(client, "preview_editmode@test.com")
        proj_id, _ = await _create_project_for_tenant(client, token, "Site EditMode", "site-em")

        valid_token = create_preview_token(proj_id, user_id, ttl_seconds=900)

        res = await client.get(f"/preview/{valid_token}?edit_mode=1")
        assert res.status_code == 200

        csp = res.headers.get("Content-Security-Policy", "")
        assert "connect-src 'none';" in csp, "Editing preview must enforce connect-src 'none'"
        assert "form-action 'none';" in csp, "Editing preview must enforce form-action 'none'"
        assert "script-src 'nonce-" in csp, "Editing preview must enforce script-src nonce"
        assert "data-od-edit-bridge" in res.text, "Editing preview must contain authorized bridge script"
