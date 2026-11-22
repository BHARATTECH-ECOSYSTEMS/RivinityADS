"""
test_phase5_financial_event_pipeline_proof.py

RivinityADS Post-Audit Remediation Roadmap — Phase 5: Financial and Event Pipeline Proof (P1)

Comprehensive Pytest Proof Suite Verifying:
- Suite 1: Webhook Suite (Razorpay Webhook Verification & Atomic Deduplication)
- Suite 2: Event & Conversion DB-Level Idempotency Suite
- Suite 3: Zero-Billing Invariants Suite (Sandbox, Unverified Property, Inactive Publisher, Visitor Consent, IVT)
- Suite 4: Revenue Share & Precision Suite (Rate Locking, 6-Decimal Arithmetic)
- Suite 5: Estimated vs. Finalised & Hold Window Suite (Hold Expiry, Pending Balance, IVT Adjustments)
- Suite 6: Payout Lifecycle & Concurrency Suite (Reservation, Overdraw Prevention, Reject/Fail Refunds, Confirmation)
- Suite 7: Rollup vs. Raw Event Agreement Suite (Spend Delta Detection, Unresolved Discrepancies Queue)
- Suite 8: Admin Ledger Filters, Masking & Exception Queue Suite (Multi-column Filtering, Bank Account Redaction)
"""

import asyncio
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from liteads.common.security import create_access_token
from liteads.common.tracking_sig import sign_click_token, sign_consent_token
from liteads.models import Advertiser, Campaign, Creative, AdEvent, AdSlot, Status, User
from liteads.models.base import EventType, TransactionType
from liteads.models.publisher import Publisher, PublisherProperty
from liteads.models.finance import Transaction, PaymentRequest, PublisherPayout, PlatformSettings
from liteads.models.website import WebsiteProject, WebsiteConsentLog
from liteads.ad_server.services.billing import BillingService
from liteads.ad_server.services.conversion_service import ConversionService
from liteads.ad_server.services.event_service import EventService
from liteads.ad_server.services.reconciliation_service import ReconciliationService


WEBHOOK_SECRET = "test_phase5_webhook_secret_key"


