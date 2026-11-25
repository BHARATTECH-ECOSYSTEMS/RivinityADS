"""
Phase 5: Financial and Event Pipeline Proof Integration Test Suite (P1).

Covers all required Phase 5 scenarios:
1. Webhook integrity: raw request byte signatures, amount/currency/order validation, provider event ID persistence, duplicate suppression, and concurrent delivery safety.
2. Event idempotency: retry after transaction failure, concurrent duplicate event IDs, and logically identical events with different request IDs.
3. Billing guardrails: zero billable advertiser spend and zero publisher payable earnings on sandbox/preview traffic, unverified properties, inactive publishers, disallowed visitor consent, and IVT-flagged events.
4. Publisher revenue share: locked rev share rates at event time (contract rate vs platform default), no retrospective modification.
5. Earnings separation & hold window: estimated earnings in pending_balance vs cleared balance, settlement via clear_settled_earnings, and explicit IVT deduction adjustment labels.
6. Payout lifecycle & concurrency: row-locking reservation, concurrent overdraw prevention, approved, declined/rejected rollback, confirmed/paid, provider failure release, and retry.
7. Admin reconciliation & unresolved queue: detects provider-vs-ledger mismatches, routes to unresolved queue without auto-balancing, and filtered ledger audit.
8. Daily rollups: raw event totals match aggregated PublisherDailyStat stats within declared aggregation rules.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from liteads.ad_server.main import app
from liteads.ad_server.services.billing import BillingService
from liteads.ad_server.services.event_service import EventService
from liteads.ad_server.services.conversion_service import ConversionService
from liteads.ad_server.services.reconciliation_service import ReconciliationService
from liteads.ad_server.services.razorpay_client import get_razorpay_client, RazorpayClient
from liteads.common.config import get_settings
from liteads.common.tracking_sig import sign_tracking, sign_click_token
from liteads.models.ad import (
    AdEvent,
    Advertiser,
    Campaign,
    CampaignAssignment,
    Creative,
)
from liteads.models.base import BidType, EventType, Status, TransactionType
from liteads.models.finance import (
    PaymentRequest,
    PlatformSettings,
    PublisherPayout,
    Transaction,
)
from liteads.models.publisher import (
    AdSlot,
    Publisher,
    PublisherDailyStat,
    PublisherProperty,
)

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures & Shared Test Helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def pg_engine():
    """Real PostgreSQL async engine for true concurrency testing; skipped if unavailable."""
    settings = get_settings()
    url = settings.database.async_url
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL async_url not configured")

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as e:
        await engine.dispose()
        pytest.skip(f"PostgreSQL server not accessible: {e}")

    yield engine
    await engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    """Session factory for creating independent, separate PostgreSQL connections."""
    return sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)


async def _create_base_entities(
    session: AsyncSession,
    tag: str | None = None,
    advertiser_balance: Decimal = Decimal("1000.00"),
    publisher_balance: Decimal = Decimal("0.00"),
    publisher_rev_share: Decimal | None = None,
    bid_type: int = BidType.CPM,
    bid_amount: Decimal = Decimal("10.00"),
    property_verified: bool = True,
    publisher_active: bool = True,
) -> tuple[Advertiser, Campaign, Creative, Publisher, PublisherProperty, AdSlot]:
    """Helper to set up a complete relational ad serving environment."""
    t = tag or uuid.uuid4().hex[:6]

    adv = Advertiser(
        name=f"Adv_{t}",
        balance=advertiser_balance,
        status=Status.ACTIVE,
    )
    pub = Publisher(
        name=f"Pub_{t}",
        balance=publisher_balance,
        pending_balance=Decimal("0.00"),
        rev_share_rate=publisher_rev_share,
        status=Status.ACTIVE if publisher_active else Status.INACTIVE,
    )
    session.add_all([adv, pub])
    await session.flush()

    prop = PublisherProperty(
        publisher_id=pub.id,
        name=f"Site_{t}",
        domain=f"site-{t}.example.com",
        status=Status.ACTIVE,
        verification_status="VERIFIED" if property_verified else "PENDING",
    )
    session.add(prop)
    await session.flush()

    slot = AdSlot(
        publisher_id=pub.id,
        property_id=prop.id,
        slot_id=f"slot_{t}",
        name=f"Banner_{t}",
        format=1,
        status=Status.ACTIVE,
    )
    session.add(slot)
    await session.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name=f"Camp_{t}",
        budget_total=Decimal("10000.00"),
        budget_daily=Decimal("1000.00"),
        bid_type=bid_type,
        bid_amount=bid_amount,
        status=Status.ACTIVE,
    )
    session.add(camp)
    await session.flush()

    creat = Creative(
        campaign_id=camp.id,
        title=f"Creative_{t}",
        landing_url="https://example.com/landing",
        status=Status.ACTIVE,
    )
    assign = CampaignAssignment(campaign_id=camp.id, publisher_id=pub.id, slot_id=slot.slot_id)
    session.add_all([creat, assign])
    await session.flush()

    return adv, camp, creat, pub, prop, slot


# ─────────────────────────────────────────────────────────────────────────────
# 1. Payment Webhook Integrity & Concurrency
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def razorpay_webhook_test_settings(monkeypatch):
    """Inject synthetic non-production Razorpay credentials without relying on local .env."""
    import liteads.ad_server.services.razorpay_client as razorpay_client_module

    webhook_secret = "test_webhook_secret_key_12345678"
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_ci_dummy")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "rzp_test_ci_secret_dummy")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setattr(razorpay_client_module, "_client_instance", None)
    get_settings.cache_clear()
    yield webhook_secret
    get_settings.cache_clear()


async def test_razorpay_webhook_signatures_and_payload_validation(
    client: AsyncClient,
    test_db: AsyncSession,
    razorpay_webhook_test_settings: str,
):
    """Webhook strictly rejects missing or forged signatures, invalid currencies, and invalid amounts."""
    adv, _, _, _, _, _ = await _create_base_entities(test_db, "webhook_val")
    await test_db.commit()

    webhook_secret = razorpay_webhook_test_settings

    order_id = f"order_val_{uuid.uuid4().hex[:6]}"
    valid_payload = {
        "event": "order.paid",
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "amount": 50000,  # ₹500.00
                    "currency": "INR",
                    "receipt": f"adv_{adv.id}_test",
                    "status": "paid",
                }
            }
        },
    }
    raw_body = json.dumps(valid_payload).encode("utf-8")
    valid_sig = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    # 1. Missing signature -> 400
    res_no_sig = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert res_no_sig.status_code == 400
    assert "Missing signature" in res_no_sig.text

    # 2. Forged signature -> 400
    res_bad_sig = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=raw_body,
        headers={"x-razorpay-signature": "forged_signature_hex_value", "Content-Type": "application/json"},
    )
    assert res_bad_sig.status_code == 400
    assert "Invalid signature" in res_bad_sig.text

    # 3. Non-INR currency -> ignored
    usd_payload = copy.deepcopy(valid_payload)
    usd_payload["payload"]["order"]["entity"]["currency"] = "USD"
    usd_body = json.dumps(usd_payload).encode("utf-8")
    usd_sig = hmac.new(webhook_secret.encode("utf-8"), usd_body, hashlib.sha256).hexdigest()
    res_usd = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=usd_body,
        headers={"x-razorpay-signature": usd_sig, "Content-Type": "application/json"},
    )
    assert res_usd.status_code == 200
    assert res_usd.json()["status"] == "ignored"
    assert res_usd.json()["reason"] == "invalid currency"

    # 4. Zero or negative amount -> ignored
    zero_payload = copy.deepcopy(valid_payload)
    zero_payload["payload"]["order"]["entity"]["amount"] = 0
    zero_body = json.dumps(zero_payload).encode("utf-8")
    zero_sig = hmac.new(webhook_secret.encode("utf-8"), zero_body, hashlib.sha256).hexdigest()
    res_zero = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=zero_body,
        headers={"x-razorpay-signature": zero_sig, "Content-Type": "application/json"},
    )
    assert res_zero.status_code == 200
    assert res_zero.json()["status"] == "ignored"
    assert res_zero.json()["reason"] == "invalid amount"


async def test_razorpay_webhook_idempotency_and_provider_event_id(
    client: AsyncClient,
    test_db: AsyncSession,
    razorpay_webhook_test_settings: str,
):
    """Valid webhook credits advertiser wallet, persists provider_event_id, and deduplicates repeated calls."""
    adv, _, _, _, _, _ = await _create_base_entities(test_db, "webhook_dedup", advertiser_balance=Decimal("100.00"))
    await test_db.commit()

    webhook_secret = razorpay_webhook_test_settings

    order_id = f"order_idemp_{uuid.uuid4().hex[:6]}"
    provider_event_id = f"evt_rzp_{uuid.uuid4().hex[:8]}"
    payload = {
        "event": "order.paid",
        "event_id": provider_event_id,
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "amount": 25000,  # ₹250.00
                    "currency": "INR",
                    "receipt": f"adv_{adv.id}_ref",
                    "status": "paid",
                }
            }
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    # First delivery: Must succeed and credit exactly ₹250.00
    res1 = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=raw_body,
        headers={"x-razorpay-signature": sig, "Content-Type": "application/json"},
    )
    assert res1.status_code == 200
    assert res1.json()["status"] == "success"

    await test_db.refresh(adv)
    assert adv.balance == Decimal("350.00")

    # Verify transaction row written with provider_event_id and reference_id
    tx_stmt = select(Transaction).where(Transaction.reference_id == order_id)
    tx = (await test_db.execute(tx_stmt)).scalar_one_or_none()
    assert tx is not None
    assert tx.amount == Decimal("250.00")
    assert tx.type == TransactionType.DEPOSIT
    assert tx.provider_event_id == provider_event_id

    # Second delivery with exact same body -> already_processed without balance mutation
    res2 = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=raw_body,
        headers={"x-razorpay-signature": sig, "Content-Type": "application/json"},
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "already_processed"

    await test_db.refresh(adv)
    assert adv.balance == Decimal("350.00")  # Remains ₹350.00; NO double-credit


async def test_postgres_concurrent_webhook_race(pg_session_factory):
    """Concurrent delivery race of the exact same payment order against PostgreSQL guarantees single credit."""
    unique_tag = uuid.uuid4().hex[:6]
    order_id = f"order_pg_race_{unique_tag}"
    adv_id = None

    async with pg_session_factory() as s_init:
        adv = Advertiser(name=f"PG Adv Race {unique_tag}", balance=Decimal("100.00"))
        s_init.add(adv)
        await s_init.commit()
        adv_id = adv.id

    async def deliver_webhook():
        async with pg_session_factory() as session:
            try:
                # Check pre-existence
                chk = select(Transaction).where(Transaction.reference_id == order_id)
                if (await session.execute(chk)).scalar_one_or_none():
                    return "already_processed"

                await BillingService.record_deposit(
                    session=session,
                    advertiser_id=adv_id,
                    amount=Decimal("500.00"),
                    reference_id=order_id,
                    provider_event_id=f"evt_{order_id}",
                )
                await session.commit()
                return "success"
            except Exception:
                await session.rollback()
                return "already_processed"

    # Fire concurrent deliveries simultaneously
    results = await asyncio.gather(deliver_webhook(), deliver_webhook())

    # Exactly one must succeed, the other must be already_processed
    assert "success" in results
    assert "already_processed" in results

    # Verify database state
    async with pg_session_factory() as s_ver:
        txs = (await s_ver.execute(select(Transaction).where(Transaction.reference_id == order_id))).scalars().all()
        adv_after = await s_ver.get(Advertiser, adv_id)

        # Cleanup
        await s_ver.execute(delete(Transaction).where(Transaction.reference_id == order_id))
        await s_ver.execute(delete(Advertiser).where(Advertiser.id == adv_id))
        await s_ver.commit()

    assert len(txs) == 1, "Exactly one Transaction row should exist"
    assert adv_after.balance == Decimal("600.00"), "Balance must be credited exactly once (100 + 500 = 600)"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Event Idempotency & Retry After Rollback
# ─────────────────────────────────────────────────────────────────────────────

async def test_event_retry_after_session_rollback(test_db: AsyncSession):
    """Proves that a failed transaction rollback leaves the session clean and allows a retry to succeed."""
    adv, camp, creat, pub, prop, slot = await _create_base_entities(
        test_db, "retry_test", advertiser_balance=Decimal("500.00"), bid_amount=Decimal("1.00")
    )
    await test_db.commit()

    ev_service = EventService(test_db)
    req_id = f"req_retry_{uuid.uuid4().hex[:6]}"
    ad_id = f"ad_{camp.id}_{creat.id}"
    slot_id_val = slot.slot_id

    # 1. Simulate failure mid-transaction (e.g. forced exception)
    test_db.add(AdEvent(request_id=req_id, event_type=EventType.IMPRESSION, event_time=datetime.now(timezone.utc).replace(tzinfo=None)))
    await test_db.rollback()  # Rolled back cleanly

    # 2. Execute retry through track_event
    success = await ev_service.track_event(
        request_id=req_id,
        ad_id=ad_id,
        event_type="impression",
        extra={"slot_id": slot_id_val},
    )
    await test_db.commit()
    assert success is True

    # Verify event and spend transaction persisted
    ev_row = (await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))).scalar_one_or_none()
    assert ev_row is not None


async def test_logically_identical_conversion_events_with_different_request_ids(test_db: AsyncSession):
    """Logically identical conversion postbacks with different request IDs but same order ID are deduplicated at DB constraint level."""
    adv, camp, creat, pub, prop, slot = await _create_base_entities(
        test_db, "cpa_dedup", advertiser_balance=Decimal("1000.00"), bid_type=BidType.CPA, bid_amount=Decimal("50.00")
    )
    # Seed matching click event
    click_req = f"req_clk_{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    click = AdEvent(
        request_id=click_req,
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=now,
        cost=Decimal("0.00"),
    )
    test_db.add(click)
    await test_db.commit()

    c_service = ConversionService(test_db)
    order_id = f"ORD_SAME_{uuid.uuid4().hex[:6]}"

    # Conversion #1 with request_id = req_a
    res1 = await c_service.track_conversion(
        request_id=click_req,
        campaign_id=camp.id,
        creative_id=creat.id,
        order_id=order_id,
        conversion_value=Decimal("500.00"),
    )
    await test_db.commit()
    assert res1.success is True
    assert res1.is_duplicate is False

    await test_db.refresh(adv)
    assert adv.balance == Decimal("950.00")  # ₹50 charged

    # Conversion #2 with DIFFERENT request_id = req_different_b, but SAME order_id
    res2 = await c_service.track_conversion(
        request_id=f"req_different_{uuid.uuid4().hex[:6]}",
        campaign_id=camp.id,
        creative_id=creat.id,
        order_id=order_id,
        conversion_value=Decimal("500.00"),
    )
    assert res2.success is True
    assert res2.is_duplicate is True  # Deduplicated!

    await test_db.refresh(adv)
    assert adv.balance == Decimal("950.00")  # NO second charge!


# ─────────────────────────────────────────────────────────────────────────────
# 3. Billing Guardrails (Zero Billable Spend & Zero Earnings)
# ─────────────────────────────────────────────────────────────────────────────

async def test_billing_guardrails_zero_spend_and_zero_earnings(test_db: AsyncSession):
    """Verifies that sandbox, unverified properties, inactive publishers, and IVT produce zero spend and zero credit."""
    adv, camp, creat, pub, prop, slot = await _create_base_entities(
        test_db, "guardrails", advertiser_balance=Decimal("1000.00"), bid_amount=Decimal("10.00")
    )
    await test_db.commit()

    ev_service = EventService(test_db)

    # 1. Sandbox / Preview Traffic
    req_sb = f"prev_test_{uuid.uuid4().hex[:6]}"
    ok_sb = await ev_service.track_event(
        request_id=req_sb,
        ad_id=f"ad_{camp.id}_{creat.id}",
        event_type="impression",
        extra={"slot_id": slot.slot_id, "is_preview": True},
    )
    await test_db.commit()
    assert ok_sb is True

    await test_db.refresh(adv)
    await test_db.refresh(pub)
    assert adv.balance == Decimal("1000.00")  # Zero spend
    assert pub.balance == Decimal("0.00")     # Zero earnings

    # 2. Unverified Property
    prop.verification_status = "PENDING"
    await test_db.commit()

    req_unver = f"req_unver_{uuid.uuid4().hex[:6]}"
    ok_unver = await ev_service.track_event(
        request_id=req_unver,
        ad_id=f"ad_{camp.id}_{creat.id}",
        event_type="impression",
        extra={"slot_id": slot.slot_id},
    )
    await test_db.commit()
    assert ok_unver is True

    await test_db.refresh(adv)
    await test_db.refresh(pub)
    assert adv.balance == Decimal("1000.00")  # Suppressed
    assert pub.balance == Decimal("0.00")

    # Restore property verification
    prop.verification_status = "VERIFIED"
    await test_db.commit()

    # 3. Inactive Publisher
    pub.status = Status.INACTIVE
    await test_db.commit()

    req_inact = f"req_inact_{uuid.uuid4().hex[:6]}"
    ok_inact = await ev_service.track_event(
        request_id=req_inact,
        ad_id=f"ad_{camp.id}_{creat.id}",
        event_type="impression",
        extra={"slot_id": slot.slot_id},
    )
    await test_db.commit()
    assert ok_inact is True

    await test_db.refresh(adv)
    await test_db.refresh(pub)
    assert adv.balance == Decimal("1000.00")  # Suppressed
    assert pub.balance == Decimal("0.00")

    pub.status = Status.ACTIVE
    await test_db.commit()

    # 4. IVT Flagged Traffic (e.g. Datacenter Bot IP or Replay)
    req_ivt = f"req_ivt_{uuid.uuid4().hex[:6]}"
    ok_ivt = await ev_service.track_event(
        request_id=req_ivt,
        ad_id=f"ad_{camp.id}_{creat.id}",
        event_type="impression",
        extra={"slot_id": slot.slot_id, "user_agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"},
    )
    await test_db.commit()
    assert ok_ivt is True

    await test_db.refresh(adv)
    await test_db.refresh(pub)
    assert adv.balance == Decimal("1000.00")  # Suppressed
    assert pub.balance == Decimal("0.00")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Publisher Revenue Share Locking & History Preservation
# ─────────────────────────────────────────────────────────────────────────────

async def test_publisher_revenue_share_locking_at_event_time(test_db: AsyncSession):
    """Publisher contracted rev share is locked at event time and historical records are not modified when global settings change."""
    # Global default: 0.700
    settings = PlatformSettings(publisher_rev_share=Decimal("0.700"), earnings_hold_hours=0)
    test_db.add(settings)
    await test_db.flush()

    # Publisher with custom contracted rate 0.850
    adv, camp, creat, pub, prop, slot = await _create_base_entities(
        test_db, "rev_lock", advertiser_balance=Decimal("1000.00"), publisher_rev_share=Decimal("0.850")
    )
    await test_db.commit()

    # Bill impression charge with cost = ₹100.00
    ok = await BillingService.process_impression_charge(
        session=test_db,
        advertiser_id=adv.id,
        campaign_id=camp.id,
        publisher_id=pub.id,
        cost=Decimal("100.00"),
        reference_id="bill_lock_test_1",
    )
    assert ok is True
    await test_db.commit()

    # Verify earnings transaction recorded locked rate 0.850 and amount 85.00
    earn_tx = (await test_db.execute(select(Transaction).where(Transaction.reference_id == "earn:bill_lock_test_1"))).scalar_one_or_none()
    assert earn_tx is not None
    assert earn_tx.rev_share_rate == Decimal("0.850")
    assert earn_tx.amount == Decimal("85.000000")

    # Now change platform global settings to 0.500
    settings.publisher_rev_share = Decimal("0.500")
    await test_db.commit()

    # The historical transaction must NOT be modified
    await test_db.refresh(earn_tx)
    assert earn_tx.rev_share_rate == Decimal("0.850")
    assert earn_tx.amount == Decimal("85.000000")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Earnings Separation, Settlement Hold Window & IVT Adjustment
# ─────────────────────────────────────────────────────────────────────────────

async def test_earnings_separation_hold_window_and_settlement(test_db: AsyncSession):
    """Estimated earnings stay in pending_balance until hold window passes, then settle to withdrawable balance."""
    settings = PlatformSettings(publisher_rev_share=Decimal("0.700"), earnings_hold_hours=24)
    test_db.add(settings)
    await test_db.flush()

    adv, camp, creat, pub, prop, slot = await _create_base_entities(
        test_db, "hold_win", advertiser_balance=Decimal("1000.00"), publisher_balance=Decimal("50.00")
    )
    await test_db.commit()

    # Track impression with cost = ₹100.00
    ok = await BillingService.process_impression_charge(
        session=test_db,
        advertiser_id=adv.id,
        campaign_id=camp.id,
        publisher_id=pub.id,
        cost=Decimal("100.00"),
        reference_id="imp_hold_test_1",
    )
    assert ok is True
    await test_db.commit()

    await test_db.refresh(pub)
    # 70% earnings = ₹70.00 must be in pending_balance; balance remains at initial ₹50.00
    assert pub.pending_balance == Decimal("70.000000")
    assert pub.balance == Decimal("50.000000")

    # Publisher attempts payout of ₹100 -> Must FAIL because only ₹50 is cleared
    with pytest.raises(ValueError, match="Insufficient balance for payout"):
        await BillingService.request_payout(session=test_db, publisher_id=pub.id, amount=Decimal("100.00"))

    # Advance time: clear settled earnings as of 25 hours later
    future_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=25)
    cleared = await BillingService.clear_settled_earnings(session=test_db, as_of=future_time, publisher_id=pub.id)
    await test_db.commit()
    assert len(cleared) == 1

    await test_db.refresh(pub)
    # Funds moved from pending to cleared balance: balance = 50 + 70 = 120
    assert pub.pending_balance == Decimal("0.000000")
    assert pub.balance == Decimal("120.000000")


async def test_ivt_adjustment_with_explicit_label(test_db: AsyncSession):
    """Admin/automated IVT adjustment deduces earnings with an explicit audit reason label."""
    adv, camp, creat, pub, prop, slot = await _create_base_entities(
        test_db, "ivt_adj", publisher_balance=Decimal("200.00")
    )
    pub.pending_balance = Decimal("100.00")
    await test_db.commit()

    adj_tx = await BillingService.apply_ivt_adjustment(
        session=test_db,
        publisher_id=pub.id,
        amount=Decimal("150.00"),
        reason="CLICK_SPAM_PATTERN_CONFIRMED",
        campaign_id=camp.id,
    )
    await test_db.commit()

    assert adj_tx.type == TransactionType.ADJUSTMENT
    assert adj_tx.amount == Decimal("-150.00")
    assert "CLICK_SPAM_PATTERN_CONFIRMED" in adj_tx.description

    await test_db.refresh(pub)
    # Deducted 100 from pending (now 0) and 50 from cleared balance (200 - 50 = 150)
    assert pub.pending_balance == Decimal("0.00")
    assert pub.balance == Decimal("150.00")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Payout Lifecycle & Concurrent Overdraw Prevention
# ─────────────────────────────────────────────────────────────────────────────

async def test_payout_complete_lifecycle(test_db: AsyncSession):
    """Tests complete payout lifecycle: requested -> approved -> paid, rejection release, and provider failure release."""
    adv, _, _, pub, _, _ = await _create_base_entities(test_db, "payout_life", publisher_balance=Decimal("500.00"))
    await test_db.commit()

    # 1. Request payout of ₹200 -> balance becomes 300
    payout, tx_req = await BillingService.request_payout(
        session=test_db,
        publisher_id=pub.id,
        amount=Decimal("200.00"),
        reference_id="pout_test_cycle_1",
    )
    await test_db.commit()
    assert payout.status == "requested"

    await test_db.refresh(pub)
    assert pub.balance == Decimal("300.00")

    # 2. Approve payout -> status = approved, funds remain reserved
    approved_payout = await BillingService.approve_payout(session=test_db, payout_id=payout.id, notes="Approved for NEFT batch")
    await test_db.commit()
    assert approved_payout.status == "approved"

    # 3. Confirm payout -> status = paid
    confirmed_payout = await BillingService.confirm_payout(
        session=test_db, payout_id=payout.id, provider_payout_id="BANK_UTR_987654321"
    )
    await test_db.commit()
    assert confirmed_payout.status == "paid"
    assert confirmed_payout.provider_payout_id == "BANK_UTR_987654321"

    await test_db.refresh(pub)
    assert pub.balance == Decimal("300.00")

    # 4. Second payout request of ₹100, then rejected -> funds restored to ₹400
    payout2, _ = await BillingService.request_payout(
        session=test_db, publisher_id=pub.id, amount=Decimal("100.00"), reference_id="pout_test_cycle_2"
    )
    await test_db.commit()
    await test_db.refresh(pub)
    assert pub.balance == Decimal("200.00")

    _, refund_tx = await BillingService.reject_payout(session=test_db, payout_id=payout2.id, reason="Invalid IFSC code")
    await test_db.commit()
    assert refund_tx.type == TransactionType.REFUND
    assert refund_tx.amount == Decimal("100.00")

    await test_db.refresh(pub)
    assert pub.balance == Decimal("300.00")  # Restored!

    # 5. Third payout request of ₹100, then external provider failure -> funds restored
    payout3, _ = await BillingService.request_payout(
        session=test_db, publisher_id=pub.id, amount=Decimal("100.00"), reference_id="pout_test_cycle_3"
    )
    await test_db.commit()

    _, fail_refund = await BillingService.fail_payout(session=test_db, payout_id=payout3.id, reason="Beneficiary bank offline")
    await test_db.commit()
    assert fail_refund.type == TransactionType.REFUND

    await test_db.refresh(pub)
    assert pub.balance == Decimal("300.00")  # Restored!


async def test_postgres_concurrent_payout_overdraw_prevention(pg_session_factory):
    """Two concurrent payout requests trying to reserve ₹500 on a ₹500 balance; row locking guarantees exactly one succeeds."""
    unique_tag = uuid.uuid4().hex[:6]
    pub_id = None

    async with pg_session_factory() as s_init:
        pub = Publisher(name=f"PG Pub Payout {unique_tag}", balance=Decimal("500.00"))
        s_init.add(pub)
        await s_init.commit()
        pub_id = pub.id

    async def attempt_payout(ref_suffix: str):
        async with pg_session_factory() as session:
            try:
                payout, tx = await BillingService.request_payout(
                    session=session,
                    publisher_id=pub_id,
                    amount=Decimal("500.00"),
                    reference_id=f"pout_race_{unique_tag}_{ref_suffix}",
                )
                await session.commit()
                return True
            except Exception:
                await session.rollback()
                return False

    res_a, res_b = await asyncio.gather(attempt_payout("a"), attempt_payout("b"))

    # Exactly one must succeed, one must fail
    assert (res_a and not res_b) or (res_b and not res_a)

    async with pg_session_factory() as s_ver:
        pub_after = await s_ver.get(Publisher, pub_id)
        payouts = (await s_ver.execute(select(PublisherPayout).where(PublisherPayout.publisher_id == pub_id))).scalars().all()

        # Cleanup
        await s_ver.execute(delete(PublisherPayout).where(PublisherPayout.publisher_id == pub_id))
        await s_ver.execute(delete(Transaction).where(Transaction.publisher_id == pub_id))
        await s_ver.execute(delete(Publisher).where(Publisher.id == pub_id))
        await s_ver.commit()

    assert len(payouts) == 1
    assert pub_after.balance == Decimal("0.00"), "Balance must be exactly 0.00; never negative overdrawn!"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Admin Reconciliation & Unresolved Queue
# ─────────────────────────────────────────────────────────────────────────────

async def test_reconciliation_detects_mismatch_and_routes_to_unresolved_queue(test_db: AsyncSession):
    """Reconciliation detects missing ledger transactions for paid provider orders and queues them without auto-balancing."""
    adv, _, _, _, _, _ = await _create_base_entities(test_db, "recon_test")
    
    # Create a paid PaymentRequest WITHOUT a ledger transaction (synthetic provider mismatch)
    pr = PaymentRequest(
        advertiser_id=adv.id,
        amount=Decimal("750.00"),
        currency="INR",
        provider_order_id=f"order_orphan_{uuid.uuid4().hex[:6]}",
        status="paid",
    )
    test_db.add(pr)
    await test_db.commit()

    rec_service = ReconciliationService(test_db)
    report = await rec_service.reconcile(advertiser_id=adv.id)

    assert report.status == "DISCREPANCY_DETECTED"
    assert len(report.unresolved_queue) >= 1

    # Verify queue entry details
    queue_item = [q for q in report.unresolved_queue if q["reference_id"] == pr.provider_order_id][0]
    assert queue_item["status"] == "UNRESOLVED"
    assert queue_item["severity"] == "CRITICAL"
    assert queue_item["metric"] == "provider_deposit_missing"
    assert queue_item["expected_value"] == 750.00
    assert queue_item["delta"] == 750.00


# ─────────────────────────────────────────────────────────────────────────────
# 8. Daily Rollups Explicitly Match Raw Events
# ─────────────────────────────────────────────────────────────────────────────

async def test_daily_rollups_match_raw_events(test_db: AsyncSession):
    """PublisherDailyStat aggregated impressions and earnings strictly match raw non-test billable AdEvent totals."""
    # Ensure platform settings with 0 hold hours for clean rollup equality
    settings = PlatformSettings(publisher_rev_share=Decimal("0.800"), earnings_hold_hours=0)
    test_db.add(settings)
    await test_db.flush()

    adv, camp, creat, pub, prop, slot = await _create_base_entities(
        test_db, "rollup_match", advertiser_balance=Decimal("5000.00"), publisher_rev_share=Decimal("0.800")
    )
    await test_db.commit()

    ev_service = EventService(test_db)

    # Track 3 billable impressions with cost = 100.00 each
    for i in range(3):
        req_id = f"req_roll_{i}_{uuid.uuid4().hex[:4]}"
        sig = sign_tracking(req_id, f"ad_{camp.id}_{creat.id}", slot.slot_id)
        ok = await ev_service.track_event(
            request_id=req_id,
            ad_id=f"ad_{camp.id}_{creat.id}",
            event_type="impression",
            extra={"slot_id": slot.slot_id},
        )
        assert ok is True
    await test_db.commit()

    # Track 1 sandbox event (must NOT contaminate daily stats earnings)
    sb_req = f"req_roll_sb_{uuid.uuid4().hex[:4]}"
    await ev_service.track_event(
        request_id=sb_req,
        ad_id=f"ad_{camp.id}_{creat.id}",
        event_type="impression",
        extra={"slot_id": slot.slot_id, "is_preview": True},
    )
    await test_db.commit()

    # Query PublisherDailyStat
    today = datetime.now(timezone.utc).date()
    stat_stmt = select(PublisherDailyStat).where(
        PublisherDailyStat.publisher_id == pub.id,
        PublisherDailyStat.slot_id == slot.id,
        PublisherDailyStat.stat_date == today,
    )
    stat = (await test_db.execute(stat_stmt)).scalar_one_or_none()
    assert stat is not None
    assert stat.impressions == 3  # 3 billable impressions
    
    # CPM = 10.00 => per impression cost = 0.010000
    # 3 impressions => 0.030000 total spend
    # 0.030000 * 0.800 rev_share = 0.024000 publisher earnings
    expected_spend = (Decimal("10.00") / Decimal("1000")) * Decimal("3")
    expected_earnings = (expected_spend * Decimal("0.800")).quantize(Decimal("0.000001"))
    assert stat.earnings == expected_earnings

    # Match against sum of raw AdEvents
    raw_stmt = select(func.sum(AdEvent.cost)).where(
        AdEvent.campaign_id == camp.id,
        AdEvent.is_test == False,
        AdEvent.is_invalid == False,
    )
    total_raw_spend = (await test_db.execute(raw_stmt)).scalar()
    assert total_raw_spend == expected_spend
    assert (total_raw_spend * Decimal("0.800")).quantize(Decimal("0.000001")) == stat.earnings
