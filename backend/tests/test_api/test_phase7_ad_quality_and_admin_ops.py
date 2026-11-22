"""Phase 7 — Ad Quality, Destinations, Publisher Safety and Admin Operations Test Suite.

Validates:
1. Destination Validation & SSRF Hardening:
   - HTTPS policy enforcement
   - Loopback (127.0.0.1, localhost), private (10.0.0.1, 192.168.1.1, 172.16.0.1), and link-local / cloud metadata (169.254.169.254) blocking
   - Userinfo in URL rejection
   - Multi-hop redirect SSRF defense
   - Malicious & phishing pattern blocking
   - URL canonicalization
2. Creative Moderation Lifecycle:
   - Categorized rejection reasons with audit log
   - Resubmission workflow for rejected creatives (with tenant ownership checks)
   - Content-equivalence checks revoking approval on substantive modifications
   - Whitespace / non-substantive edits preserving approval
3. Publisher Safety & IVT Detection:
   - Click velocity detection (>15 clicks in 60s)
   - IP-sharing / NAT protection rule (single IP is never treated as conclusive fraud)
   - Clickjacking detection (negative / hidden state coordinates)
   - Repeated creative click tracking
   - Publisher safety summary without leaking proprietary detection thresholds
4. Audited Financial Earnings & Adjustments:
   - Detailed earnings breakdown across 6 states: estimated, under review, finalised, adjusted, payable, paid
   - Manual payment adjustment recording immutable audit log
   - Payout review lifecycle audit logging
5. Administrative Audit & Control:
   - Admin-only audit log query endpoint (normal users get 403)
   - Publisher, property, and campaign status changes record audit entries
6. Delivery Enforcement:
   - Immediate cessation of ad serving (JSON and iframe) when publisher is paused/suspended
   - Immediate cessation of ad serving when publisher property is paused/suspended or unverified
7. Dashboard & Operational Exception Queues:
   - SLA tracking for moderation (24h) and finance (48h) queues (ON_TRACK, AT_RISK, BREACHED)
   - Automated alerts for spend spikes, high CTRs, stale integrations, zero fill placements
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.destination_validator import (
    DestinationValidator,
    DestinationValidationError,
    normalize_destination_url,
    validate_hostname_ssrf,
)
from liteads.ad_server.services.creative_moderation import (
    CreativeModerationService,
    ModerationCategory,
    compute_creative_content_hash,
)
from liteads.ad_server.services.ivt_service import IVTService
from liteads.ad_server.services.billing import BillingService
from liteads.common.security import create_access_token
from liteads.models.ad import (
    Advertiser,
    AdSet,
    Campaign,
    Creative,
    Status,
)
from liteads.models.publisher import (
    AdSlot,
    Publisher,
    PublisherProperty,
)
from liteads.models.auth import AuditLog, User
from liteads.models.base import EventType, ModerationStatus
from liteads.models.finance import PublisherPayout, Transaction
from liteads.models.base import TransactionType

pytestmark = pytest.mark.asyncio


# ── Test Fixtures & Helpers ───────────────────────────────────────────────────

async def _create_test_admin(test_db: AsyncSession) -> tuple[User, str]:
    admin = User(
        name="Platform Admin",
        email=f"admin_{datetime.now().timestamp()}@example.com",
        password_hash="admin_pw_hash",
        role="admin",
        is_active=True,
        email_verified=True,
    )
    test_db.add(admin)
    await test_db.commit()
    await test_db.refresh(admin)
    return admin, create_access_token(admin)


async def _create_test_advertiser(test_db: AsyncSession, balance: Decimal = Decimal("1000.00")) -> tuple[User, Advertiser, str]:
    ts = int(datetime.now().timestamp() * 1000)
    adv = Advertiser(name=f"Adv_{ts}", contact_email=f"adv_{ts}@example.com", balance=balance)
    test_db.add(adv)
    await test_db.flush()

    user = User(
        name=f"User Adv_{ts}",
        email=f"user_adv_{ts}@example.com",
        password_hash="test_pw_hash",
        role="advertiser",
        advertiser_id=adv.id,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(adv)
    await test_db.refresh(user)
    return user, adv, create_access_token(user)


async def _create_test_publisher(test_db: AsyncSession, balance: Decimal = Decimal("500.00"), pending: Decimal = Decimal("100.00")) -> tuple[User, Publisher, str]:
    ts = int(datetime.now().timestamp() * 1000)
    pub = Publisher(
        name=f"Pub_{ts}",
        email=f"pub_{ts}@example.com",
        site_url="https://publisher-site.com",
        balance=balance,
        pending_balance=pending,
        status=Status.ACTIVE,
    )
    test_db.add(pub)
    await test_db.flush()

    user = User(
        name=f"User Pub_{ts}",
        email=f"user_pub_{ts}@example.com",
        password_hash="test_pw_hash",
        role="publisher",
        publisher_id=pub.id,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(pub)
    await test_db.refresh(user)
    return user, pub, create_access_token(user)


async def _create_campaign_with_creative(
    test_db: AsyncSession,
    advertiser_id: int,
    landing_url: str = "https://example.com/landing",
    mod_status: ModerationStatus = ModerationStatus.APPROVED,
) -> tuple[Campaign, Creative]:
    camp = Campaign(
        advertiser_id=advertiser_id,
        name="Phase 7 Test Campaign",
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        status=Status.ACTIVE,
    )
    test_db.add(camp)
    await test_db.flush()

    ad_set = AdSet(
        campaign_id=camp.id,
        name="Default Set",
        status=Status.ACTIVE,
        daily_budget=Decimal("100.00"),
    )
    test_db.add(ad_set)
    await test_db.flush()

    cr = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set.id,
        title="Best Developer Tools",
        description="Supercharge your workflow today",
        image_url="https://example.com/creative.jpg",
        landing_url=landing_url,
        moderation_status=mod_status,
        status=Status.ACTIVE if mod_status == ModerationStatus.APPROVED else Status.PENDING,
    )
    test_db.add(cr)
    await test_db.commit()
    await test_db.refresh(camp)
    await test_db.refresh(cr)
    return camp, cr


# ── 1. Destination Validation & SSRF Hardening ───────────────────────────────

class TestDestinationValidationAndSSRF:
    """Rigorous verification of URL normalization, HTTPS policy, and SSRF defenses."""

    async def test_https_policy_enforcement(self) -> None:
        """Destination must strictly use HTTPS scheme."""
        res = DestinationValidator.normalize_and_prevalidate("http://insecure-site.com/landing")
        assert not res.is_valid
        assert "HTTPS" in (res.reason or "")

        res_ftp = DestinationValidator.normalize_and_prevalidate("ftp://files.com/ad")
        assert not res_ftp.is_valid

        res_https = DestinationValidator.normalize_and_prevalidate("https://secure-site.com/landing")
        assert res_https.is_valid

    async def test_userinfo_credential_blocking(self) -> None:
        """URLs containing credentials (user:pass@) must be rejected to prevent deceptive routing."""
        res = DestinationValidator.normalize_and_prevalidate("https://admin:secret@malicious.com/landing")
        assert not res.is_valid
        assert "userinfo" in (res.reason or "").lower() or "credentials" in (res.reason or "").lower()

    async def test_ssrf_blocking_loopback_and_private_hostnames(self) -> None:
        """Hostname-level SSRF defenses must reject loopback, private RFC1918, and metadata endpoints."""
        for blocked in ["127.0.0.1", "localhost", "::1", "10.0.0.5", "172.16.50.1", "192.168.1.100", "169.254.169.254", "metadata.google.internal"]:
            with pytest.raises(DestinationValidationError):
                validate_hostname_ssrf(blocked)

        # Valid public domains pass without exception
        validate_hostname_ssrf("google.com")
        validate_hostname_ssrf("bharattech.org")

    async def test_url_normalization(self) -> None:
        """URL normalization must lower-case scheme/host, drop default 443 port, and strip fragment."""
        raw = "HTTPS://WWW.EXAMPLE.COM:443/landing/?utm_source=test#anchor"
        norm = normalize_destination_url(raw)
        assert norm.startswith("https://www.example.com/landing/?utm_source=test")
        assert ":443" not in norm
        assert "#anchor" not in norm

    async def test_malicious_regex_blocking(self) -> None:
        """Known phishing and malicious keyword combinations must be detected."""
        res = DestinationValidator.normalize_and_prevalidate("https://verify-bank-account-update.xyz/login")
        assert not res.is_valid
        assert "pattern" in (res.reason or "").lower() or "high-risk" in (res.reason or "").lower()

    async def test_redirect_chain_ssrf_protection(self) -> None:
        """If an initial public URL redirects to a private IP, the validator must catch and block it."""
        mock_response_1 = MagicMock(spec=httpx.Response)
        mock_response_1.status_code = 302
        mock_response_1.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

        with patch("liteads.ad_server.services.destination_validator.resolve_and_verify_safe_ips", new_callable=AsyncMock) as mock_dns:
            mock_dns.return_value = ["93.184.216.34"]
            with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
                mock_head.return_value = mock_response_1
                result = await DestinationValidator.check_destination("https://initial-safe-looking.com/jump", perform_fetch=True)
                assert not result.is_valid
                assert "SSRF" in (result.reason or "") or "HTTPS" in (result.reason or "") or "policy" in (result.reason or "").lower()

    async def test_circular_redirect_loop_detection(self) -> None:
        """Circular redirects between two URLs must be terminated safely."""
        r1 = MagicMock(spec=httpx.Response)
        r1.status_code = 302
        r1.headers = {"Location": "https://example.com/loop2"}

        r2 = MagicMock(spec=httpx.Response)
        r2.status_code = 302
        r2.headers = {"Location": "https://example.com/loop1"}

        with patch("liteads.ad_server.services.destination_validator.resolve_and_verify_safe_ips", new_callable=AsyncMock) as mock_dns:
            mock_dns.return_value = ["93.184.216.34"]
            with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
                mock_head.side_effect = [r1, r2, r1]
                result = await DestinationValidator.check_destination("https://example.com/loop1", perform_fetch=True)
                assert not result.is_valid
                assert "loop" in (result.reason or "").lower()


# ── 2. Creative Moderation Lifecycle ─────────────────────────────────────────

class TestCreativeModerationLifecycle:
    """Tests for categorized review, resubmission workflow, content equivalence, and auditability."""

    async def test_categorized_creative_rejection_records_audit(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        admin_user, admin_token = await _create_test_admin(test_db)
        _, adv, _ = await _create_test_advertiser(test_db)
        _, cr = await _create_campaign_with_creative(test_db, adv.id, mod_status=ModerationStatus.PENDING_REVIEW)

        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = await client.post(
            f"/api/v1/moderation/creatives/{cr.id}/reject",
            headers=headers,
            json={
                "category": ModerationCategory.POLICY_VIOLATION.value,
                "reason": "Misleading performance claims in ad copy",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["moderation_status"] == ModerationStatus.REJECTED.value
        assert "[POLICY_VIOLATION]" in data["moderation_reason"]
        assert data["status"] == Status.PENDING.value

        # Verify Audit Log
        audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "creative_rejected").order_by(AuditLog.id.desc())
        )
        audit = audit_res.scalars().first()
        assert audit is not None
        assert audit.user_id == admin_user.id
        details = json.loads(audit.details or "{}")
        assert details["creative_id"] == cr.id
        assert details["category"] == ModerationCategory.POLICY_VIOLATION.value

    async def test_creative_resubmission_lifecycle(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        """A rejected creative can be resubmitted by the owning advertiser."""
        adv_user, adv, adv_token = await _create_test_advertiser(test_db)
        _, other_adv, other_token = await _create_test_advertiser(test_db)
        _, cr = await _create_campaign_with_creative(test_db, adv.id, mod_status=ModerationStatus.REJECTED)

        # 1. Other advertiser attempts resubmit -> Forbidden (403)
        unauth_resp = await client.post(
            f"/api/v1/moderation/creatives/{cr.id}/resubmit",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert unauth_resp.status_code == 403

        # 2. Owning advertiser resubmits -> Success (200)
        owner_resp = await client.post(
            f"/api/v1/moderation/creatives/{cr.id}/resubmit",
            headers={"Authorization": f"Bearer {adv_token}"},
        )
        assert owner_resp.status_code == 200
        data = owner_resp.json()
        assert data["moderation_status"] == ModerationStatus.PENDING_REVIEW.value
        assert data["moderation_reason"] == "Resubmitted for review"

        # Verify Audit Log
        audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "creative_resubmitted").order_by(AuditLog.id.desc())
        )
        audit = audit_res.scalars().first()
        assert audit is not None
        assert audit.user_id == adv_user.id

    async def test_content_equivalence_preserves_approval_on_minor_whitespace(self) -> None:
        """Whitespace normalization does not revoke approval."""
        orig = ("Title", "Desc", "https://img.com/a.jpg", None, "https://target.com/a")
        equivalent = (" Title ", "  Desc\n", "https://img.com/a.jpg ", None, " https://target.com/a ")
        assert CreativeModerationService.are_contents_equivalent(
            orig[0], orig[1], orig[2], orig[3], orig[4],
            equivalent[0], equivalent[1], equivalent[2], equivalent[3], equivalent[4],
        )

    async def test_substantive_content_modification_revokes_approval(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        """Substantive edits to an approved creative immediately revoke approval and log audit entry."""
        adv_user, adv, adv_token = await _create_test_advertiser(test_db)
        _, cr = await _create_campaign_with_creative(test_db, adv.id, mod_status=ModerationStatus.APPROVED)
        assert cr.moderation_status == ModerationStatus.APPROVED

        # Edit creative title substantively
        patch_resp = await client.patch(
            f"/api/v1/creative/{cr.id}",
            headers={"Authorization": f"Bearer {adv_token}"},
            json={"title": "Completely Changed Unapproved Title"},
        )
        assert patch_resp.status_code == 200
        updated = patch_resp.json()
        assert updated["moderation_status"] == ModerationStatus.PENDING_REVIEW.value
        assert "Content modified" in (updated["moderation_reason"] or "")
        assert updated["status"] == Status.PENDING.value

        # Verify Audit Log
        audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "creative_content_modified_reset").order_by(AuditLog.id.desc())
        )
        audit = audit_res.scalars().first()
        assert audit is not None
        assert audit.user_id == adv_user.id
        details = json.loads(audit.details or "{}")
        assert details["creative_id"] == cr.id


# ── 3. Publisher Safety & IVT Detection ───────────────────────────────────────

class TestPublisherSafetyAndIVTDetection:
    """Verifies IVT algorithms, non-conclusive IP rule, clickjacking, and publisher safety summary."""

    async def test_click_velocity_exceeded(self, test_db: AsyncSession) -> None:
        """Rapid click bursts exceeding velocity limit trigger IVT invalidation."""
        ivt_service = IVTService(test_db)
        now = datetime.now(timezone.utc)

        async def mock_incr(key: str) -> int:
            if "burst" in key:
                return 1
            if "velocity" in key:
                return 16
            return 1

        with patch("liteads.ad_server.services.ivt_service.redis_client") as mock_redis:
            mock_redis.connected = True
            mock_redis.incr = AsyncMock(side_effect=mock_incr)
            mock_redis.expire = AsyncMock(return_value=True)

            result = await ivt_service.evaluate_event(
                event_type=EventType.CLICK,
                request_id="req_velo_1",
                campaign_id=1,
                creative_id=1,
                user_id="user_active_bot",
                ip_address="203.0.113.10",
                event_time=now,
            )
            assert result.is_invalid
            assert result.reason == "CLICK_VELOCITY_EXCEEDED"

    async def test_ip_sharing_protection_non_conclusive_fraud(self, test_db: AsyncSession) -> None:
        """Crucial rule: A single IP address alone is NEVER treated as conclusive fraud."""
        from liteads.models.ad import AdEvent
        ivt_service = IVTService(test_db)
        now = datetime.now(timezone.utc)

        imp = AdEvent(
            request_id="req_nat_1",
            event_type=EventType.IMPRESSION,
            campaign_id=1,
            creative_id=1,
            event_time=now - timedelta(seconds=3),
            is_invalid=False,
            cost=Decimal("0.05"),
        )
        test_db.add(imp)
        await test_db.commit()

        with patch("liteads.ad_server.services.ivt_service.redis_client") as mock_redis:
            mock_redis.connected = True
            mock_redis.incr = AsyncMock(return_value=2)
            mock_redis.expire = AsyncMock(return_value=True)

            result = await ivt_service.evaluate_event(
                event_type=EventType.CLICK,
                request_id="req_nat_1",
                campaign_id=1,
                creative_id=1,
                user_id=None,
                ip_address="198.51.100.25",
                event_time=now,
            )
            assert not result.is_invalid

    async def test_clickjacking_detection(self, test_db: AsyncSession) -> None:
        """Negative coordinates, out-of-bounds, or hidden iframe state must be flagged."""
        ivt_service = IVTService(test_db)
        now = datetime.now(timezone.utc)

        # 1. Negative coordinates
        res_neg = await ivt_service.evaluate_event(
            event_type=EventType.CLICK,
            request_id="req_cj_1",
            campaign_id=1,
            creative_id=1,
            extra={"click_x": -10, "click_y": 20},
            event_time=now,
        )
        assert res_neg.is_invalid
        assert res_neg.reason == "CLICKJACKING_SUSPECTED"

        # 2. Hidden iframe state
        res_hidden = await ivt_service.evaluate_event(
            event_type=EventType.CLICK,
            request_id="req_cj_2",
            campaign_id=1,
            creative_id=1,
            extra={"hidden_state": True},
            event_time=now,
        )
        assert res_hidden.is_invalid
        assert res_hidden.reason == "CLICKJACKING_SUSPECTED"

    async def test_publisher_safety_summary_endpoint_does_not_leak_thresholds(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        """Publisher safety summary returns actionable guidance without leaking proprietary internal threshold numbers."""
        pub_user, pub, pub_token = await _create_test_publisher(test_db)

        resp = await client.get(
            f"/api/v1/publisher/{pub.id}/safety-summary",
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "quality_score" in data
        assert "invalid_rate_pct" in data
        assert "warning_categories" in data
        assert "recommendations" in data

        dumped = json.dumps(data)
        assert "50ms" not in dumped
        assert "CLICK_VELOCITY_MAX_COUNT" not in dumped


# ── 4. Audited Financial Earnings & Adjustments ───────────────────────────────

class TestAuditedFinancialEarnings:
    """Verifies 6-state earnings breakdown, manual adjustments, and audit logging."""

    async def test_publisher_earnings_breakdown_distinguishes_six_states(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        pub_user, pub, pub_token = await _create_test_publisher(
            test_db, balance=Decimal("250.00"), pending=Decimal("75.00")
        )

        payout = PublisherPayout(
            publisher_id=pub.id,
            amount=Decimal("150.00"),
            currency="INR",
            status="paid",
            reference_id=f"payout_paid_{pub.id}",
        )
        test_db.add(payout)

        tx_earn = Transaction(
            publisher_id=pub.id,
            amount=Decimal("250.00"),
            currency="INR",
            type=TransactionType.EARNINGS,
            is_cleared=True,
            description="Cleared earnings",
        )
        test_db.add(tx_earn)

        tx_adj = Transaction(
            publisher_id=pub.id,
            amount=Decimal("-25.00"),
            currency="INR",
            type=TransactionType.ADJUSTMENT,
            is_cleared=True,
            description="IVT deduction",
        )
        test_db.add(tx_adj)
        await test_db.commit()

        resp = await client.get(
            f"/api/v1/publisher/{pub.id}/earnings-breakdown",
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert Decimal(str(data["payable"])) == Decimal("250.00")
        assert Decimal(str(data["under_review"])) == Decimal("75.00")
        assert Decimal(str(data["paid"])) == Decimal("150.00")
        assert Decimal(str(data["finalised"])) == Decimal("250.00")
        assert Decimal(str(data["adjusted"])) == Decimal("-25.00")
        assert Decimal(str(data["estimated"])) == Decimal("300.00")

    async def test_manual_publisher_adjustment_records_audit(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        admin_user, admin_token = await _create_test_admin(test_db)
        _, pub, _ = await _create_test_publisher(test_db, balance=Decimal("400.00"))

        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = await client.post(
            f"/api/v1/finance/publisher/{pub.id}/adjustment",
            headers=headers,
            json={
                "amount": "50.00",
                "reason": "Traffic quality audit penalty for click burst cluster",
            },
        )
        assert resp.status_code == 200
        tx = resp.json()
        assert tx["type"] == TransactionType.ADJUSTMENT.value
        assert Decimal(str(tx["amount"])) == Decimal("-50.00")

        audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "manual_payment_adjustment").order_by(AuditLog.id.desc())
        )
        audit = audit_res.scalars().first()
        assert audit is not None
        assert audit.user_id == admin_user.id
        details = json.loads(audit.details or "{}")
        assert details["publisher_id"] == pub.id
        assert details["amount"] == "50.00"


# ── 5. Administrative Audit & Control ─────────────────────────────────────────

class TestAdministrativeAuditAndControl:
    """Verifies immutable audit querying, RBAC restrictions, and entity status audits."""

    async def test_audit_logs_endpoint_rbac_enforcement(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        admin_user, admin_token = await _create_test_admin(test_db)
        adv_user, _, adv_token = await _create_test_advertiser(test_db)

        # 1. Non-admin gets 403 Forbidden
        unauth_resp = await client.get(
            "/api/v1/admin/audit-logs",
            headers={"Authorization": f"Bearer {adv_token}"},
        )
        assert unauth_resp.status_code == 403

        # 2. Admin gets 200 OK with logs
        auth_resp = await client.get(
            "/api/v1/admin/audit-logs",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert auth_resp.status_code == 200
        assert isinstance(auth_resp.json(), list)

    async def test_audit_logs_immutability_guards_reject_mutations(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        admin_user, admin_token = await _create_test_admin(test_db)
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. DELETE attempt on audit log is forbidden (403)
        del_resp = await client.delete("/api/v1/admin/audit-logs/1", headers=headers)
        assert del_resp.status_code == 403
        assert "immutable" in del_resp.text.lower()

        # 2. PUT attempt on audit log is forbidden (403)
        put_resp = await client.put("/api/v1/admin/audit-logs/1", headers=headers, json={"action": "tampered"})
        assert put_resp.status_code == 403
        assert "immutable" in put_resp.text.lower()

    async def test_publisher_status_change_records_audit(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        admin_user, admin_token = await _create_test_admin(test_db)
        _, pub, _ = await _create_test_publisher(test_db)
        assert pub.status == Status.ACTIVE

        resp = await client.patch(
            f"/api/v1/publisher/{pub.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": Status.PAUSED.value, "reason": "Compliance review pause"},
        )
        assert resp.status_code == 200

        audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "publisher_status_updated").order_by(AuditLog.id.desc())
        )
        audit = audit_res.scalars().first()
        assert audit is not None
        assert audit.user_id == admin_user.id
        details = json.loads(audit.details or "{}")
        assert details["publisher_id"] == pub.id
        assert details["new_status"] == Status.PAUSED.value
        assert details["reason"] == "Compliance review pause"
        assert details["actor_role"] == "admin"
        assert "publisher:" in details["target"]

    async def test_campaign_and_property_status_changes_record_audits(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        admin_user, admin_token = await _create_test_admin(test_db)
        _, adv, adv_token = await _create_test_advertiser(test_db)
        _, pub, _ = await _create_test_publisher(test_db)
        camp, _ = await _create_campaign_with_creative(test_db, adv.id)

        # 1. Campaign status update records audit
        camp_resp = await client.patch(
            f"/api/v1/campaign/{camp.id}",
            headers={"Authorization": f"Bearer {adv_token}"},
            json={"status": Status.PAUSED.value, "reason": "Paused by advertiser for budget review"},
        )
        assert camp_resp.status_code == 200

        camp_audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "campaign_status_updated").order_by(AuditLog.id.desc())
        )
        camp_audit = camp_audit_res.scalars().first()
        assert camp_audit is not None
        camp_details = json.loads(camp_audit.details or "{}")
        assert camp_details["campaign_id"] == camp.id
        assert camp_details["reason"] == "Paused by advertiser for budget review"
        assert camp_details["target"] == f"campaign:{camp.id}"

        # 2. Property status update records audit
        prop = PublisherProperty(
            publisher_id=pub.id,
            name="Audited Prop",
            domain="audited-prop.com",
            status=Status.ACTIVE,
            verification_status="VERIFIED",
        )
        test_db.add(prop)
        await test_db.commit()

        prop_resp = await client.patch(
            f"/api/v1/publisher/property/{prop.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": Status.PAUSED.value, "reason": "Domain under administrative review"},
        )
        assert prop_resp.status_code == 200

        prop_audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "property_status_updated").order_by(AuditLog.id.desc())
        )
        prop_audit = prop_audit_res.scalars().first()
        assert prop_audit is not None
        prop_details = json.loads(prop_audit.details or "{}")
        assert prop_details["property_id"] == prop.id
        assert prop_details["reason"] == "Domain under administrative review"
        assert prop_details["target"] == f"property:{prop.id}"


# ── 6. Delivery Enforcement on Suspension ─────────────────────────────────────

class TestDeliveryEnforcementOnSuspension:
    """Verifies that suspending publisher or property immediately stops live ad serving."""

    async def test_suspended_publisher_halts_ad_serving(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        admin_user, admin_token = await _create_test_admin(test_db)
        _, pub, _ = await _create_test_publisher(test_db)

        prop = PublisherProperty(
            publisher_id=pub.id,
            name="Main Portal",
            domain="example-portal.com",
            status=Status.ACTIVE,
            verification_status="VERIFIED",
        )
        test_db.add(prop)
        await test_db.flush()

        slot = AdSlot(
            publisher_id=pub.id,
            property_id=prop.id,
            slot_id=f"slot_pub_susp_{pub.id}",
            name="Header Banner",
            status=Status.ACTIVE,
            format=1,
            width=728,
            height=90,
        )
        test_db.add(slot)
        await test_db.commit()

        # Suspend publisher
        pub.status = Status.PAUSED
        await test_db.commit()

        # JSON ad request -> 400 Bad Request
        resp_json = await client.post(
            "/api/v1/ad/request",
            json={"slot_id": slot.slot_id, "num_ads": 1},
            headers={"origin": "https://example-portal.com"},
        )
        assert resp_json.status_code == 400
        assert "Publisher is not active" in resp_json.text

        # Iframe ad request -> 400 Bad Request
        resp_iframe = await client.get(
            f"/api/v1/ad/serve?slot_id={slot.slot_id}",
            headers={"origin": "https://example-portal.com"},
        )
        assert resp_iframe.status_code == 400
        assert "publisher inactive or suspended" in resp_iframe.text

    async def test_suspended_property_halts_ad_serving(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        _, pub, _ = await _create_test_publisher(test_db)

        prop = PublisherProperty(
            publisher_id=pub.id,
            name="Suspended Portal",
            domain="suspended-site.com",
            status=Status.PAUSED,  # Suspended property
            verification_status="VERIFIED",
        )
        test_db.add(prop)
        await test_db.flush()

        slot = AdSlot(
            publisher_id=pub.id,
            property_id=prop.id,
            slot_id=f"slot_prop_susp_{pub.id}",
            name="Sidebar",
            status=Status.ACTIVE,
            format=1,
            width=300,
            height=250,
        )
        test_db.add(slot)
        await test_db.commit()

        # JSON ad request -> 400 Bad Request
        resp_json = await client.post(
            "/api/v1/ad/request",
            json={"slot_id": slot.slot_id, "num_ads": 1},
            headers={"origin": "https://suspended-site.com"},
        )
        assert resp_json.status_code == 400
        assert "Publisher property is not active" in resp_json.text

        # Iframe ad request -> 400 Bad Request
        resp_iframe = await client.get(
            f"/api/v1/ad/serve?slot_id={slot.slot_id}",
            headers={"origin": "https://suspended-site.com"},
        )
        assert resp_iframe.status_code == 400
        assert "property inactive or suspended" in resp_iframe.text


# ── 7. Dashboard Operational Exceptions & SLA Tracking ────────────────────────

class TestDashboardOperationalExceptions:
    """Verifies operational exception queues, SLA statuses, and alert aggregations."""

    async def test_operational_exceptions_dashboard_endpoint(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        admin_user, admin_token = await _create_test_admin(test_db)
        adv_user, adv, _ = await _create_test_advertiser(test_db)

        _, cr = await _create_campaign_with_creative(test_db, adv.id, mod_status=ModerationStatus.PENDING_REVIEW)

        # Non-admin gets 403
        adv_token = create_access_token(adv_user)
        unauth_resp = await client.get(
            "/api/v1/admin/dashboard/operational-exceptions",
            headers={"Authorization": f"Bearer {adv_token}"},
        )
        assert unauth_resp.status_code == 403

        # Admin gets 200 with structured queues
        auth_resp = await client.get(
            "/api/v1/admin/dashboard/operational-exceptions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert auth_resp.status_code == 200
        data = auth_resp.json()
        assert "moderation_queue" in data
        assert "finance_queue" in data
        assert "stale_integrations" in data
        assert "total_exceptions_count" in data

        # Check moderation queue contains our pending creative with SLA status
        mod_queue = data["moderation_queue"]
        assert any(item["id"] == cr.id for item in mod_queue)
        item = next(it for it in mod_queue if it["id"] == cr.id)
        assert item["sla_status"] in ("ON_TRACK", "AT_RISK", "BREACHED")
        assert "time_remaining_minutes" in item
        assert item["owner"] == "compliance_team"
        assert item["status"] in ("open", "in_review", "escalated")
        assert item["triage_action"] == "/admin/creatives"

    async def test_website_monetization_disable_records_audit_log(
        self, client: AsyncClient, test_db: AsyncSession
    ) -> None:
        from liteads.models.website import WebsiteProject, WebsiteMonetizationStatus
        adv_user, adv, adv_token = await _create_test_advertiser(test_db)

        project = WebsiteProject(
            name="Audited Website",
            slug=f"audited-site-{int(datetime.now().timestamp() * 1000)}",
            owner_user_id=adv_user.id,
            status=1,
            monetization_status=WebsiteMonetizationStatus.ACTIVE_MONETIZED,
        )
        test_db.add(project)
        await test_db.commit()
        await test_db.refresh(project)

        headers = {"Authorization": f"Bearer {adv_token}"}
        resp = await client.post(f"/api/v1/website/projects/{project.id}/monetization/disable", headers=headers)
        assert resp.status_code == 200

        audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "website_monetization_disabled").order_by(AuditLog.id.desc())
        )
        audit = audit_res.scalars().first()
        assert audit is not None
        assert audit.user_id == adv_user.id
        details = json.loads(audit.details or "{}")
        assert details["project_id"] == project.id
        assert details["new_state"] == int(WebsiteMonetizationStatus.DISABLED)
        assert details["target"] == f"website_project:{project.id}"