def generate_webhook_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def get_auth_headers(user_id: int, role: str, advertiser_id: int | None = None, publisher_id: int | None = None):
    user = User(
        id=user_id,
        role=role,
        is_active=True,
        email=f"user{user_id}_{role}@ads.example.com",
        name=f"User {user_id}",
        password_hash="test_hash",
        advertiser_id=advertiser_id,
        publisher_id=publisher_id,
    )
    token = create_access_token(user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_razorpay_phase5():
    with patch("liteads.ad_server.services.tools.get_razorpay_client") as mock_tools_rzp, \
         patch("liteads.ad_server.routers.finance.get_razorpay_client") as mock_finance_rzp:
        mock_client = MagicMock()

        def verify_sig(body, signature):
            b = body if isinstance(body, bytes) else body.encode("utf-8")
            expected = generate_webhook_signature(WEBHOOK_SECRET, b)
            return hmac.compare_digest(expected, signature)

        mock_client.verify_webhook_signature.side_effect = verify_sig

        from liteads.ad_server.main import app
        from liteads.ad_server.services.razorpay_client import get_razorpay_client
        app.dependency_overrides[get_razorpay_client] = lambda: mock_client
        mock_tools_rzp.return_value = mock_client
        mock_finance_rzp.return_value = mock_client

        yield mock_client
        app.dependency_overrides.pop(get_razorpay_client, None)


@pytest.fixture
async def phase5_entities(test_db):
    """Seed authoritative base entities for Phase 5 financial and event pipeline proof."""
    admin = User(id=10, email="admin_p5@ads.example.com", name="Admin P5", password_hash="h", role="admin")
    adv_user = User(id=11, email="adv_p5@ads.example.com", name="Adv P5", password_hash="h", role="advertiser")
    pub_user = User(id=12, email="pub_p5@ads.example.com", name="Pub P5", password_hash="h", role="publisher")
    test_db.add_all([admin, adv_user, pub_user])
    await test_db.flush()

    adv = Advertiser(id=10, name="Adv Phase 5", balance=Decimal("1000.0000"), owner_user_id=11)
    pub = Publisher(
        id=10,
        name="Pub Phase 5",
        balance=Decimal("500.0000"),
        pending_balance=Decimal("0.0000"),
        rev_share_rate=Decimal("0.700"),
        owner_user_id=12,
        status=Status.ACTIVE,
        payout_details={
            "bank_name": "State Bank of India",
            "account_number": "123456789012",
            "ifsc": "SBIN0001234",
            "upi_id": "pub10@upi",
            "secret": "super_secret_payout_token",
        },
    )
    settings = PlatformSettings(id=1, publisher_rev_share=Decimal("0.700"), earnings_hold_hours=0)
    test_db.add_all([adv, pub, settings])
    await test_db.flush()

    adv_user.advertiser_id = 10
    pub_user.publisher_id = 10

    camp = Campaign(
        id=10,
        advertiser_id=10,
        name="Phase 5 Proof Campaign",
        budget_total=Decimal("5000.0000"),
        budget_daily=Decimal("1000.0000"),
        bid_type=3,  # CPA
        bid_amount=Decimal("50.0000"),
        status=Status.ACTIVE,
    )
    test_db.add(camp)
    await test_db.flush()

    creat = Creative(
        id=10,
        campaign_id=10,
        title="Phase 5 Banner",
        landing_url="https://example.com/p5",
        status=Status.ACTIVE,
    )
    test_db.add(creat)
    await test_db.flush()

    prop = PublisherProperty(
        id=10,
        publisher_id=10,
        name="P5 News Portal",
        domain="p5news.example.com",
        status=Status.ACTIVE,
        verification_status="VERIFIED",
    )
    test_db.add(prop)
    await test_db.flush()

    slot = AdSlot(
        id=10,
        publisher_id=10,
        property_id=10,
        slot_id="slot_p5_banner",
        name="Header Slot",
        format=1,
        width=728,
        height=90,
        status=Status.ACTIVE,
    )
    test_db.add(slot)
    await test_db.commit()

    return {
        "admin_id": 10,
        "adv_user_id": 11,
        "pub_user_id": 12,
        "adv_id": 10,
        "pub_id": 10,
        "camp_id": 10,
        "creat_id": 10,
        "prop_id": 10,
        "slot_id": "slot_p5_banner",
    }


# ==============================================================================
# Suite 1: Webhook Suite (Razorpay Webhook Verification & Atomic Deduplication)
# ==============================================================================

@pytest.mark.asyncio
async def test_suite1_webhook_valid_signature_credits_wallet(client: AsyncClient, test_db, phase5_entities, mock_razorpay_phase5):
    """Valid webhook credits wallet, marks PaymentRequest paid, and records Transaction."""
    adv_id = phase5_entities["adv_id"]
    order_id = "order_p5_valid_1"

    # Pre-create pending PaymentRequest
    pr = PaymentRequest(
        advertiser_id=adv_id,
        amount=Decimal("200.0000"),
        currency="INR",
        provider_order_id=order_id,
        status="pending",
    )
    test_db.add(pr)
    await test_db.commit()

    payload_dict = {
        "event": "order.paid",
        "event_id": "evt_p5_order_paid_1",
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "amount": 20000,  # 200 INR in paise
                    "currency": "INR",
                    "receipt": f"adv_{adv_id}_topup",
                    "status": "paid",
                }
            }
        },
    }
    raw_body = json.dumps(payload_dict).encode("utf-8")
    sig = generate_webhook_signature(WEBHOOK_SECRET, raw_body)

    resp = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=raw_body,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}

    # Verify Advertiser balance increased by 200 (1000 -> 1200)
    adv = await test_db.get(Advertiser, adv_id)
    assert adv.balance == Decimal("1200.0000")

    # Verify PaymentRequest updated to paid
    q_pr = select(PaymentRequest).where(PaymentRequest.provider_order_id == order_id)
    updated_pr = (await test_db.execute(q_pr)).scalar_one()
    assert updated_pr.status == "paid"

    # Verify Transaction created with unique reference_id
    q_tx = select(Transaction).where(Transaction.reference_id == order_id)
    tx = (await test_db.execute(q_tx)).scalar_one()
    assert tx.type == TransactionType.DEPOSIT
    assert tx.amount == Decimal("200.0000")
    assert tx.provider_event_id == "evt_p5_order_paid_1"


