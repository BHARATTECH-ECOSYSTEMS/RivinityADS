"""
RivinityADS — Open Design Integration Phase 6 (Stage 6.2)
Financial Zero-Spend & Ledger Billing Hardening Adversarial Test Suite.

Work Unit: Financial Zero-Spend Verification
Validates:
1. Preview Impression Zero-Spend (Editor preview, draft preview, unpublished content, forged requests)
2. Preview Click Zero-Spend (CPC campaigns, manipulated client flags, unverified properties)
3. Preview Conversion Zero-Spend (CPA campaigns, preview clicks, preview tokens as click tokens)
4. Invalid Tracking Token & HMAC Attacks (Forged signatures, expired tokens, tampered parameters, replay guard)
5. Event Classification & Trust Boundaries (Client-supplied is_preview, billable, publisher_id)
6. Transaction Atomicity & Rollback Integrity (Failure isolation, zero orphaned ledger records)

CRITICAL RESTRICTION:
Live monetization remains STRICTLY DISABLED.
All tests run against isolated in-memory test databases and zero-spend sandbox assertions.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import time
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.routers.website_public import create_preview_token
from liteads.ad_server.services.billing import BillingService
from liteads.ad_server.services.event_service import EventService
from liteads.ad_server.services.conversion_service import ConversionService
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.tracking_sig import sign_click_token, sign_conversion_sig, sign_tracking
from liteads.models.ad import (
    AdEvent,
    Advertiser,
    Campaign,
    Creative,
    EventType,
    Status,
)
from liteads.models.auth import User
from liteads.models.finance import PlatformSettings, Transaction, TransactionType
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat, PublisherProperty
from liteads.models.website import (
    WebsiteMonetizationStatus,
    WebsitePage,
    WebsiteProject,
    WebsiteStatus,
)

pytestmark = pytest.mark.asyncio


# ── In-Memory Redis Mock Fixture for Financial & Replay Tests ─────────────────

@pytest.fixture(autouse=True)
def mock_redis_financial(monkeypatch):
    """
    In-memory Redis store ensuring isolated replay-guard and sliding-window
    counters without requiring an external running Redis server.
    """
    store: dict[str, int] = {}
    ttls: dict[str, int] = {}

    async def mock_incr(key: str, amount: int = 1):
        store[key] = store.get(key, 0) + amount
        return store[key]

    async def mock_expire(key: str, ttl: int):
        if key in store:
            ttls[key] = ttl
            return True
        return False

    async def mock_ttl(key: str):
        if key not in store:
            return -2
        return ttls.get(key, -1)

    async def mock_delete(*keys: str):
        count = 0
        for k in keys:
            if k in store:
                del store[k]
                count += 1
            ttls.pop(k, None)
        return count

    async def mock_hincrbyfloat(key: str, field: str, amount: float):
        return 0.0

    monkeypatch.setattr(redis_client, "incr", mock_incr)
    monkeypatch.setattr(redis_client, "expire", mock_expire)
    monkeypatch.setattr(redis_client, "ttl", mock_ttl)
    monkeypatch.setattr(redis_client, "delete", mock_delete)
    monkeypatch.setattr(redis_client, "hincrbyfloat", mock_hincrbyfloat)
    yield


# ── Financial Setup & Snapshot Utilities ─────────────────────────────────────

async def setup_financial_fixtures(
    test_db: AsyncSession,
    *,
    cpm_bid: Decimal = Decimal("1.5000"),
    cpc_bid: Decimal = Decimal("2.0000"),
    cpa_bid: Decimal = Decimal("50.0000"),
) -> dict[str, Any]:
    """Provision a full financial hierarchy: Advertiser ($100), Publisher ($10), Verified Property & Slot."""
    adv = Advertiser(name="Acme Corp Adv", balance=Decimal("100.0000"), status=Status.ACTIVE)
    test_db.add(adv)
    await test_db.flush()

    pub = Publisher(name="Global Media Pub", balance=Decimal("10.0000"), status=Status.ACTIVE)
    test_db.add(pub)
    await test_db.flush()

    user = User(
        email="financial_test_user@example.com",
        name="Financial Test User",
        password_hash="hashed_pw_financial_test_12345",
        role="advertiser",
        advertiser_id=adv.id,
        publisher_id=pub.id,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.flush()

    prop = PublisherProperty(
        publisher_id=pub.id,
        name="Global News Network",
        domain="global-news.com",
        verification_status="VERIFIED",
        status=Status.ACTIVE,
    )
    test_db.add(prop)
    await test_db.flush()

    slot = AdSlot(
        publisher_id=pub.id,
        property_id=prop.id,
        slot_id="slot_global_leaderboard",
        name="Header Leaderboard",
        format=1,
        status=Status.ACTIVE,
    )
    test_db.add(slot)
    await test_db.flush()

    platform_settings = PlatformSettings(id=1, publisher_rev_share=Decimal("0.700"))
    test_db.add(platform_settings)
    await test_db.flush()

    camp_cpm = Campaign(
        advertiser_id=adv.id,
        name="CPM Brand Campaign",
        bid_type=1,
        bid_amount=cpm_bid,
        budget_daily=Decimal("100.00"),
        spent_today=Decimal("0.0000"),
        spent_total=Decimal("0.0000"),
        status=Status.ACTIVE,
    )
    camp_cpc = Campaign(
        advertiser_id=adv.id,
        name="CPC Performance Campaign",
        bid_type=2,
        bid_amount=cpc_bid,
        budget_daily=Decimal("100.00"),
        spent_today=Decimal("0.0000"),
        spent_total=Decimal("0.0000"),
        status=Status.ACTIVE,
    )
    camp_cpa = Campaign(
        advertiser_id=adv.id,
        name="CPA Lead Gen Campaign",
        bid_type=3,
        bid_amount=cpa_bid,
        budget_daily=Decimal("500.00"),
        spent_today=Decimal("0.0000"),
        spent_total=Decimal("0.0000"),
        status=Status.ACTIVE,
    )
    test_db.add_all([camp_cpm, camp_cpc, camp_cpa])
    await test_db.flush()

    creat_cpm = Creative(
        campaign_id=camp_cpm.id,
        title="CPM Banner",
        landing_url="https://example.com/cpm",
        status=Status.ACTIVE,
    )
    creat_cpc = Creative(
        campaign_id=camp_cpc.id,
        title="CPC Banner",
        landing_url="https://example.com/cpc",
        status=Status.ACTIVE,
    )
    creat_cpa = Creative(
        campaign_id=camp_cpa.id,
        title="CPA Lead Banner",
        landing_url="https://example.com/cpa",
        status=Status.ACTIVE,
    )
    test_db.add_all([creat_cpm, creat_cpc, creat_cpa])
    await test_db.commit()

    return {
        "user": user,
        "adv": adv,
        "pub": pub,
        "prop": prop,
        "slot": slot,
        "camp_cpm": camp_cpm,
        "camp_cpc": camp_cpc,
        "camp_cpa": camp_cpa,
        "creat_cpm": creat_cpm,
        "creat_cpc": creat_cpc,
        "creat_cpa": creat_cpa,
    }


async def snapshot_financial_state(
    db: AsyncSession,
    adv_id: int,
    pub_id: int,
    camp_ids: list[int],
) -> dict[str, Any]:
    """Capture precise financial state across ledger, balances, and campaign spend."""
    db.expire_all()
    adv = await db.get(Advertiser, adv_id)
    pub = await db.get(Publisher, pub_id)
    camps = {}
    for cid in camp_ids:
        c = await db.get(Campaign, cid)
        if c:
            camps[cid] = {
                "spent_today": Decimal(str(c.spent_today)),
                "spent_total": Decimal(str(c.spent_total)),
                "impressions": c.impressions,
                "clicks": c.clicks,
                "conversions": c.conversions,
            }

    tx_count = (await db.execute(select(func.count(Transaction.id)))).scalar_one()
    event_count = (await db.execute(select(func.count(AdEvent.id)))).scalar_one()
    billable_events = (
        await db.execute(select(func.count(AdEvent.id)).where(AdEvent.cost > Decimal("0")))
    ).scalar_one()

    return {
        "adv_balance": Decimal(str(adv.balance)) if adv else Decimal("0"),
        "pub_balance": Decimal(str(pub.balance)) if pub else Decimal("0"),
        "camps": camps,
        "tx_count": tx_count,
        "event_count": event_count,
        "billable_events": billable_events,
    }


def assert_zero_financial_impact(before: dict[str, Any], after: dict[str, Any], msg: str = ""):
    """Enforce the central invariant: zero balance changes, zero spend, zero ledger records."""
    prefix = f"[{msg}] " if msg else ""
    assert after["adv_balance"] == before["adv_balance"], (
        f"{prefix}Advertiser balance changed: {before['adv_balance']} -> {after['adv_balance']}"
    )
    assert after["pub_balance"] == before["pub_balance"], (
        f"{prefix}Publisher balance changed: {before['pub_balance']} -> {after['pub_balance']}"
    )
    assert after["tx_count"] == before["tx_count"], (
        f"{prefix}Transactions created: {before['tx_count']} -> {after['tx_count']}"
    )
    assert after["billable_events"] == before["billable_events"], (
        f"{prefix}Billable events recorded: {before['billable_events']} -> {after['billable_events']}"
    )
    for cid, b_c in before["camps"].items():
        if cid in after["camps"]:
            a_c = after["camps"][cid]
            assert a_c["spent_today"] == b_c["spent_today"], (
                f"{prefix}Campaign {cid} spent_today changed: {b_c['spent_today']} -> {a_c['spent_today']}"
            )
            assert a_c["spent_total"] == b_c["spent_total"], (
                f"{prefix}Campaign {cid} spent_total changed: {b_c['spent_total']} -> {a_c['spent_total']}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# AREA A: Preview Impression Zero-Spend
# ══════════════════════════════════════════════════════════════════════════════

class TestPreviewImpressionZeroSpend:
    """Adversarial tests verifying that preview impressions produce zero billable activity."""

    async def test_editor_preview_impression_zero_spend(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Impression event marked with preview flags produces zero cost and zero financial ledger records."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "prev_editor_session_101"

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        sig = sign_tracking(req_id, ad_id, slot_id)
        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": sig,
                "extra": {"is_preview": True, "slot_id": slot_id},
            },
        )
        assert res.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Editor preview impression")

        # Verify event recorded has explicitly cost == 0
        stmt = select(AdEvent).where(AdEvent.request_id == req_id)
        ev = (await test_db.execute(stmt)).scalar_one_or_none()
        assert ev is not None
        assert ev.cost == Decimal("0")

    async def test_draft_preview_route_renders_placeholders_only(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Draft website preview route (/preview/{token}) renders non-billable sandbox placeholders only."""
        fixtures = await setup_financial_fixtures(test_db)
        user = fixtures["user"]
        camp_id = fixtures["camp_cpm"].id
        slot_id = fixtures["slot"].slot_id

        # Setup draft project with ad placement
        proj = WebsiteProject(
            name="Draft Preview Site",
            slug="draft-preview-site",
            owner_user_id=user.id,
            status=WebsiteStatus.DRAFT,
            monetization_status=WebsiteMonetizationStatus.DISABLED,
        )
        test_db.add(proj)
        await test_db.flush()

        page = WebsitePage(
            project_id=proj.id,
            title="Home",
            slug="",
            is_home=True,
            blocks={
                "schema_version": 2,
                "blocks": [
                    {
                        "id": "blk_ad_prev_1",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": slot_id,
                            "format": 1,
                        },
                    }
                ],
            },
        )
        test_db.add(page)
        await test_db.commit()

        token = create_preview_token(proj.id, user.id)

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.get(f"/preview/{token}")
        assert res.status_code == 200

        # Verify HTML contains placeholder and non-billable notice
        html = res.text
        assert "rivinity-ad-placeholder" in html
        assert "Non-Billable Sandbox" in html
        assert "Monetization Inactive" in html
        assert "/static/rivinity.js" not in html
        assert "rivinity-live-ad-container" not in html

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Draft website preview page render")

    async def test_forged_preview_tracking_request_zero_spend(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Attacker sending forged preview request_id cannot induce advertiser wallet debit."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "test_preview_forged_999"

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        sig = sign_tracking(req_id, ad_id, slot_id)
        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": sig,
            },
        )
        assert res.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Forged preview impression")

    async def test_preview_impression_on_pixel_endpoint_zero_spend(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """GET /api/v1/event/track pixel tracking with preview request_id produces zero cost and zero ledger records."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "prev_pixel_imp_888"

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        sig = sign_tracking(req_id, ad_id, slot_id)
        res = await client.get(
            f"/api/v1/event/track?type=impression&req={req_id}&ad={ad_id}&sig={sig}&slot={slot_id}"
        )
        assert res.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Pixel preview impression")


# ══════════════════════════════════════════════════════════════════════════════
# AREA B: Preview Click Zero-Spend
# ══════════════════════════════════════════════════════════════════════════════

class TestPreviewClickZeroSpend:
    """Adversarial tests verifying that preview-originated clicks produce zero cost on CPC campaigns."""

    async def test_preview_click_zero_spend_on_cpc_campaign(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Preview click on CPC campaign ($2.00 bid) produces zero cost and zero advertiser debit."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpc"].id
        creat_id = fixtures["creat_cpc"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "prev_clk_session_201"

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        sig = sign_tracking(req_id, ad_id, slot_id)
        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "click",
                "sig": sig,
                "extra": {"is_preview": True, "slot_id": slot_id},
            },
        )
        assert res.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "CPC preview click")

        stmt = select(AdEvent).where(AdEvent.request_id == req_id)
        ev = (await test_db.execute(stmt)).scalar_one_or_none()
        assert ev is not None
        assert ev.cost == Decimal("0")

    async def test_preview_click_client_flag_override_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Adversary sending is_preview=False or billable=True on a preview request_id is forced to zero cost."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpc"].id
        creat_id = fixtures["creat_cpc"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "prev_clk_spoof_202"

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        sig = sign_tracking(req_id, ad_id, slot_id)
        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "click",
                "sig": sig,
                "extra": {"is_preview": False, "billable": True, "slot_id": slot_id},
            },
        )
        assert res.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Client-overridden preview click")

    async def test_click_on_unverified_property_zero_spend(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Click on a slot attached to an unverified property produces zero cost."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpc"].id
        creat_id = fixtures["creat_cpc"].id
        ad_id = f"ad_{camp_id}_{creat_id}"

        # Unverified property and slot
        unverified_prop = PublisherProperty(
            publisher_id=fixtures["pub"].id,
            name="Unverified Site",
            domain="unverified-portal.com",
            verification_status="PENDING",
            status=Status.ACTIVE,
        )
        test_db.add(unverified_prop)
        await test_db.flush()

        unverified_slot = AdSlot(
            publisher_id=fixtures["pub"].id,
            property_id=unverified_prop.id,
            slot_id="slot_unverified_cpc",
            name="Unverified Banner",
            format=1,
            status=Status.ACTIVE,
        )
        test_db.add(unverified_slot)
        await test_db.commit()

        unver_slot_id = unverified_slot.slot_id
        req_id = "req_unver_click_203"
        sig = sign_tracking(req_id, ad_id, unver_slot_id)

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": unver_slot_id,
                "event_type": "click",
                "sig": sig,
            },
        )
        assert res.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Unverified property click")

    async def test_click_on_inactive_publisher_zero_spend(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Click on a slot owned by a suspended/inactive publisher produces zero cost."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpc"].id
        creat_id = fixtures["creat_cpc"].id
        ad_id = f"ad_{camp_id}_{creat_id}"

        inactive_pub = Publisher(
            name="Suspended Pub", balance=Decimal("0.00"), status=Status.INACTIVE
        )
        test_db.add(inactive_pub)
        await test_db.flush()

        prop = PublisherProperty(
            publisher_id=inactive_pub.id,
            name="Suspended Site",
            domain="suspended-site.com",
            verification_status="VERIFIED",
            status=Status.ACTIVE,
        )
        test_db.add(prop)
        await test_db.flush()

        slot = AdSlot(
            publisher_id=inactive_pub.id,
            property_id=prop.id,
            slot_id="slot_suspended_pub",
            name="Suspended Slot",
            format=1,
            status=Status.ACTIVE,
        )
        test_db.add(slot)
        await test_db.commit()

        inact_slot_id = slot.slot_id
        req_id = "req_inact_pub_click_204"
        sig = sign_tracking(req_id, ad_id, inact_slot_id)

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": inact_slot_id,
                "event_type": "click",
                "sig": sig,
            },
        )
        assert res.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Inactive publisher click")


# ══════════════════════════════════════════════════════════════════════════════
# AREA C: Preview Conversion Zero-Spend
# ══════════════════════════════════════════════════════════════════════════════

class TestPreviewConversionZeroSpend:
    """Adversarial tests verifying that preview-originated conversions produce zero spend on CPA campaigns."""

    async def test_conversion_tied_to_preview_click_zero_spend(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Conversion attributed to a preview-originated click must produce zero CPA cost ($0, not $50)."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpa"].id
        creat_id = fixtures["creat_cpa"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        preview_clk_req_id = "prev_clk_cpa_301"

        # 1. Track preview click (cost == 0)
        sig = sign_tracking(preview_clk_req_id, ad_id, slot_id)
        res_clk = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": preview_clk_req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "click",
                "sig": sig,
                "extra": {"is_preview": True, "slot_id": slot_id},
            },
        )
        assert res_clk.status_code == 200

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        # 2. Submit conversion referencing the preview click request_id
        res_conv = await client.post(
            "/api/v1/event/conversion",
            json={
                "request_id": preview_clk_req_id,
                "campaign_id": camp_id,
                "order_id": "ORD-PREV-CPA-001",
                "conversion_value": 150.0,
                "is_test": False,  # Attacker attempts to mark conversion as live production
            },
        )
        assert res_conv.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        # Invariant: Advertiser balance must remain exactly $100 (NOT debited $50 CPA)
        assert_zero_financial_impact(before, after, "Conversion tied to preview click")

        # Verify conversion record is marked is_test=True and cost=0
        stmt = select(AdEvent).where(
            AdEvent.external_order_id == "ORD-PREV-CPA-001",
            AdEvent.event_type == EventType.CONVERSION,
        )
        conv_ev = (await test_db.execute(stmt)).scalar_one_or_none()
        assert conv_ev is not None
        assert conv_ev.cost == Decimal("0")
        assert conv_ev.is_test is True

    async def test_conversion_with_preview_request_id_zero_spend(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Conversion directly presenting a prev_ request_id produces zero CPA cost."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpa"].id
        creat_id = fixtures["creat_cpa"].id

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res_conv = await client.post(
            "/api/v1/event/conversion",
            json={
                "request_id": "prev_direct_conv_302",
                "campaign_id": camp_id,
                "creative_id": creat_id,
                "order_id": "ORD-PREV-CPA-002",
                "conversion_value": 99.0,
                "is_test": False,
            },
        )
        assert res_conv.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Direct preview conversion request")

    async def test_conversion_with_preview_extra_flag_zero_spend(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Conversion with extra={'is_preview': True} produces zero CPA cost."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpa"].id

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res_conv = await client.post(
            "/api/v1/event/conversion",
            json={
                "request_id": "req_flag_conv_303",
                "campaign_id": camp_id,
                "order_id": "ORD-PREV-CPA-003",
                "conversion_value": 50.0,
                "extra": {"is_preview": True},
            },
        )
        assert res_conv.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Extra preview flag conversion")

    async def test_preview_token_used_as_click_token_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Website builder preview token passed as a conversion click_token is rejected format fail-closed."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpa"].id

        # Website preview token has 5 parts: proj_id.user_id.expiry.nonce.sig
        preview_tok = create_preview_token(1, 1)

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/conversion",
            json={
                "click_token": preview_tok,
                "campaign_id": camp_id,
                "order_id": "ORD-BAD-TOKEN-004",
                "conversion_value": 75.0,
            },
        )
        # Click token format expects req_id.camp_id.creat_id.ts.sig
        # Conversion parser attempts to parse camp_id and fails
        assert res.status_code == 400
        assert "click token" in res.json()["detail"].lower()

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Preview token used as click token")


# ══════════════════════════════════════════════════════════════════════════════
# AREA D: Invalid Tracking Token & HMAC Attacks
# ══════════════════════════════════════════════════════════════════════════════

class TestInvalidTrackingTokenAndHmacAttacks:
    """Adversarial tests validating HMAC signature verification, expiration, tampering, and replay protection."""

    async def test_forged_tracking_signature_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Arbitrary forged signature string on impression tracking returns 400 Bad signature."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": "req_forged_sig_401",
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": f"{int(time.time())}.attackerforgedhexsignature123456",
            },
        )
        assert res.status_code == 400
        assert "signature" in res.json()["detail"].lower()

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Forged tracking signature")

    async def test_modified_signature_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Tampering with trailing characters of a valid signature returns 400 Bad signature."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "req_mod_sig_402"

        valid_sig = sign_tracking(req_id, ad_id, slot_id)
        # Flip last characters
        ts, sig_hex = valid_sig.split(".", 1)
        tampered_sig = f"{ts}.{'a' if sig_hex[0] != 'a' else 'b'}{sig_hex[1:]}"

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": tampered_sig,
            },
        )
        assert res.status_code == 400
        assert "signature" in res.json()["detail"].lower()

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Modified tracking signature")

    async def test_missing_signature_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Missing or empty signature string returns 400 Missing signature."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": "req_missing_sig_403",
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": "",
            },
        )
        assert res.status_code == 400
        assert "signature" in res.json()["detail"].lower()

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Missing tracking signature")

    async def test_expired_tracking_signature_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Signature older than signature_ttl_seconds returns 400 Expired signature."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "req_exp_sig_404"

        # Construct signature with timestamp 700s in the past (TTL is 600s)
        expired_ts = str(int(time.time()) - 700)
        msg = f"{req_id}|{ad_id}|{slot_id}|{expired_ts}".encode()
        secret = get_settings().tracking.secret.encode()
        sig_hex = hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]
        expired_sig = f"{expired_ts}.{sig_hex}"

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": expired_sig,
            },
        )
        assert res.status_code == 400
        assert "expired" in res.json()["detail"].lower()

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Expired tracking signature")

    async def test_tampered_request_id_after_signing_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Valid signature for req_A submitted with req_B returns 400 Bad signature."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id

        sig_for_a = sign_tracking("req_A_405", ad_id, slot_id)

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": "req_B_405",  # Tampered request ID
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": sig_for_a,
            },
        )
        assert res.status_code == 400
        assert "signature" in res.json()["detail"].lower()

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Tampered request_id")

    async def test_tampered_ad_id_after_signing_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Valid signature for ad_1_1 submitted with ad_1_2 returns 400 Bad signature."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        slot_id = fixtures["slot"].slot_id
        req_id = "req_tamper_ad_406"

        sig = sign_tracking(req_id, f"ad_{camp_id}_1", slot_id)

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": f"ad_{camp_id}_999",  # Tampered ad_id
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": sig,
            },
        )
        assert res.status_code == 400

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Tampered ad_id")

    async def test_tampered_slot_id_after_signing_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Valid signature for slot_A submitted with slot_B returns 400 Bad signature."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        req_id = "req_tamper_slot_407"

        sig = sign_tracking(req_id, ad_id, "slot_legitimate_A")

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": "slot_injected_B",  # Tampered slot_id
                "event_type": "impression",
                "sig": sig,
            },
        )
        assert res.status_code == 400

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Tampered slot_id")

    async def test_replayed_impression_rejected_by_replay_guard(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Second submission of identical impression returns 429 and causes zero additional billing."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "req_replay_imp_408"

        sig = sign_tracking(req_id, ad_id, slot_id)
        payload = {
            "request_id": req_id,
            "ad_id": ad_id,
            "slot_id": slot_id,
            "event_type": "impression",
            "sig": sig,
        }

        # 1. First impression succeeds and bills 1x
        res1 = await client.post("/api/v1/event/track", json=payload)
        assert res1.status_code == 200

        state_after_first = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        # 2. Second impression replayed with identical payload -> 429
        res2 = await client.post("/api/v1/event/track", json=payload)
        assert res2.status_code == 429
        assert "duplicate" in res2.json()["detail"].lower()

        state_after_replay = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        # Replayed impression must produce ZERO financial difference from state_after_first
        assert_zero_financial_impact(
            state_after_first, state_after_replay, "Replayed duplicate impression"
        )

    async def test_replayed_click_bounded_by_replay_guard(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Click events beyond limit (3 clicks per request_id) return 429 preventing wallet drain."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpc"].id
        creat_id = fixtures["creat_cpc"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "req_replay_clk_409"

        sig = sign_tracking(req_id, ad_id, slot_id)
        payload = {
            "request_id": req_id,
            "ad_id": ad_id,
            "slot_id": slot_id,
            "event_type": "click",
            "sig": sig,
        }

        # First 3 clicks are permitted by replay guard limit
        for i in range(1, 4):
            r = await client.post("/api/v1/event/track", json=payload)
            assert r.status_code == 200, f"Click {i} failed: {r.text}"

        state_after_third = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        # 4th click must be rejected with 429
        res4 = await client.post("/api/v1/event/track", json=payload)
        assert res4.status_code == 429

        state_after_fourth = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(
            state_after_third, state_after_fourth, "Exceeded replay click limit"
        )

    async def test_preview_token_as_tracking_signature_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Attempting to use a website builder preview token as tracking 'sig' is rejected with 400."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id

        preview_tok = create_preview_token(1, 1)

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": "req_prev_as_sig_410",
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": preview_tok,
            },
        )
        assert res.status_code == 400

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Preview token as tracking sig")


# ══════════════════════════════════════════════════════════════════════════════
# AREA E: Event Classification & Trust Boundaries
# ══════════════════════════════════════════════════════════════════════════════

class TestEventClassificationAndTrustBoundaries:
    """Verifies that billing authority is derived solely from trusted server state, not client parameters."""

    async def test_client_supplied_is_preview_false_on_preview_req_ignored(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Client-supplied is_preview=False cannot grant billing authority when request_id is a preview."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "prev_trust_bnd_501"

        sig = sign_tracking(req_id, ad_id, slot_id)

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": sig,
                "extra": {"is_preview": False, "billable": True},
            },
        )
        assert res.status_code == 200

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Trust boundary: is_preview=False override")

    async def test_client_supplied_publisher_id_in_extra_ignored(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Client-supplied publisher_id in extra does not override database-resolved slot publisher."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "req_trust_pub_502"

        sig = sign_tracking(req_id, ad_id, slot_id)

        # Attacker tries to credit arbitrary publisher_id: 99999
        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": sig,
                "extra": {"publisher_id": 99999},
            },
        )
        assert res.status_code == 200

        # Authoritative publisher was credited, not 99999
        await test_db.refresh(fixtures["pub"])
        assert fixtures["pub"].balance > Decimal("10.00")

    async def test_invalid_event_type_rejected_before_signature_or_billing(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Invalid or unrecognized event_type string fails fast before signature or replay processing."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": "req_invalid_type_503",
                "ad_id": f"ad_{camp_id}_1",
                "event_type": "arbitrary_payout_trigger",
                "sig": "some_sig",
            },
        )
        assert res.status_code == 400
        assert "event type" in res.json()["detail"].lower()

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Invalid event type")

    async def test_s2s_conversion_signature_forgery_rejected(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Server-to-server (S2S) conversion postback with forged HMAC signature returns 400."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpa"].id

        before = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )

        res = await client.post(
            "/api/v1/conversion/postback",
            json={
                "campaign_id": camp_id,
                "order_id": "ORD-FORGED-S2S-504",
                "conversion_value": 300.0,
                "currency": "INR",
                "timestamp": int(time.time()),
                "signature": "forged_s2s_signature_hex_1234567890",
            },
        )
        assert res.status_code == 400
        assert "signature" in res.json()["detail"].lower()

        after = await snapshot_financial_state(
            test_db, fixtures["adv"].id, fixtures["pub"].id, [camp_id]
        )
        assert_zero_financial_impact(before, after, "Forged S2S conversion signature")


# ══════════════════════════════════════════════════════════════════════════════
# AREA F: Transaction Atomicity & Rollback Integrity
# ══════════════════════════════════════════════════════════════════════════════

class TestTransactionAtomicityAndRollback:
    """Verifies that failed billing or rejected operations leave zero partial records or balance alterations."""

    async def test_insufficient_advertiser_balance_fails_closed_zero_ledger(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """Advertiser with balance lower than cost fails billing with zero partial deduction or ledger entry."""
        # Provision advertiser with only $0.0005 balance (less than CPM $0.0015 charge)
        fixtures = await setup_financial_fixtures(test_db, cpm_bid=Decimal("1.5000"))
        adv = fixtures["adv"]
        adv.balance = Decimal("0.0005")
        test_db.add(adv)
        await test_db.commit()

        camp_id = fixtures["camp_cpm"].id
        creat_id = fixtures["creat_cpm"].id
        ad_id = f"ad_{camp_id}_{creat_id}"
        slot_id = fixtures["slot"].slot_id
        req_id = "req_insufficient_funds_601"

        before = await snapshot_financial_state(
            test_db, adv.id, fixtures["pub"].id, [camp_id]
        )

        sig = sign_tracking(req_id, ad_id, slot_id)
        # Event tracking returns False or 200 with message="Failed to record event"
        res = await client.post(
            "/api/v1/event/track",
            json={
                "request_id": req_id,
                "ad_id": ad_id,
                "slot_id": slot_id,
                "event_type": "impression",
                "sig": sig,
            },
        )
        assert res.status_code == 200
        assert res.json()["success"] is False

        after = await snapshot_financial_state(
            test_db, adv.id, fixtures["pub"].id, [camp_id]
        )
        # Invariant: Balance stays exactly $0.0005, zero transactions recorded
        assert_zero_financial_impact(before, after, "Insufficient funds fail closed")

    async def test_duplicate_database_constraint_rolls_back_cleanly(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        """IntegrityError during database event insertion rolls back without partial transactions."""
        fixtures = await setup_financial_fixtures(test_db)
        camp_id = fixtures["camp_cpm"].id
        adv_id = fixtures["adv"].id
        pub_id = fixtures["pub"].id

        # Insert a pre-existing AdEvent with known idempotency key
        conflict_key = "req_conflict_key_602:1:1:1"
        existing_ev = AdEvent(
            request_id="req_preexisting_602",
            campaign_id=camp_id,
            event_type=EventType.IMPRESSION,
            event_time=datetime.now(timezone.utc).replace(tzinfo=None),
            cost=Decimal("0"),
            idempotency_key=conflict_key,
        )
        test_db.add(existing_ev)
        await test_db.commit()

        event_service = EventService(test_db)

        before = await snapshot_financial_state(test_db, adv_id, pub_id, [camp_id])

        # Attempt to track an event colliding on the unique constraint
        success = await event_service.track_event(
            request_id="req_conflict_key_602",
            ad_id=f"ad_{camp_id}_1",
            event_type="impression",
            seq=1,
            raise_on_duplicate=False,
        )
        assert success is False

        after = await snapshot_financial_state(test_db, adv_id, pub_id, [camp_id])
        assert_zero_financial_impact(before, after, "DB integrity collision rollback")
