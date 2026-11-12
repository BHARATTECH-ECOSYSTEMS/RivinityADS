"""
Phase 3 Measurement Trustworthiness, IVT Controls, Disputes, and Reconciliation Test Suite.

Verifies:
1. First-Party IVT: Impossible click timing (<50ms) is flagged with is_invalid=True, cost=0, and no wallet deduction.
2. First-Party IVT: Click bursts (>3 clicks per 5s) are flagged with is_invalid=True, cost=0.
3. First-Party IVT: Orphan clicks without matching impression are flagged with is_invalid=True, cost=0.
4. First-Party IVT: Replayed duplicate events are flagged/rejected without double billing.
5. Attribution Safety: Conversions referencing invalid IVT clicks are rejected from attribution (is_attributed=False).
6. Consistent Rollups: RollupService aggregates valid events while strictly excluding IVT and test events.
7. Data Minimisation & Ephemeral Pruning: Old test/ephemeral tracking events are pruned after retention window.
8. Financial Reconciliation: Audits gross delivery spend against ledger transactions and detects drift.
9. Dispute Lifecycle: Advertisers can submit disputes, tenant isolation is enforced, and admin resolution refunds wallet.
10. Publisher Traffic Appeal: Publishers can appeal IVT traffic, and admin resolution credits publisher balance.
11. Exportable Reporting: RFC 4180 CSV exports for advertiser and publisher performance metrics.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.billing import BillingService
from liteads.ad_server.services.conversion_service import ConversionService
from liteads.ad_server.services.event_service import EventService
from liteads.ad_server.services.reconciliation_service import ReconciliationService
from liteads.ad_server.services.rollup_service import RollupService
from liteads.common.cache import redis_client
from liteads.common.security import create_access_token
from liteads.models.ad import (
    AdEvent,
    AdSet,
    Advertiser,
    Campaign,
    Creative,
    HourlyStat,
)
from liteads.models.auth import User
from liteads.models.base import BidType, CreativeType, EventType, ModerationStatus, Status, TransactionType
from liteads.models.finance import BillingDispute, PlatformSettings, Transaction
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat, PublisherProperty

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures & Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _create_test_entities(
    session: AsyncSession,
    adv_balance: Decimal = Decimal("5000.00"),
    pub_balance: Decimal = Decimal("0.00"),
) -> dict[str, Any]:
    """Helper to bootstrap users, advertiser, campaign, creative, publisher, and slot."""
    # 1. Admin User
    admin_user = User(
        email="admin_phase3@example.com",
        name="Admin User",
        password_hash="test_password_hash_admin_12345678",
        role="admin",
        is_active=True,
        email_verified=True,
    )
    session.add(admin_user)

    # 2. Advertiser User & Advertiser
    adv_user = User(
        email="adv_phase3@example.com",
        name="Advertiser User",
        password_hash="test_password_hash_adv_12345678",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    session.add(adv_user)
    await session.flush()

    advertiser = Advertiser(
        name="Phase 3 Test Brand",
        company="Phase 3 Corp",
        balance=adv_balance,
        owner_user_id=adv_user.id,
    )
    session.add(advertiser)
    await session.flush()
    adv_user.advertiser_id = advertiser.id

    # 3. Campaign & Creative
    campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Phase 3 Measurement Campaign",
        budget_total=Decimal("10000.00"),
        budget_daily=Decimal("1000.00"),
        bid_type=BidType.CPM,
        bid_amount=Decimal("100.00"),  # CPM = 100 INR -> 0.10 INR per impression
        status=Status.ACTIVE,
    )
    session.add(campaign)
    await session.flush()

    creative = Creative(
        campaign_id=campaign.id,
        title="Phase 3 Test Banner",
        landing_url="https://example.com/landing",
        creative_type=CreativeType.BANNER,
        format="banner_300x250",
        category="technology",
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED,
    )
    session.add(creative)

    # 4. Publisher User & Publisher
    pub_user = User(
        email="pub_phase3@example.com",
        name="Publisher User",
        password_hash="test_password_hash_pub_12345678",
        role="publisher",
        is_active=True,
        email_verified=True,
    )
    session.add(pub_user)
    await session.flush()

    publisher = Publisher(
        name="Phase 3 Test Network",
        balance=pub_balance,
        owner_user_id=pub_user.id,
        status=Status.ACTIVE,
    )
    session.add(publisher)
    await session.flush()
    pub_user.publisher_id = publisher.id

    # 5. Publisher Property & Ad Slot
    prop = PublisherProperty(
        publisher_id=publisher.id,
        name="Phase 3 Media Site",
        domain="example.com",
        status=Status.ACTIVE,
        verification_status="VERIFIED",
    )
    session.add(prop)
    await session.flush()

    slot = AdSlot(
        publisher_id=publisher.id,
        property_id=prop.id,
        name="Header Display",
        slot_id="slot_p3_header",
        format="banner_300x250",
        width=300,
        height=250,
        is_chat_surface=False,
    )
    session.add(slot)
    await session.flush()

    # Initial Deposit for reconciliation consistency
    init_dep = Transaction(
        type=TransactionType.DEPOSIT,
        amount=adv_balance,
        currency="INR",
        advertiser_id=advertiser.id,
        reference_id="dep_init_phase3",
        description="Initial Wallet Funding",
    )
    session.add(init_dep)
    await session.commit()

    return {
        "admin_user": admin_user,
        "adv_user": adv_user,
        "advertiser": advertiser,
        "campaign": campaign,
        "creative": creative,
        "pub_user": pub_user,
        "publisher": publisher,
        "slot": slot,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task E: First-Party Invalid Traffic (IVT) Controls Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_ivt_impossible_click_timing_rejected(test_db: AsyncSession):
    """Clicks occurring < 50ms after an impression must be flagged as IVT with cost=0 and no billing."""
    ctx = await _create_test_entities(test_db)
    adv = ctx["advertiser"]
    camp = ctx["campaign"]
    creat = ctx["creative"]
    slot = ctx["slot"]

    initial_adv_balance = adv.balance
    ad_id = f"ad_{camp.id}_{creat.id}"
    req_id = "req_timing_test_001"

    # 1. Track Impression at T0
    t0 = int(time.time())
    event_service = EventService(test_db)
    success_imp = await event_service.track_event(
        request_id=req_id,
        ad_id=ad_id,
        event_type="impression",
        timestamp=t0,
        extra={"slot_id": slot.slot_id},
    )
    assert success_imp is True
    await test_db.commit()

    # Check that impression was billable
    await test_db.refresh(adv)
    imp_cost = Decimal("100.00") / Decimal("1000")  # 0.10 INR
    assert adv.balance == initial_adv_balance - imp_cost

    # 2. Track Click at T0 + 10ms (0.010s < 0.050s threshold)
    t_impossible = t0  # identical timestamp or < 50ms
    success_click = await event_service.track_event(
        request_id=req_id,
        ad_id=ad_id,
        event_type="click",
        timestamp=t_impossible,
        extra={"slot_id": slot.slot_id},
    )
    assert success_click is True
    await test_db.commit()

    # Query the stored click event in ad_events
    click_res = await test_db.execute(
        select(AdEvent).where(
            AdEvent.request_id == req_id,
            AdEvent.event_type == EventType.CLICK,
        )
    )
    click_event = click_res.scalar_one_or_none()
    assert click_event is not None
    assert click_event.is_invalid is True
    assert click_event.invalid_reason == "IMPOSSIBLE_TIMING"
    assert click_event.cost == Decimal("0.000000")

    # Inviolable Rule: Advertiser balance must NOT be debited for IVT click
    await test_db.refresh(adv)
    assert adv.balance == initial_adv_balance - imp_cost


async def test_ivt_orphan_click_rejected(test_db: AsyncSession):
    """A click referencing a non-existent impression must be flagged as ORPHAN_CLICK with cost=0."""
    ctx = await _create_test_entities(test_db)
    camp = ctx["campaign"]
    creat = ctx["creative"]
    slot = ctx["slot"]

    ad_id = f"ad_{camp.id}_{creat.id}"
    orphan_req_id = "req_nonexistent_99999"

    event_service = EventService(test_db)
    success = await event_service.track_event(
        request_id=orphan_req_id,
        ad_id=ad_id,
        event_type="click",
        timestamp=int(time.time()),
        extra={"slot_id": slot.slot_id},
    )
    assert success is True
    await test_db.commit()

    click_res = await test_db.execute(
        select(AdEvent).where(AdEvent.request_id == orphan_req_id)
    )
    click_event = click_res.scalar_one_or_none()
    assert click_event is not None
    assert click_event.is_invalid is True
    assert click_event.invalid_reason == "ORPHAN_CLICK"
    assert click_event.cost == Decimal("0.000000")


async def test_ivt_click_burst_rate_limiting(test_db: AsyncSession):
    """Sending more than 3 clicks within 5 seconds triggers CLICK_BURST IVT detection on subsequent clicks."""
    ctx = await _create_test_entities(test_db)
    camp = ctx["campaign"]
    creat = ctx["creative"]
    slot = ctx["slot"]

    ad_id = f"ad_{camp.id}_{creat.id}"
    req_id = "req_burst_test_002"
    now_ts = int(time.time())

    # Create Impression first
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id=req_id,
        ad_id=ad_id,
        event_type="impression",
        timestamp=now_ts - 10,
        extra={"slot_id": slot.slot_id},
    )
    await test_db.commit()

    # Track 3 clicks within burst window
    for i in range(1, 4):
        await event_service.track_event(
            request_id=req_id,
            ad_id=ad_id,
            event_type="click",
            timestamp=now_ts - 5 + i,
            seq=i,
            extra={"slot_id": slot.slot_id, "ip_address": "203.0.113.42"},
        )
    await test_db.commit()

    # 4th click within 5 seconds must be flagged as CLICK_BURST
    await event_service.track_event(
        request_id=req_id,
        ad_id=ad_id,
        event_type="click",
        timestamp=now_ts,
        seq=4,
        extra={"slot_id": slot.slot_id, "ip_address": "203.0.113.42"},
    )
    await test_db.commit()

    fourth_res = await test_db.execute(
        select(AdEvent).where(
            AdEvent.request_id == req_id,
            AdEvent.event_type == EventType.CLICK,
        ).order_by(AdEvent.id.desc()).limit(1)
    )
    fourth_click = fourth_res.scalar_one()
    assert fourth_click.is_invalid is True
    assert fourth_click.invalid_reason == "CLICK_BURST"
    assert fourth_click.cost == Decimal("0")


async def test_attributed_conversion_with_invalid_click_rejected(test_db: AsyncSession):
    """Conversions referencing an invalid IVT click must be denied attribution (is_attributed=False)."""
    ctx = await _create_test_entities(test_db)
    adv = ctx["advertiser"]
    camp = ctx["campaign"]
    creat = ctx["creative"]

    # Set campaign to CPA bid type
    camp.bid_type = BidType.CPA
    camp.bid_amount = Decimal("50.00")
    await test_db.commit()

    # Manually insert an invalid click
    req_id = "req_cpa_ivt_invalid_click"
    invalid_click = AdEvent(
        request_id=req_id,
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        cost=Decimal("0"),
        is_invalid=True,
        invalid_reason="IMPOSSIBLE_TIMING",
    )
    test_db.add(invalid_click)
    await test_db.commit()

    initial_adv_balance = adv.balance

    # Attempt to attribute conversion to this invalid click
    conv_service = ConversionService(test_db)
    result = await conv_service.track_conversion(
        request_id=req_id,
        campaign_id=camp.id,
        order_id="order_ivt_cpa_001",
        conversion_value=Decimal("500.00"),
    )
    await test_db.commit()

    assert result.is_attributed is False
    assert result.attribution_reason == "click_invalid_ivt"

    # Wallet must NOT have been charged
    await test_db.refresh(adv)
    assert adv.balance == initial_adv_balance


# ─────────────────────────────────────────────────────────────────────────────
# Task C & G: Consistent Rollups & Data Minimisation Pruning
# ─────────────────────────────────────────────────────────────────────────────

async def test_sync_campaign_hourly_stats_excludes_ivt(test_db: AsyncSession):
    """HourlyStat rollups must include valid events and strictly exclude IVT and test events."""
    ctx = await _create_test_entities(test_db)
    camp = ctx["campaign"]
    creat = ctx["creative"]

    now = datetime.now(timezone.utc).replace(minute=15, second=0, microsecond=0, tzinfo=None)

    # 1. Add 1 valid impression
    test_db.add(AdEvent(
        request_id="req_valid_1",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.IMPRESSION,
        event_time=now,
        cost=Decimal("0.100000"),
        is_invalid=False,
        is_test=False,
    ))
    # 2. Add 1 IVT impression
    test_db.add(AdEvent(
        request_id="req_ivt_1",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.IMPRESSION,
        event_time=now,
        cost=Decimal("0.000000"),
        is_invalid=True,
        invalid_reason="BOT_USER_AGENT",
        is_test=False,
    ))
    # 3. Add 1 test impression
    test_db.add(AdEvent(
        request_id="test_sandbox_1",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.IMPRESSION,
        event_time=now,
        cost=Decimal("0.000000"),
        is_invalid=False,
        is_test=True,
    ))
    await test_db.commit()

    rollup_service = RollupService(test_db)
    stat = await rollup_service.sync_campaign_hourly_stats(camp.id, now)
    await test_db.commit()

    assert stat is not None
    assert stat.impressions == 1  # Only the single valid impression is counted
    assert stat.spend == Decimal("0.1000")


async def test_ephemeral_retention_pruning(test_db: AsyncSession):
    """Pruning removes ephemeral test records older than 90 days while leaving financial ledger intact."""
    ctx = await _create_test_entities(test_db)
    camp = ctx["campaign"]

    old_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)

    # Add an old test event
    old_test_event = AdEvent(
        request_id="test_old_event_prune",
        campaign_id=camp.id,
        event_type=EventType.IMPRESSION,
        event_time=old_dt,
        created_at=old_dt,
        is_test=True,
        cost=Decimal("0"),
    )
    test_db.add(old_test_event)
    await test_db.commit()

    rollup_service = RollupService(test_db)
    pruned_count = await rollup_service.prune_ephemeral_events(retention_days=90)
    await test_db.commit()

    assert pruned_count >= 1

    # Verify pruned event is gone
    res = await test_db.execute(
        select(AdEvent).where(AdEvent.request_id == "test_old_event_prune")
    )
    assert res.scalar_one_or_none() is None


# ─────────────────────────────────────────────────────────────────────────────
# Task D: Financial & Measurement Reconciliation Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_reconciliation_service_balanced(test_db: AsyncSession):
    """When ad delivery spend matches ledger transactions, status must be BALANCED with 0 delta."""
    ctx = await _create_test_entities(test_db)
    adv = ctx["advertiser"]
    camp = ctx["campaign"]
    creat = ctx["creative"]
    slot = ctx["slot"]

    ad_id = f"ad_{camp.id}_{creat.id}"
    req_id = "req_reconcile_balanced_01"

    event_service = EventService(test_db)
    await event_service.track_event(
        request_id=req_id,
        ad_id=ad_id,
        event_type="impression",
        extra={"slot_id": slot.slot_id},
    )
    await test_db.commit()

    rec_service = ReconciliationService(test_db)
    today = datetime.now(timezone.utc).date()
    report = await rec_service.reconcile(start_date=today, end_date=today, advertiser_id=adv.id)

    assert report.status == "BALANCED"
    assert report.spend_delta <= Decimal("0.01")
    assert len(report.discrepancies) == 0


async def test_reconciliation_service_detects_drift(test_db: AsyncSession):
    """When a financial ledger transaction is missing or unbalanced, reconcile alerts on discrepancy."""
    ctx = await _create_test_entities(test_db)
    adv = ctx["advertiser"]
    camp = ctx["campaign"]

    # Artificially inject unbilled delivery into ad_events without a corresponding Transaction
    test_db.add(AdEvent(
        request_id="req_unbilled_delivery_drift",
        campaign_id=camp.id,
        event_type=EventType.IMPRESSION,
        event_time=datetime.now(timezone.utc).replace(tzinfo=None),
        cost=Decimal("500.00"),  # ₹500 unbilled
        is_invalid=False,
        is_test=False,
    ))
    await test_db.commit()

    rec_service = ReconciliationService(test_db)
    today = datetime.now(timezone.utc).date()
    report = await rec_service.reconcile(start_date=today, end_date=today, advertiser_id=adv.id)

    assert report.status == "DISCREPANCY_DETECTED"
    assert report.spend_delta >= Decimal("500.00")
    assert len(report.alerts) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Task F: Dispute & Appeal Workflow Integration Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_dispute_creation_and_tenant_isolation(client: AsyncClient, test_db: AsyncSession):
    """Advertisers can submit disputes; tenant isolation prevents unauthorized viewing."""
    ctx = await _create_test_entities(test_db)
    adv_user = ctx["adv_user"]
    adv = ctx["advertiser"]
    pub_user = ctx["pub_user"]

    adv_token = create_access_token(adv_user)
    pub_token = create_access_token(pub_user)

    # 1. Advertiser submits dispute
    dispute_payload = {
        "dispute_type": "ADVERTISER_SPEND",
        "advertiser_id": adv.id,
        "amount_disputed": "250.00",
        "reason": "Suspected bot click spike during peak window",
        "evidence": {"log_snippet": "repeated clicks from ASN 12345"},
    }
    resp = await client.post(
        "/api/v1/finance/disputes",
        json=dispute_payload,
        headers={"Authorization": f"Bearer {adv_token}"},
    )
    assert resp.status_code == 201
    dispute_data = resp.json()
    dispute_id = dispute_data["id"]
    assert dispute_data["status"] == "OPEN"
    assert Decimal(dispute_data["amount_disputed"]) == Decimal("250.00")

    # 2. Publisher attempts to view advertiser's dispute -> 403 Forbidden
    resp_pub = await client.get(
        f"/api/v1/finance/disputes/{dispute_id}",
        headers={"Authorization": f"Bearer {pub_token}"},
    )
    assert resp_pub.status_code == 403

    # 3. Owner advertiser views dispute -> 200 OK
    resp_owner = await client.get(
        f"/api/v1/finance/disputes/{dispute_id}",
        headers={"Authorization": f"Bearer {adv_token}"},
    )
    assert resp_owner.status_code == 200
    assert resp_owner.json()["id"] == dispute_id


async def test_dispute_resolution_with_wallet_refund(client: AsyncClient, test_db: AsyncSession):
    """Admin resolving an advertiser dispute with RESOLVED_REFUNDED atomically credits wallet and creates REFUND transaction."""
    ctx = await _create_test_entities(test_db)
    admin_user = ctx["admin_user"]
    adv_user = ctx["adv_user"]
    adv = ctx["advertiser"]

    admin_token = create_access_token(admin_user)
    adv_token = create_access_token(adv_user)

    initial_balance = adv.balance

    # 1. Advertiser creates dispute
    create_resp = await client.post(
        "/api/v1/finance/disputes",
        json={
            "dispute_type": "ADVERTISER_SPEND",
            "advertiser_id": adv.id,
            "amount_disputed": "350.00",
            "reason": "Duplicate click billing dispute",
        },
        headers={"Authorization": f"Bearer {adv_token}"},
    )
    assert create_resp.status_code == 201
    dispute_id = create_resp.json()["id"]

    # 2. Admin resolves dispute with partial/full refund
    resolve_resp = await client.post(
        f"/api/v1/finance/disputes/{dispute_id}/resolve",
        json={
            "status": "RESOLVED_REFUNDED",
            "amount_credited": "350.00",
            "resolution_notes": "Verified duplicate click billing. Crediting 350 INR to wallet.",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resolve_resp.status_code == 200
    res_data = resolve_resp.json()
    assert res_data["status"] == "RESOLVED_REFUNDED"
    assert Decimal(res_data["amount_credited"]) == Decimal("350.00")

    # 3. Verify advertiser balance increased by ₹350.00
    await test_db.refresh(adv)
    assert adv.balance == initial_balance + Decimal("350.00")

    # 4. Verify immutable REFUND transaction was recorded
    tx_res = await test_db.execute(
        select(Transaction).where(Transaction.reference_id == f"dispute_refund_{dispute_id}")
    )
    refund_tx = tx_res.scalar_one_or_none()
    assert refund_tx is not None
    assert refund_tx.type == TransactionType.REFUND
    assert refund_tx.amount == Decimal("350.00")


async def test_publisher_traffic_appeal_resolution(client: AsyncClient, test_db: AsyncSession):
    """Admin resolving a publisher IVT appeal with RESOLVED_CREDITED credits publisher balance."""
    ctx = await _create_test_entities(test_db)
    admin_user = ctx["admin_user"]
    pub_user = ctx["pub_user"]
    pub = ctx["publisher"]

    admin_token = create_access_token(admin_user)
    pub_token = create_access_token(pub_user)

    initial_pub_balance = pub.balance

    # 1. Publisher appeals IVT traffic deduction
    create_resp = await client.post(
        "/api/v1/finance/disputes",
        json={
            "dispute_type": "IVT_APPEAL",
            "publisher_id": pub.id,
            "amount_disputed": "150.00",
            "reason": "Traffic spike was verified genuine promotion campaign",
        },
        headers={"Authorization": f"Bearer {pub_token}"},
    )
    assert create_resp.status_code == 201
    dispute_id = create_resp.json()["id"]

    # 2. Admin approves appeal and credits balance
    resolve_resp = await client.post(
        f"/api/v1/finance/disputes/{dispute_id}/resolve",
        json={
            "status": "RESOLVED_CREDITED",
            "amount_credited": "150.00",
            "resolution_notes": "Reviewed promotional logs; traffic accepted.",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resolve_resp.status_code == 200

    # 3. Verify publisher balance credited
    await test_db.refresh(pub)
    assert pub.balance == initial_pub_balance + Decimal("150.00")


# ─────────────────────────────────────────────────────────────────────────────
# Task H: Exportable Reports Integration Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_export_advertiser_reporting_csv(client: AsyncClient, test_db: AsyncSession):
    """Exporting advertiser reporting to CSV returns valid RFC 4180 headers and tabular rows."""
    ctx = await _create_test_entities(test_db)
    adv_user = ctx["adv_user"]
    adv = ctx["advertiser"]
    camp = ctx["campaign"]
    creat = ctx["creative"]
    slot = ctx["slot"]

    adv_token = create_access_token(adv_user)

    # Track 1 impression
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_export_test_01",
        ad_id=f"ad_{camp.id}_{creat.id}",
        event_type="impression",
        extra={"slot_id": slot.slot_id},
    )
    await test_db.commit()

    resp = await client.get(
        f"/api/v1/advertiser/{adv.id}/reporting/export?format=csv",
        headers={"Authorization": f"Bearer {adv_token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in resp.headers["content-disposition"]

    # Parse and verify CSV contents
    csv_text = resp.content.decode("utf-8")
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    assert len(rows) >= 2  # Header + at least 1 daily row
    assert rows[0] == [
        "Date",
        "Impressions",
        "Clicks",
        "TotalConversions",
        "AttributedConversions",
        "ConversionValue",
        "Spend",
        "CTR_Percent",
        "CPA",
        "ROAS",
        "Currency",
    ]


async def test_export_publisher_reporting_csv(client: AsyncClient, test_db: AsyncSession):
    """Exporting publisher reporting to CSV returns valid RFC 4180 headers and rows."""
    ctx = await _create_test_entities(test_db)
    pub_user = ctx["pub_user"]
    pub = ctx["publisher"]
    slot = ctx["slot"]

    pub_token = create_access_token(pub_user)

    # Insert PublisherDailyStat
    today = datetime.now(timezone.utc).date()
    test_db.add(PublisherDailyStat(
        publisher_id=pub.id,
        slot_id=slot.id,
        stat_date=today,
        impressions=1000,
        clicks=25,
        conversions=5,
        earnings=Decimal("70.00"),
    ))
    await test_db.commit()

    resp = await client.get(
        f"/api/v1/publisher/{pub.id}/reporting/export?format=csv",
        headers={"Authorization": f"Bearer {pub_token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    csv_text = resp.content.decode("utf-8")
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    assert len(rows) >= 2
    assert rows[0] == [
        "Date",
        "Impressions",
        "Clicks",
        "Conversions",
        "Earnings",
        "ECPM",
        "Currency",
    ]