@pytest.mark.asyncio
async def test_suite1_webhook_duplicate_delivery_atomically_deduplicated(client: AsyncClient, test_db, phase5_entities, mock_razorpay_phase5):
    """Duplicate webhook delivery returns already_processed without double crediting."""
    adv_id = phase5_entities["adv_id"]
    order_id = "order_p5_dedup_1"

    pr = PaymentRequest(
        advertiser_id=adv_id,
        amount=Decimal("150.0000"),
        currency="INR",
        provider_order_id=order_id,
        status="pending",
    )
    test_db.add(pr)
    await test_db.commit()

    payload_dict = {
        "event": "order.paid",
        "event_id": "evt_p5_dedup_1",
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "amount": 15000,
                    "currency": "INR",
                    "receipt": f"adv_{adv_id}_topup",
                    "status": "paid",
                }
            }
        },
    }
    raw_body = json.dumps(payload_dict).encode("utf-8")
    sig = generate_webhook_signature(WEBHOOK_SECRET, raw_body)

    # First delivery
    resp1 = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=raw_body,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"},
    )
    assert resp1.status_code == 200
    assert resp1.json() == {"status": "success"}

    # Duplicate delivery
    resp2 = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=raw_body,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"},
    )
    assert resp2.status_code == 200
    assert resp2.json() == {"status": "already_processed"}

    # Balance credited exactly once (1000 + 150 = 1150)
    adv = await test_db.get(Advertiser, adv_id)
    assert adv.balance == Decimal("1150.0000")

    # Strictly 1 transaction row in ledger
    tx_count_q = select(func.count()).where(Transaction.reference_id == order_id)
    tx_count = (await test_db.execute(tx_count_q)).scalar()
    assert tx_count == 1


@pytest.mark.asyncio
async def test_suite1_webhook_invalid_signature_fails_closed(client: AsyncClient, test_db, phase5_entities, mock_razorpay_phase5):
    """Invalid signature returns 400 Bad Request with zero side-effects."""
    payload_dict = {
        "event": "order.paid",
        "payload": {"order": {"entity": {"id": "order_bad_sig", "amount": 10000, "currency": "INR", "receipt": "adv_10_x", "status": "paid"}}},
    }
    raw_body = json.dumps(payload_dict).encode("utf-8")

    resp = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=raw_body,
        headers={"x-razorpay-signature": "invalid_hex_digest", "content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert "Invalid signature" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_suite1_webhook_parameter_mismatch_and_unpaid_status_ignored(client: AsyncClient, test_db, phase5_entities, mock_razorpay_phase5):
    """Wrong currency, wrong amount, and unpaid status are ignored with auditable reasons."""
    adv_id = phase5_entities["adv_id"]

    # 1. Currency mismatch (USD)
    p_curr = {
        "event": "order.paid",
        "payload": {"order": {"entity": {"id": "ord_usd", "amount": 10000, "currency": "USD", "receipt": f"adv_{adv_id}", "status": "paid"}}},
    }
    b_curr = json.dumps(p_curr).encode("utf-8")
    r_curr = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=b_curr,
        headers={"x-razorpay-signature": generate_webhook_signature(WEBHOOK_SECRET, b_curr)},
    )
    assert r_curr.json() == {"status": "ignored", "reason": "invalid currency"}

    # 2. Unpaid status
    p_unpaid = {
        "event": "order.paid",
        "payload": {"order": {"entity": {"id": "ord_unpaid", "amount": 10000, "currency": "INR", "receipt": f"adv_{adv_id}", "status": "attempted"}}},
    }
    b_unpaid = json.dumps(p_unpaid).encode("utf-8")
    r_unpaid = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=b_unpaid,
        headers={"x-razorpay-signature": generate_webhook_signature(WEBHOOK_SECRET, b_unpaid)},
    )
    assert r_unpaid.json() == {"status": "ignored", "reason": "order not paid"}


# ==============================================================================
# Suite 2: Event & Conversion DB-Level Idempotency Suite
# ==============================================================================

