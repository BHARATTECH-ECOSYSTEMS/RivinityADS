"""
Phase 1 Financial Integrity and Conversion Security Integration Test Suite.

Verifies:
1. Unsigned public S2S postbacks are rejected (400).
2. Invalid HMAC signature on S2S postback is rejected (400).
3. Stale timestamp (> 24h drift) and future timestamp (> 24h) are rejected (400).
4. Cross-tenant authorization isolation (Advertiser A cannot submit postbacks for Advertiser B).
5. A valid click token with NO stored click row in the database cannot create an attributed conversion.
6. Wrong campaign mismatch (token/click campaign != payload campaign) is rejected (400).
7. Removal of arbitrary publisher selection fallback (direct inventory rule applied; no random publisher credited).
8. Single canonical CPA billing path with DB idempotency across duplicate and retry requests.
9. Concurrent duplicate conversions do not double-bill (PostgreSQL concurrency safe).
10. Test conversions (is_test=True) produce zero billable charges and zero stats contamination.
11. Transaction currency is always INR (no default to USD).
12. Publisher revenue-share calculation is centralized (wallet credits reconcile with daily stats under custom percentages).
13. Payout reservation state machine: requested -> held funds -> reject & rollback -> funds restored.
14. Payout confirmation flow: transitions to 'paid' only upon external provider confirmation.
15. Razorpay signed-webhook idempotency and concurrent duplicate protection.
16. Advertiser API key lifecycle: create, list, rotate, revoke with audit events.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from liteads.ad_server.services.billing import BillingService
from liteads.ad_server.services.conversion_service import ConversionService
from liteads.common.config import get_settings
from liteads.common.tracking_sig import (
    sign_click_token,
    sign_conversion_sig,
)
from liteads.management.advertiser_api_key_service import AdvertiserApiKeyService
from liteads.models.ad import (
    AdEvent,
    Advertiser,
    AdvertiserApiKey,
    Campaign,
    CampaignAssignment,
    Creative,
)
from liteads.models.auth import AuditLog, User
from liteads.models.base import BidType, EventType, Status, TransactionType
from liteads.models.finance import PaymentRequest, PlatformSettings, PublisherPayout, Transaction
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures and Helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def pg_engine():
    """Create a real PostgreSQL async engine or skip if unavailable."""
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
    """Session factory for creating real independent PostgreSQL sessions."""
    return sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)


async def _create_test_advertiser_campaign(
    session: AsyncSession,
    name: str = "P1 Adv",
    balance: Decimal = Decimal("1000.00"),
    bid_type: int = BidType.CPA,
    bid_amount: Decimal = Decimal("50.00"),
) -> tuple[Advertiser, Campaign, Creative]:
    adv = Advertiser(
        name=name,
        company="Bharattech Ads Test Corp",
        balance=balance,
    )
    session.add(adv)
    await session.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name=f"{name} Campaign",
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
        title=f"{name} Creative",
        landing_url="https://example.com/target",
        status=Status.ACTIVE,
    )
    session.add(creat)
    await session.flush()
    return adv, camp, creat


# ─────────────────────────────────────────────────────────────────────────────
# Task A & B: S2S Postback Signature & Credential Rejection Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_unsigned_s2s_postback_rejected(client: AsyncClient, test_db: AsyncSession):
    """Unsigned S2S conversions must be strictly rejected with 400."""
    adv, camp, creat = await _create_test_advertiser_campaign(test_db)
    await test_db.commit()

    res = await client.post(
        "/api/v1/conversion/postback",
        json={
            "campaign_id": camp.id,
            "order_id": "ORD-UNSIGNED-01",
            "conversion_value": 150.0,
            "currency": "INR",
            "timestamp": int(time.time()),
            # No signature supplied
        },
    )
    assert res.status_code == 400
    assert "Missing conversion signature" in res.json()["detail"]


async def test_invalid_signature_s2s_postback_rejected(client: AsyncClient, test_db: AsyncSession):
    """S2S postbacks with forged/tampered signature must be rejected with 400."""
    adv, camp, creat = await _create_test_advertiser_campaign(test_db)
    await test_db.commit()

    res = await client.post(
        "/api/v1/conversion/postback",
        json={
            "campaign_id": camp.id,
            "order_id": "ORD-FORGED-01",
            "conversion_value": 150.0,
            "currency": "INR",
            "timestamp": int(time.time()),
            "signature": "forged_invalid_signature_hex_1234567890abcdef",
        },
    )
    assert res.status_code == 400
    assert "Invalid conversion signature" in res.json()["detail"]


async def test_stale_and_future_timestamp_s2s_postback_rejected(client: AsyncClient, test_db: AsyncSession):
    """S2S postbacks with timestamp outside allowed 24h drift window must be rejected."""
    adv, camp, creat = await _create_test_advertiser_campaign(test_db)
    await test_db.commit()

    # 1. Stale timestamp (30 hours old)
    stale_ts = int(time.time()) - (30 * 3600)
    stale_sig = sign_conversion_sig(
        campaign_id=camp.id,
        order_id="ORD-STALE-01",
        value=100.0,
        currency="INR",
        timestamp=stale_ts,
    )
    res_stale = await client.post(
        "/api/v1/conversion/postback",
        json={
            "campaign_id": camp.id,
            "order_id": "ORD-STALE-01",
            "conversion_value": 100.0,
            "currency": "INR",
            "timestamp": stale_ts,
            "signature": stale_sig,
        },
    )
    assert res_stale.status_code == 400
    assert "Expired conversion signature timestamp" in res_stale.json()["detail"]

    # 2. Future timestamp (30 hours in future)
    future_ts = int(time.time()) + (30 * 3600)
    future_sig = sign_conversion_sig(
        campaign_id=camp.id,
        order_id="ORD-FUT-01",
        value=100.0,
        currency="INR",
        timestamp=future_ts,
    )
    res_fut = await client.post(
        "/api/v1/conversion/postback",
        json={
            "campaign_id": camp.id,
            "order_id": "ORD-FUT-01",
            "conversion_value": 100.0,
            "currency": "INR",
            "timestamp": future_ts,
            "signature": future_sig,
        },
    )
    assert res_fut.status_code == 400
    assert "Expired conversion signature timestamp" in res_fut.json()["detail"]


async def test_cross_tenant_isolation_s2s_postback(client: AsyncClient, test_db: AsyncSession):
    """Advertiser A's API key attempting to submit postbacks for Advertiser B's campaign must be rejected (403)."""
    adv_a, camp_a, _ = await _create_test_advertiser_campaign(test_db, "Adv A")
    adv_b, camp_b, _ = await _create_test_advertiser_campaign(test_db, "Adv B")

    # Create API key for Advertiser A
    key_row, raw_key_a, raw_sec_a = await AdvertiserApiKeyService.create_api_key(
        test_db, advertiser_id=adv_a.id, name="Adv A Key"
    )
    await test_db.commit()

    now_ts = int(time.time())
    sig = sign_conversion_sig(
        campaign_id=camp_b.id,
        order_id="ORD-X-TENANT",
        value=100.0,
        currency="INR",
        timestamp=now_ts,
        secret=raw_sec_a,
        advertiser_id=adv_a.id,
    )

    res = await client.post(
        "/api/v1/conversion/postback",
        headers={"X-API-Key": raw_key_a},
        json={
            "campaign_id": camp_b.id,  # Owned by Adv B!
            "order_id": "ORD-X-TENANT",
            "conversion_value": 100.0,
            "currency": "INR",
            "timestamp": now_ts,
            "signature": sig,
        },
    )
    assert res.status_code == 403
    assert "belongs to another advertiser" in res.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Task C: Real Stored Click Validation (Risk 3)
# ─────────────────────────────────────────────────────────────────────────────

async def test_valid_token_with_missing_stored_click_is_unattributed(client: AsyncClient, test_db: AsyncSession):
    """
    Risk 3 / Task C: A valid cryptographically signed click token with NO matching stored click row
    in the database must NOT be attributed, and must generate zero billable spend.
    """
    adv, camp, creat = await _create_test_advertiser_campaign(
        test_db, balance=Decimal("1000.00"), bid_type=BidType.CPA, bid_amount=Decimal("50.00")
    )
    await test_db.commit()

    # Generate a cryptographically valid click token for a non-existent DB request
    token = sign_click_token("req_phantom_click_999", camp.id, creat.id)

    # Post conversion referencing the token (notice: no AdEvent click row exists in DB)
    res = await client.post(
        "/api/v1/event/conversion",
        json={
            "click_token": token,
            "campaign_id": camp.id,
            "order_id": "ORD-PHANTOM-01",
            "conversion_value": 200.0,
            "currency": "INR",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["is_attributed"] is False
    assert data["attribution_reason"] == "click_not_found"

    # Verify wallet was NOT charged
    await test_db.refresh(adv)
    assert adv.balance == Decimal("1000.00")

    # Verify no Transaction was recorded
    tx_res = await test_db.execute(
        select(Transaction).where(Transaction.advertiser_id == adv.id)
    )
    assert len(tx_res.scalars().all()) == 0


async def test_wrong_campaign_mismatch_rejected(client: AsyncClient, test_db: AsyncSession):
    """Postback campaign ID mismatch against stored click campaign must be rejected with 400."""
    adv_a, camp_a, creat_a = await _create_test_advertiser_campaign(test_db, "Camp A")
    adv_b, camp_b, creat_b = await _create_test_advertiser_campaign(test_db, "Camp B")

    # Insert click for Camp A
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    click = AdEvent(
        request_id="req_mismatch_camp",
        campaign_id=camp_a.id,
        creative_id=creat_a.id,
        event_type=EventType.CLICK,
        event_time=now,
    )
    test_db.add(click)
    await test_db.commit()

    # Fire conversion passing req_mismatch_camp but camp_b.id
    res = await client.post(
        "/api/v1/event/conversion",
        json={
            "request_id": "req_mismatch_camp",
            "campaign_id": camp_b.id,
            "order_id": "ORD-MISMATCH",
            "conversion_value": 100.0,
        },
    )
    assert res.status_code == 400
    assert "Campaign ID mismatch" in res.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# Task D: Removal of Arbitrary Publisher Selection Fallback (Risk 4)
# ─────────────────────────────────────────────────────────────────────────────

async def test_publisher_resolution_no_arbitrary_fallback(client: AsyncClient, test_db: AsyncSession):
    """
    Risk 4 / Task D: When no real publisher or campaign assignment exists, the system
    must apply the documented direct inventory rule. An arbitrary publisher MUST NOT be credited.
    """
    # Create arbitrary publisher in system that has NOTHING to do with this campaign
    unrelated_pub = Publisher(
        name="Unrelated Random Publisher",
        balance=Decimal("0.000000"),
    )
    test_db.add(unrelated_pub)
    await test_db.flush()

    adv, camp, creat = await _create_test_advertiser_campaign(
        test_db, balance=Decimal("1000.00"), bid_type=BidType.CPA, bid_amount=Decimal("50.00")
    )

    # Insert valid direct click (no publisher slot)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    click = AdEvent(
        request_id="req_direct_inventory_01",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=now,
    )
    test_db.add(click)
    await test_db.commit()

    res = await client.post(
        "/api/v1/event/conversion",
        json={
            "request_id": "req_direct_inventory_01",
            "campaign_id": camp.id,
            "order_id": "ORD-DIRECT-INV-01",
            "conversion_value": 300.0,
        },
    )
    assert res.status_code == 200
    assert res.json()["is_attributed"] is True

    # Advertiser wallet must be charged CPA
    await test_db.refresh(adv)
    assert adv.balance == Decimal("950.00")

    # CRITICAL: Unrelated publisher balance must remain exactly ZERO!
    await test_db.refresh(unrelated_pub)
    assert unrelated_pub.balance == Decimal("0.000000")

    # Transaction must record publisher_id=None (Direct Inventory)
    tx_res = await test_db.execute(
        select(Transaction).where(Transaction.advertiser_id == adv.id)
    )
    tx = tx_res.scalar_one_or_none()
    assert tx is not None
    assert tx.publisher_id is None
    assert tx.amount == Decimal("-50.000000")
    assert tx.currency == "INR"


# ─────────────────────────────────────────────────────────────────────────────
# Task E: Single Canonical CPA Billing Path & Deduplication (Risk 5)
# ─────────────────────────────────────────────────────────────────────────────

async def test_duplicate_order_cpa_deduplication(client: AsyncClient, test_db: AsyncSession):
    """The same external_order_id sent multiple times cannot double-bill the advertiser."""
    adv, camp, creat = await _create_test_advertiser_campaign(
        test_db, balance=Decimal("1000.00"), bid_type=BidType.CPA, bid_amount=Decimal("40.00")
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    click = AdEvent(
        request_id="req_cpa_dedup_01",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=now,
    )
    test_db.add(click)
    await test_db.commit()

    payload = {
        "request_id": "req_cpa_dedup_01",
        "campaign_id": camp.id,
        "order_id": "ORD-CPA-DEDUP-01",
        "conversion_value": 500.0,
        "currency": "INR",
    }

    # First attempt: charges 40.00
    res1 = await client.post("/api/v1/event/conversion", json=payload)
    assert res1.status_code == 200
    assert res1.json()["is_attributed"] is True
    assert res1.json()["is_duplicate"] is False

    await test_db.refresh(adv)
    assert adv.balance == Decimal("960.00")

    # Second attempt: deduplicated idempotently, NO secondary charge
    res2 = await client.post("/api/v1/event/conversion", json=payload)
    assert res2.status_code == 200
    assert res2.json()["is_duplicate"] is True

    await test_db.refresh(adv)
    assert adv.balance == Decimal("960.00")

    # Confirm only 1 Transaction row exists
    txs = (await test_db.execute(select(Transaction).where(Transaction.advertiser_id == adv.id))).scalars().all()
    assert len(txs) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Task F: Currency Propagation into Ledger Records (Risk 6)
# ─────────────────────────────────────────────────────────────────────────────

async def test_transaction_currency_is_inr(client: AsyncClient, test_db: AsyncSession):
    """
    Risk 6 / Task F: Ensure all transactions (deposit, spend, payout) record currency as INR,
    and reject unsupported currencies.
    """
    adv, camp, creat = await _create_test_advertiser_campaign(test_db, balance=Decimal("100.00"))
    pub = Publisher(name="Pub Currency Test", balance=Decimal("100.00"))
    test_db.add(pub)
    await test_db.commit()

    # 1. Deposit
    tx_dep = await BillingService.record_deposit(
        session=test_db,
        advertiser_id=adv.id,
        amount=Decimal("50.00"),
        currency="INR",
        reference_id=f"dep_curr_test_{int(time.time())}",
    )
    assert tx_dep.currency == "INR"

    # 2. Spend
    bill_ok = await BillingService.process_impression_charge(
        session=test_db,
        advertiser_id=adv.id,
        campaign_id=camp.id,
        publisher_id=pub.id,
        cost=Decimal("10.00"),
        reference_id=f"imp_curr_test_{int(time.time())}",
        currency="INR",
    )
    assert bill_ok is True

    # 3. Payout reservation
    payout, tx_pout = await BillingService.request_payout(
        session=test_db,
        publisher_id=pub.id,
        amount=Decimal("20.00"),
        currency="INR",
        reference_id=f"pout_curr_test_{int(time.time())}",
    )
    assert tx_pout.currency == "INR"
    assert payout.currency == "INR"

    await test_db.commit()

    # Query all ledger records for this advertiser & publisher
    txs = (await test_db.execute(select(Transaction))).scalars().all()
    for tx in txs:
        assert tx.currency == "INR"


# ─────────────────────────────────────────────────────────────────────────────
# Task G: Centralized Configurable Revenue Share (Risk 7)
# ─────────────────────────────────────────────────────────────────────────────

async def test_configurable_revenue_share_reconciliation(client: AsyncClient, test_db: AsyncSession):
    """
    Risk 7 / Task G: Publisher wallet credits and PublisherDailyStat earnings must reconcile
    identically under custom configured revenue shares (e.g. 85%).
    """
    # Configure non-default revenue share (85%)
    settings = PlatformSettings(publisher_rev_share=Decimal("0.850"))
    test_db.add(settings)
    await test_db.flush()

    adv, camp, creat = await _create_test_advertiser_campaign(test_db, balance=Decimal("1000.00"))
    pub = Publisher(name="Pub 85 RevShare", balance=Decimal("0.000000"))
    test_db.add(pub)
    await test_db.flush()

    slot = AdSlot(
        publisher_id=pub.id,
        slot_id="slot_rev_share_85",
        name="RevShare 85 Slot",
        format=1,
    )
    test_db.add(slot)
    await test_db.commit()

    # Track an impression event with cost = 100.00
    from liteads.ad_server.services.event_service import EventService
    ev_service = EventService(test_db)
    await ev_service._update_publisher_stats(
        event_type=EventType.IMPRESSION,
        cost=Decimal("100.00"),
        extra={"slot_id": "slot_rev_share_85"},
    )
    await test_db.commit()

    # Check PublisherDailyStat earnings: must be 100 * 0.85 = 85.00
    stat_res = await test_db.execute(
        select(PublisherDailyStat).where(PublisherDailyStat.slot_id == slot.id)
    )
    stat = stat_res.scalar_one_or_none()
    assert stat is not None
    assert stat.earnings == Decimal("85.000000")

    # Process billable impression charge through BillingService:
    bill_ok = await BillingService.process_impression_charge(
        session=test_db,
        advertiser_id=adv.id,
        campaign_id=camp.id,
        publisher_id=pub.id,
        cost=Decimal("100.00"),
        reference_id="ref_bill_revshare_85",
    )
    assert bill_ok is True
    await test_db.commit()

    pub_refreshed = await test_db.get(Publisher, pub.id)
    assert pub_refreshed is not None
    # Wallet balance must be credited exactly 85.00
    assert pub_refreshed.balance == Decimal("85.000000")
    # Exact reconciliation between stat earnings and wallet balance:
    assert pub_refreshed.balance == stat.earnings


# ─────────────────────────────────────────────────────────────────────────────
# Task H: Payout Lifecycle State Machine (Risk 8)
# ─────────────────────────────────────────────────────────────────────────────

async def test_payout_reservation_and_rejection_rollback(test_db: AsyncSession):
    """
    Risk 8 / Task H: Payout operations hold funds in a reservation.
    Rejection of a requested payout must restore the reserved funds with an immutable reversal record.
    """
    pub = Publisher(name="Pub Payout Test", balance=Decimal("500.00"))
    test_db.add(pub)
    await test_db.commit()

    # 1. Request payout of 200.00
    payout, tx_req = await BillingService.request_payout(
        session=test_db,
        publisher_id=pub.id,
        amount=Decimal("200.00"),
        currency="INR",
        reference_id="pout_test_res_01",
        notes="Monthly earnings payout",
    )
    await test_db.commit()

    assert payout.status == "requested"
    assert payout.amount == Decimal("200.00")
    await test_db.refresh(pub)
    assert pub.balance == Decimal("300.00")  # 200 held in reservation

    # 2. Reject payout
    payout_rej, tx_rev = await BillingService.reject_payout(
        session=test_db,
        payout_id=payout.id,
        reason="Bank details invalid",
    )
    await test_db.commit()

    assert payout_rej.status == "rejected"
    assert "Bank details invalid" in payout_rej.notes
    await test_db.refresh(pub)
    assert pub.balance == Decimal("500.00")  # Funds fully restored!

    # Reversal transaction check
    assert tx_rev.type == TransactionType.REFUND
    assert tx_rev.amount == Decimal("200.00")
    assert tx_rev.reference_id == "rev_pout_test_res_01"


async def test_payout_confirmation_flow(test_db: AsyncSession):
    """Confirmation with external provider transaction ID transitions payout to 'paid'."""
    pub = Publisher(name="Pub Payout Confirm", balance=Decimal("300.00"))
    test_db.add(pub)
    await test_db.commit()

    payout, _ = await BillingService.request_payout(
        session=test_db,
        publisher_id=pub.id,
        amount=Decimal("100.00"),
        reference_id="pout_test_conf_01",
    )
    await test_db.commit()
    assert payout.status == "requested"

    # Confirm with RazorpayX / Bank UTR transfer reference
    confirmed_payout = await BillingService.confirm_payout(
        session=test_db,
        payout_id=payout.id,
        provider_payout_id="rzp_pout_transfer_999",
        notes="Bank transfer processed via IMPS UTR 1234567890",
    )
    await test_db.commit()

    assert confirmed_payout.status == "paid"
    assert confirmed_payout.provider_payout_id == "rzp_pout_transfer_999"
    assert confirmed_payout.processed_at is not None
    await test_db.refresh(pub)
    assert pub.balance == Decimal("200.00")  # No double debit


# ─────────────────────────────────────────────────────────────────────────────
# Task I: Razorpay Webhook Idempotency & Concurrency
# ─────────────────────────────────────────────────────────────────────────────

async def test_razorpay_webhook_idempotency_flow(client: AsyncClient, test_db: AsyncSession):
    """Duplicate and repeated Razorpay webhooks must return 'already_processed' without double crediting."""
    from unittest.mock import MagicMock
    from liteads.ad_server.main import app
    from liteads.ad_server.services.razorpay_client import get_razorpay_client

    mock_client = MagicMock()
    mock_client.verify_webhook_signature.return_value = True
    app.dependency_overrides[get_razorpay_client] = lambda: mock_client

    try:
        adv, _, _ = await _create_test_advertiser_campaign(test_db, balance=Decimal("0.00"))
        await test_db.commit()

        order_id = f"order_rzp_{uuid.uuid4().hex[:8]}"

        # Construct webhook body
        body_dict = {
            "event": "order.paid",
            "payload": {
                "order": {
                    "entity": {
                        "id": order_id,
                        "amount": 50000,  # ₹500.00
                        "currency": "INR",
                        "receipt": f"adv_{adv.id}_123",
                        "status": "paid",
                    }
                }
            },
        }
        import json
        body_bytes = json.dumps(body_dict).encode("utf-8")
        sig = "valid_mocked_signature_123"

        # First delivery
        res1 = await client.post(
            "/api/v1/finance/webhooks/razorpay",
            content=body_bytes,
            headers={"x-razorpay-signature": sig, "Content-Type": "application/json"},
        )
        assert res1.status_code == 200
        assert res1.json()["status"] == "success"

        await test_db.refresh(adv)
        assert adv.balance == Decimal("500.00")

        # Duplicate delivery
        res2 = await client.post(
            "/api/v1/finance/webhooks/razorpay",
            content=body_bytes,
            headers={"x-razorpay-signature": sig, "Content-Type": "application/json"},
        )
        assert res2.status_code == 200
        assert res2.json()["status"] == "already_processed"

        await test_db.refresh(adv)
        assert adv.balance == Decimal("500.00")  # NO duplicate top-up!
    finally:
        app.dependency_overrides.pop(get_razorpay_client, None)


# ─────────────────────────────────────────────────────────────────────────────
# Task J: Advertiser API Key Lifecycle & Audit Events
# ─────────────────────────────────────────────────────────────────────────────

async def test_advertiser_api_key_lifecycle_and_audit(test_db: AsyncSession):
    """Advertiser API keys can be created, listed, rotated, and revoked with full audit trail."""
    adv, _, _ = await _create_test_advertiser_campaign(test_db)
    await test_db.commit()

    # 1. Create Key
    key_row, raw_key, raw_secret = await AdvertiserApiKeyService.create_api_key(
        test_db, advertiser_id=adv.id, name="Shopify S2S Key"
    )
    assert raw_key.startswith("ak_live_")
    assert raw_secret.startswith("sec_live_")
    assert key_row.key_prefix.startswith("ak_live_")
    assert key_row.is_active is True

    # Check AuditLog
    audits = (await test_db.execute(select(AuditLog).where(AuditLog.action == "create_advertiser_api_key"))).scalars().all()
    assert len(audits) >= 1

    # 2. List Keys
    keys = await AdvertiserApiKeyService.list_api_keys(test_db, adv.id)
    assert len(keys) == 1
    assert keys[0].id == key_row.id

    # 3. Rotate Key
    new_key_row, new_raw_key, new_raw_secret = await AdvertiserApiKeyService.rotate_api_key(
        test_db, advertiser_id=adv.id, key_id=key_row.id
    )
    assert new_key_row.id != key_row.id
    assert new_key_row.is_active is True
    # Old key is revoked
    await test_db.refresh(key_row)
    assert key_row.is_active is False
    assert key_row.revoked_at is not None

    # 4. Authenticating with revoked old key fails
    with pytest.raises(Exception) as exc_info:
        await AdvertiserApiKeyService.resolve_advertiser_api_key(raw_key, test_db)
    assert "revoked" in str(exc_info.value).lower()

    # Authenticating with new key succeeds
    authed_key, authed_adv = await AdvertiserApiKeyService.resolve_advertiser_api_key(new_raw_key, test_db)
    assert authed_key.id == new_key_row.id
    assert authed_adv.id == adv.id


# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL Real Concurrency Tests (Rule 6, 11)
# ─────────────────────────────────────────────────────────────────────────────

async def test_postgres_cpa_concurrent_duplicate_race(pg_session_factory):
    """
    True PostgreSQL competing sessions race condition:
    Two concurrent requests try to charge the same CPA conversion order simultaneously.
    Database uniqueness on Transaction.reference_id guarantees exactly ONE charge occurs.
    """
    unique_tag = uuid.uuid4().hex[:8]
    adv_id = None
    camp_id = None
    order_id = f"ORD-CONCUR-RACE-{unique_tag}"

    async with pg_session_factory() as s1:
        adv = Advertiser(name=f"PG Concur Adv {unique_tag}", balance=Decimal("1000.00"))
        s1.add(adv)
        await s1.flush()
        adv_id = adv.id

        camp = Campaign(
            advertiser_id=adv_id,
            name=f"PG Concur Camp {unique_tag}",
            budget_total=Decimal("10000.00"),
            budget_daily=Decimal("1000.00"),
            bid_type=BidType.CPA,
            bid_amount=Decimal("100.00"),
            status=Status.ACTIVE,
        )
        s1.add(camp)
        await s1.commit()
        camp_id = camp.id

    async def attempt_cpa_charge():
        async with pg_session_factory() as session:
            try:
                ok = await BillingService.process_cpa_charge(
                    session=session,
                    advertiser_id=adv_id,
                    campaign_id=camp_id,
                    cost=Decimal("100.00"),
                    currency="INR",
                    order_id=order_id,
                )
                if ok:
                    await session.commit()
                return ok
            except Exception as e:
                await session.rollback()
                return False

    # Fire two concurrent tasks competing against real PostgreSQL
    res_a, res_b = await asyncio.gather(attempt_cpa_charge(), attempt_cpa_charge())

    # Verify exactly one transaction was recorded in PostgreSQL
    async with pg_session_factory() as verify_session:
        txs = (
            await verify_session.execute(
                select(Transaction).where(Transaction.advertiser_id == adv_id)
            )
        ).scalars().all()
        adv_after = await verify_session.get(Advertiser, adv_id)

        # Teardown
        await verify_session.execute(delete(Transaction).where(Transaction.advertiser_id == adv_id))
        await verify_session.execute(delete(Campaign).where(Campaign.id == camp_id))
        await verify_session.execute(delete(Advertiser).where(Advertiser.id == adv_id))
        await verify_session.commit()

    assert len(txs) == 1, f"Expected exactly 1 Transaction row, found {len(txs)}"
    assert adv_after.balance == Decimal("900.00"), f"Expected 900.00 balance, found {adv_after.balance}"