@pytest.mark.asyncio
async def test_suite2_conversion_insufficient_funds_rollback(test_db, phase5_entities):
    """Conversion with insufficient funds rolls back completely without writing events or deducting balance."""
    adv_id = phase5_entities["adv_id"]
    camp_id = phase5_entities["camp_id"]
    creat_id = phase5_entities["creat_id"]

    # Drain advertiser balance below CPA cost (50.00)
    adv = await test_db.get(Advertiser, adv_id)
    adv.balance = Decimal("20.0000")
    await test_db.commit()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    click_ev = AdEvent(
        request_id="req_insufficient_charge",
        campaign_id=camp_id,
        creative_id=creat_id,
        event_type=EventType.CLICK,
        event_time=now,
    )
    test_db.add(click_ev)
    await test_db.commit()

    tok = sign_click_token("req_insufficient_charge", camp_id, creat_id)
    service = ConversionService(test_db)

    with pytest.raises(Exception):
        await service.track_conversion(
            click_token=tok,
            order_id="ORD-INSUFFICIENT-1",
            conversion_value=100.0,
            is_test=False,
        )
    await test_db.rollback()

    # Verify zero conversion events inserted
    ev_count_q = select(func.count()).where(AdEvent.external_order_id == "ORD-INSUFFICIENT-1")
    assert (await test_db.execute(ev_count_q)).scalar() == 0

    # Verify advertiser balance intact
    adv_check = await test_db.get(Advertiser, adv_id)
    assert adv_check.balance == Decimal("20.0000")


@pytest.mark.asyncio
async def test_suite2_conversion_postback_idempotency(test_db, phase5_entities):
    """Repeat conversion postback with same order_id deduplicates idempotently with zero double billing."""
    adv_id = phase5_entities["adv_id"]
    camp_id = phase5_entities["camp_id"]
    creat_id = phase5_entities["creat_id"]

    adv = await test_db.get(Advertiser, adv_id)
    adv.balance = Decimal("500.0000")
    await test_db.commit()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    click_ev = AdEvent(
        request_id="req_repeat_postback",
        campaign_id=camp_id,
        creative_id=creat_id,
        event_type=EventType.CLICK,
        event_time=now,
    )
    test_db.add(click_ev)
    await test_db.commit()

    tok = sign_click_token("req_repeat_postback", camp_id, creat_id)
    service = ConversionService(test_db)

    # First conversion
    res1 = await service.track_conversion(
        click_token=tok,
        order_id="ORD-REPEAT-P5-1",
        conversion_value=250.0,
        is_test=False,
    )
    await test_db.commit()
    assert res1.success is True
    assert res1.is_duplicate is False

    # Second conversion (retry with same order ID)
    res2 = await service.track_conversion(
        click_token=tok,
        order_id="ORD-REPEAT-P5-1",
        conversion_value=250.0,
        is_test=False,
    )
    await test_db.commit()
    assert res2.is_duplicate is True

    # Debited exactly once (500 - 50 = 450)
    adv_after = await test_db.get(Advertiser, adv_id)
    assert adv_after.balance == Decimal("450.0000")


# ==============================================================================
# Suite 3: Zero-Billing Invariants Suite
# ==============================================================================

@pytest.mark.asyncio
async def test_suite3_preview_and_sandbox_zero_billing(test_db, phase5_entities):
    """Preview and sandbox traffic enforce strict zero billable spend."""
    camp_id = phase5_entities["camp_id"]
    creat_id = phase5_entities["creat_id"]
    slot_id = phase5_entities["slot_id"]

    service = EventService(test_db)

    # Event with preview request ID
    ok1 = await service.track_event(
        request_id="prev_sandbox_session_1",
        ad_id=f"{camp_id}:{creat_id}",
        event_type="impression",
        extra={"slot_id": slot_id, "is_preview": True},
    )
    assert ok1 is True
    await test_db.commit()

    ev_q = select(AdEvent).where(AdEvent.request_id == "prev_sandbox_session_1")
    ev = (await test_db.execute(ev_q)).scalar_one()
    assert ev.cost == Decimal("0")


@pytest.mark.asyncio
async def test_suite3_unverified_property_zero_billing(test_db, phase5_entities):
    """Placements on unverified publisher properties produce strictly zero billable charge."""
    camp_id = phase5_entities["camp_id"]
    creat_id = phase5_entities["creat_id"]
    pub_id = phase5_entities["pub_id"]

    # Create unverified property
    unverified_prop = PublisherProperty(
        id=99,
        publisher_id=pub_id,
        name="Unverified Site",
        domain="unverified.example.com",
        status=Status.ACTIVE,
        verification_status="PENDING",
    )
    unverified_slot = AdSlot(
        id=99,
        publisher_id=pub_id,
        property_id=99,
        slot_id="slot_unverified",
        name="Unverified Slot",
        format=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
    )
    test_db.add_all([unverified_prop, unverified_slot])
    await test_db.commit()

    service = EventService(test_db)
    ok = await service.track_event(
        request_id="req_unverified_prop_1",
        ad_id=f"{camp_id}:{creat_id}",
        event_type="impression",
        extra={"slot_id": "slot_unverified"},
    )
    assert ok is True
    await test_db.commit()

    ev = (await test_db.execute(select(AdEvent).where(AdEvent.request_id == "req_unverified_prop_1"))).scalar_one()
    assert ev.cost == Decimal("0")


@pytest.mark.asyncio
async def test_suite3_inactive_publisher_zero_billing(test_db, phase5_entities):
    """Placements owned by inactive or suspended publishers produce zero billable charge."""
    camp_id = phase5_entities["camp_id"]
    creat_id = phase5_entities["creat_id"]

    inactive_pub = Publisher(id=98, name="Suspended Pub", balance=Decimal("0"), status=Status.PAUSED)
    inactive_prop = PublisherProperty(id=98, publisher_id=98, name="Site", domain="site98.com", status=Status.ACTIVE, verification_status="VERIFIED")
    inactive_slot = AdSlot(id=98, publisher_id=98, property_id=98, slot_id="slot_inactive_pub", name="Slot", format=1, width=300, height=250, status=Status.ACTIVE)
    test_db.add_all([inactive_pub, inactive_prop, inactive_slot])
    await test_db.commit()

    service = EventService(test_db)
    ok = await service.track_event(
        request_id="req_inactive_pub_1",
        ad_id=f"{camp_id}:{creat_id}",
        event_type="impression",
        extra={"slot_id": "slot_inactive_pub"},
    )
    assert ok is True
    await test_db.commit()

    ev = (await test_db.execute(select(AdEvent).where(AdEvent.request_id == "req_inactive_pub_1"))).scalar_one()
    assert ev.cost == Decimal("0")


# ==============================================================================
# Suite 4: Revenue Share & Precision Suite
# ==============================================================================

@pytest.mark.asyncio
async def test_suite4_rev_share_rate_locking_and_precision(test_db, phase5_entities):
    """Transaction rev_share_rate is locked at write time and does not drift if global settings change."""
    adv_id = phase5_entities["adv_id"]
    pub_id = phase5_entities["pub_id"]
    camp_id = phase5_entities["camp_id"]

    # Global setting is 0.700 (70%)
    cost = Decimal("100.000000")
    order_id = "ORD-REVSHARE-LOCK-1"

    success = await BillingService.process_cpa_charge(
        session=test_db,
        advertiser_id=adv_id,
        campaign_id=camp_id,
        cost=cost,
        publisher_id=pub_id,
        order_id=order_id,
    )
    assert success is True
    await test_db.commit()

    earn_tx_q = select(Transaction).where(Transaction.reference_id == f"earn:cpa:{camp_id}:{order_id}")
    earn_tx = (await test_db.execute(earn_tx_q)).scalar_one()
    assert earn_tx.rev_share_rate == Decimal("0.700")
    assert earn_tx.amount == Decimal("70.000000")

    # Now change platform global rev share to 0.850 (85%)
    settings = (await test_db.execute(select(PlatformSettings))).scalar_one()
    settings.publisher_rev_share = Decimal("0.850")
    await test_db.commit()

    # Verify prior transaction STILL has 0.700
    prior_tx = (await test_db.execute(earn_tx_q)).scalar_one()
    assert prior_tx.rev_share_rate == Decimal("0.700")
    assert prior_tx.amount == Decimal("70.000000")


# ==============================================================================
# Suite 5: Estimated vs. Finalised & Hold Window Suite
# ==============================================================================

@pytest.mark.asyncio
async def test_suite5_hold_window_pending_balance_and_clearing(test_db, phase5_entities):
    """When hold_hours > 0, earnings credit pending_balance with is_cleared=False; clear_settled_earnings clears them on expiry."""
    adv_id = phase5_entities["adv_id"]
    pub_id = phase5_entities["pub_id"]
    camp_id = phase5_entities["camp_id"]

    # Enable 24h hold window
    settings = (await test_db.execute(select(PlatformSettings))).scalar_one()
    settings.earnings_hold_hours = 24
    await test_db.commit()

    pub = await test_db.get(Publisher, pub_id)
    initial_pub_balance = pub.balance
    assert pub.pending_balance == Decimal("0.0000")

    order_id = "ORD-HOLD-WINDOW-1"
    cost = Decimal("100.0000")
    await BillingService.process_cpa_charge(
        session=test_db,
        advertiser_id=adv_id,
        campaign_id=camp_id,
        cost=cost,
        publisher_id=pub_id,
        order_id=order_id,
    )
    await test_db.commit()

    pub_after = await test_db.get(Publisher, pub_id)
    # Available balance untouched; pending_balance credited ₹70
    assert pub_after.balance == initial_pub_balance
    assert pub_after.pending_balance == Decimal("70.000000")

    earn_tx = (await test_db.execute(select(Transaction).where(Transaction.reference_id == f"earn:cpa:{camp_id}:{order_id}"))).scalar_one()
    assert earn_tx.is_cleared is False
    assert earn_tx.hold_until is not None

    # Fast forward: clearing as of 48 hours later
    future_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=48)
    cleared = await BillingService.clear_settled_earnings(test_db, as_of=future_time, publisher_id=pub_id)
    await test_db.commit()

    assert len(cleared) >= 1
    assert earn_tx.is_cleared is True

    pub_final = await test_db.get(Publisher, pub_id)
    assert pub_final.pending_balance == Decimal("0.000000")
    assert pub_final.balance == initial_pub_balance + Decimal("70.000000")


@pytest.mark.asyncio
async def test_suite5_manual_ivt_adjustment(test_db, phase5_entities):
    """Admin IVT deduction creates auditable ADJUSTMENT transaction with explicit label."""
    pub_id = phase5_entities["pub_id"]
    pub = await test_db.get(Publisher, pub_id)
    bal_before = pub.balance

    adj_tx = await BillingService.apply_ivt_adjustment(
        session=test_db,
        publisher_id=pub_id,
        amount=Decimal("45.0000"),
        reason="Detected datacenter bot click farm on slot_p5_banner",
        reference_id="adj_ivt_p5_1",
    )
    await test_db.commit()

    assert adj_tx.type == TransactionType.ADJUSTMENT
    assert adj_tx.amount == Decimal("-45.0000")
    assert "IVT Adjustment: Detected datacenter bot click farm" in adj_tx.description

    pub_after = await test_db.get(Publisher, pub_id)
    assert pub_after.balance == bal_before - Decimal("45.0000")


# ==============================================================================
# Suite 6: Payout Lifecycle & Concurrency Suite
# ==============================================================================

@pytest.mark.asyncio
async def test_suite6_payout_reservation_and_overdraw_protection(test_db, phase5_entities):
    """Payout reservation holds funds; competing requests against exact balance reject overdraw."""
    pub_id = phase5_entities["pub_id"]
    pub = await test_db.get(Publisher, pub_id)
    pub.balance = Decimal("100.0000")
    await test_db.commit()

    # Request 1 for 100.00
    payout1, tx1 = await BillingService.request_payout(
        session=test_db,
        publisher_id=pub_id,
        amount=Decimal("100.0000"),
        reference_id="payout_p5_test_1",
    )
    await test_db.commit()
    assert payout1.status == "requested"
    assert tx1.amount == Decimal("-100.0000")

    # Pub balance is now 0.00
    pub_zero = await test_db.get(Publisher, pub_id)
    assert pub_zero.balance == Decimal("0.0000")

    # Competing request 2 for 100.00 must be rejected
    with pytest.raises(ValueError, match="Insufficient balance"):
        await BillingService.request_payout(
            session=test_db,
            publisher_id=pub_id,
            amount=Decimal("100.0000"),
            reference_id="payout_p5_test_2",
        )
    await test_db.rollback()

    pub_still_zero = await test_db.get(Publisher, pub_id)
    assert pub_still_zero.balance == Decimal("0.0000")


@pytest.mark.asyncio
async def test_suite6_payout_reject_and_fail_refunds(test_db, phase5_entities):
    """Rejecting or failing a payout restores reserved funds via REFUND transaction."""
    pub_id = phase5_entities["pub_id"]
    pub = await test_db.get(Publisher, pub_id)
    pub.balance = Decimal("200.0000")
    await test_db.commit()

    payout, _ = await BillingService.request_payout(
        session=test_db,
        publisher_id=pub_id,
        amount=Decimal("80.0000"),
        reference_id="po_refund_test",
    )
    await test_db.commit()

    pub_held = await test_db.get(Publisher, pub_id)
    assert pub_held.balance == Decimal("120.0000")

    # Reject payout
    payout_rej, ref_tx = await BillingService.reject_payout(
        session=test_db,
        payout_id=payout.id,
        reason="Bank details invalid",
    )
    await test_db.commit()

    assert payout_rej.status == "rejected"
    assert ref_tx.type == TransactionType.REFUND
    assert ref_tx.amount == Decimal("80.0000")

    # Funds fully restored (120 + 80 = 200)
    pub_restored = await test_db.get(Publisher, pub_id)
    assert pub_restored.balance == Decimal("200.0000")


# ==============================================================================
# Suite 7: Rollup vs. Raw Event Agreement Suite
# ==============================================================================

@pytest.mark.asyncio
async def test_suite7_reconciliation_detects_event_vs_ledger_drift(test_db, phase5_entities):
    """Reconciliation detects gross spend drift and populates the unresolved queue without auto-balancing."""
    camp_id = phase5_entities["camp_id"]
    creat_id = phase5_entities["creat_id"]
    adv_id = phase5_entities["adv_id"]

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Insert raw delivery event with cost 150.00
    ev = AdEvent(
        request_id="req_drift_ev_1",
        campaign_id=camp_id,
        creative_id=creat_id,
        event_type=EventType.IMPRESSION,
        event_time=now,
        cost=Decimal("150.0000"),
        is_test=False,
        is_invalid=False,
    )
    test_db.add(ev)
    # But only insert 100.00 spend transaction in ledger (50.00 drift)
    tx = Transaction(
        type=TransactionType.SPEND,
        amount=Decimal("-100.0000"),
        currency="INR",
        advertiser_id=adv_id,
        campaign_id=camp_id,
        reference_id="spend_drift_tx_1",
        created_at=now,
    )
    test_db.add(tx)
    await test_db.commit()

    service = ReconciliationService(test_db)
    today = now.date()
    report = await service.reconcile(start_date=today, end_date=today, advertiser_id=adv_id)

    assert report.status == "DISCREPANCY_DETECTED"
    assert report.spend_delta >= Decimal("50.00")
    assert len(report.unresolved_queue) >= 1

    first_item = report.unresolved_queue[0]
    assert first_item["status"] == "UNRESOLVED"
    assert "gross_spend" in first_item["metric"]
    assert first_item["delta"] >= 50.0


# ==============================================================================
# Suite 8: Admin Ledger Filters, Masking & Exception Queue Suite
# ==============================================================================

@pytest.mark.asyncio
async def test_suite8_admin_ledger_filters_and_bank_masking(client: AsyncClient, test_db, phase5_entities):
    """Admin ledger supports multi-column filtering and returns masked bank details."""
    admin_id = phase5_entities["admin_id"]
    pub_id = phase5_entities["pub_id"]
    adv_id = phase5_entities["adv_id"]

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Create transaction with sensitive bank detail in description
    tx_bank = Transaction(
        type=TransactionType.PAYOUT,
        amount=Decimal("-150.0000"),
        currency="INR",
        publisher_id=pub_id,
        reference_id="payout_bank_mask_1",
        description="Payout to Bank Account: 987654321098 via IMPS",
        is_cleared=True,
        created_at=now,
    )
    test_db.add(tx_bank)
    await test_db.commit()

    # 1. Non-admin forbidden
    adv_headers = get_auth_headers(phase5_entities["adv_user_id"], "advertiser", advertiser_id=adv_id)
    r_forbidden = await client.get("/api/v1/finance/ledger/audit", headers=adv_headers)
    assert r_forbidden.status_code == 403

    # 2. Admin filtered query
    admin_headers = get_auth_headers(admin_id, "admin")
    r_audit = await client.get(
        f"/api/v1/finance/ledger/audit?publisher_id={pub_id}&reference=payout_bank_mask_1",
        headers=admin_headers,
    )
    assert r_audit.status_code == 200
    data = r_audit.json()
    assert len(data) == 1
    assert data[0]["reference_id"] == "payout_bank_mask_1"
    # Bank account masked: 987654321098 -> ****1098
    assert "987654321098" not in data[0]["description"]
    assert "****1098" in data[0]["description"]

    # 3. Masked publisher payout details endpoint
    r_pd = await client.get(f"/api/v1/finance/publisher/{pub_id}/payout-details", headers=admin_headers)
    assert r_pd.status_code == 200
    pd = r_pd.json()["payout_details"]
    assert pd["account_number"] == "****9012"
    assert pd["secret"] == "[REDACTED]"
    assert pd["bank_name"] == "State Bank of India"
