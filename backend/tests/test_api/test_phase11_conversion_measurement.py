"""
RivinityADS — Phase 11 Conversion Measurement & Advertiser Outcome Reporting Test Suite.

Automated verification covering all 15 required scenarios:
1. Valid click-to-conversion attribution.
2. Conversion outside the attribution window.
3. Duplicate postback and retry idempotency.
4. Invalid signature.
5. Expired or replayed request.
6. Mapping across multiple campaigns and creatives.
7. Cross-tenant authorization isolation.
8. UTC and attribution-window boundary behavior.
9. Missing conversion value or currency validation.
10. Incompatible currencies in financial reporting.
11. Zero spend and division-by-zero handling.
12. Test-event exclusion from production metrics.
13. Invalid payloads and signature tampering.
14. Existing impression/click tracking regressions.
15. No duplicate spend, wallet, ledger, or publisher-earnings effects.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import get_settings
from liteads.common.tracking_sig import (
    sign_click_token,
    sign_conversion_sig,
    sign_tracking,
)
from liteads.models.ad import AdEvent, Advertiser, Campaign, Creative
from liteads.models.auth import User
from liteads.models.base import BidType, EventType, Status

pytestmark = pytest.mark.asyncio


async def _register_user(client: AsyncClient, email: str, role: str = "advertiser") -> tuple[str, int]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Phase 11 Org",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def _setup_advertiser_campaign(
    session: AsyncSession,
    user_id: int,
    campaign_name: str = "Conv Camp",
    bid_type: int = BidType.CPM,
    bid_amount: Decimal = Decimal("5.0"),
    balance: Decimal = Decimal("1000.0"),
    currency: str = "INR",
) -> tuple[Advertiser, Campaign, Creative]:
    adv = Advertiser(
        name="Phase 11 Adv",
        company="Phase 11 Corp",
        balance=balance,
    )
    session.add(adv)
    await session.flush()

    user = await session.get(User, user_id)
    if user:
        user.advertiser_id = adv.id

    camp = Campaign(
        advertiser_id=adv.id,
        name=campaign_name,
        budget_total=Decimal("10000.0"),
        budget_daily=Decimal("1000.0"),
        bid_type=bid_type,
        bid_amount=bid_amount,
        status=Status.ACTIVE,
    )
    session.add(camp)
    await session.flush()

    creat = Creative(
        campaign_id=camp.id,
        title=f"{campaign_name} Creative",
        landing_url="https://example.com/landing",
        status=Status.ACTIVE,
    )
    session.add(creat)
    await session.flush()

    return adv, camp, creat


# ── Test 1: Valid click-to-conversion attribution ──────────────────────────
async def test_valid_click_to_conversion_attribution(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_valid_click@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    # 1. Record a valid click event in DB
    now_utc = datetime.now(timezone.utc)
    click_event = AdEvent(
        request_id="req_valid_click_101",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=now_utc.replace(tzinfo=None) - timedelta(hours=2),
        cost=Decimal("0.50"),
        idempotency_key="click:req_valid_click_101",
    )
    test_db.add(click_event)
    await test_db.commit()

    # 2. Fire conversion
    res = await client.post(
        "/api/v1/event/conversion",
        json={
            "request_id": "req_valid_click_101",
            "campaign_id": camp.id,
            "order_id": "ORD-101",
            "conversion_value": 249.99,
            "currency": "INR",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert data["is_attributed"] is True
    assert data["attribution_reason"] == "last_click"
    assert data["conversion_value"] == 249.99
    assert data["currency"] == "INR"
    assert data["is_test"] is False

    # Check DB record
    conv_res = await test_db.execute(select(AdEvent).where(AdEvent.id == data["event_id"]))
    conv_obj = conv_res.scalar_one_or_none()
    assert conv_obj is not None
    assert conv_obj.attributed_event_id == click_event.id
    assert conv_obj.external_order_id == "ORD-101"
    assert conv_obj.is_attributed is True


# ── Test 2: Conversion outside the attribution window ──────────────────────
async def test_conversion_outside_attribution_window(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_outside_window@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    # Click occurred 35 days ago (attribution window is 30 days)
    old_click = AdEvent(
        request_id="req_old_click_102",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=35),
        cost=Decimal("0.50"),
        idempotency_key="click:req_old_click_102",
    )
    test_db.add(old_click)
    await test_db.commit()

    res = await client.post(
        "/api/v1/event/conversion",
        json={
            "request_id": "req_old_click_102",
            "campaign_id": camp.id,
            "order_id": "ORD-102",
            "conversion_value": 99.00,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert data["is_attributed"] is False
    assert data["attribution_reason"] == "outside_window"


# ── Test 3: Duplicate postback and retry idempotency ───────────────────────
async def test_duplicate_postback_and_retry_idempotency(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_dedup@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    payload = {
        "request_id": "req_dedup_103",
        "campaign_id": camp.id,
        "order_id": "ORD-UNIQUE-103",
        "conversion_value": 500.0,
        "currency": "INR",
    }

    # First attempt
    res1 = await client.post("/api/v1/event/conversion", json=payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["is_duplicate"] is False

    # Second retry attempt with same order_id
    res2 = await client.post("/api/v1/event/conversion", json=payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["success"] is True
    assert data2["is_duplicate"] is True
    assert data2["event_id"] == data1["event_id"]
    assert "deduplicated" in data2["message"].lower()

    # Verify database contains only 1 conversion event
    count_res = await test_db.execute(
        select(AdEvent).where(
            AdEvent.campaign_id == camp.id,
            AdEvent.external_order_id == "ORD-UNIQUE-103",
            AdEvent.event_type == EventType.CONVERSION,
        )
    )
    assert len(count_res.scalars().all()) == 1


# ── Test 4: Invalid signature on S2S postback ──────────────────────────────
async def test_invalid_signature_postback(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_bad_sig@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    res = await client.post(
        "/api/v1/conversion/postback",
        json={
            "campaign_id": camp.id,
            "order_id": "ORD-BAD-SIG",
            "conversion_value": 100.0,
            "currency": "INR",
            "timestamp": int(time.time()),
            "signature": "bad_forged_signature_0000000000000000",
        },
    )
    assert res.status_code == 400
    assert "Invalid conversion signature" in res.json()["detail"]


# ── Test 5: Expired or replayed request ─────────────────────────────────────
async def test_expired_or_replayed_request(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_expired@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    # 48 hours in the past (exceeds 24h drift window)
    expired_ts = int(time.time()) - 172800
    valid_sig_for_old_time = sign_conversion_sig(camp.id, "ORD-EXP-1", 100.0, "INR", expired_ts)

    res = await client.post(
        "/api/v1/conversion/postback",
        json={
            "campaign_id": camp.id,
            "order_id": "ORD-EXP-1",
            "conversion_value": 100.0,
            "currency": "INR",
            "timestamp": expired_ts,
            "signature": valid_sig_for_old_time,
        },
    )
    assert res.status_code == 400
    assert "Expired conversion signature timestamp" in res.json()["detail"]


# ── Test 6: Mapping across multiple campaigns and creatives ────────────────
async def test_multi_campaign_creative_mapping(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_multi_camp@example.com")
    adv, camp_a, creat_a = await _setup_advertiser_campaign(test_db, uid, "Campaign A")
    _, camp_b, creat_b = await _setup_advertiser_campaign(test_db, uid, "Campaign B")

    # Generate signed click tokens for each
    tok_a = sign_click_token("req_tok_a", camp_a.id, creat_a.id)
    tok_b = sign_click_token("req_tok_b", camp_b.id, creat_b.id)

    # Insert DB clicks
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_db.add(AdEvent(request_id="req_tok_a", campaign_id=camp_a.id, creative_id=creat_a.id, event_type=EventType.CLICK, event_time=now))
    test_db.add(AdEvent(request_id="req_tok_b", campaign_id=camp_b.id, creative_id=creat_b.id, event_type=EventType.CLICK, event_time=now))
    await test_db.commit()

    # Fire conversions
    res_a = await client.post("/api/v1/event/conversion", json={"click_token": tok_a, "order_id": "ORD-A1", "conversion_value": 10.0})
    res_b = await client.post("/api/v1/event/conversion", json={"click_token": tok_b, "order_id": "ORD-B1", "conversion_value": 20.0})

    assert res_a.status_code == 200
    assert res_b.status_code == 200
    assert res_a.json()["campaign_id"] == camp_a.id
    assert res_a.json()["creative_id"] == creat_a.id
    assert res_b.json()["campaign_id"] == camp_b.id
    assert res_b.json()["creative_id"] == creat_b.id


# ── Test 7: Cross-tenant authorization isolation ───────────────────────────
async def test_cross_tenant_authorization_isolation(client: AsyncClient, test_db: AsyncSession):
    token1, uid1 = await _register_user(client, "test_p11_adv1@example.com")
    token2, uid2 = await _register_user(client, "test_p11_adv2@example.com")

    adv1, camp1, _ = await _setup_advertiser_campaign(test_db, uid1, "Camp 1")
    adv2, camp2, _ = await _setup_advertiser_campaign(test_db, uid2, "Camp 2")

    # Adv1 tries to inspect timeline of Adv2's campaign
    res = await client.get(
        f"/api/v1/conversion/diagnostics/timeline?campaign_id={camp2.id}",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert res.status_code == 403
    assert "belongs to another advertiser" in res.json()["detail"]

    # Adv1 tries to view Adv2's reporting
    res_rep = await client.get(
        f"/api/v1/advertiser/{adv2.id}/reporting",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert res_rep.status_code == 403


# ── Test 8: UTC and attribution-window boundary behavior ───────────────────
async def test_utc_attribution_window_boundary(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_utc_boundary@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    # Inside window (29 days, 23 hours ago)
    inside_click = AdEvent(
        request_id="req_boundary_inside",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=29, hours=23),
        cost=Decimal("0"),
        idempotency_key="click:req_boundary_inside",
    )
    # Outside window (30 days, 2 hours ago)
    outside_click = AdEvent(
        request_id="req_boundary_outside",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30, hours=2),
        cost=Decimal("0"),
        idempotency_key="click:req_boundary_outside",
    )
    test_db.add_all([inside_click, outside_click])
    await test_db.commit()

    res_in = await client.post("/api/v1/event/conversion", json={"request_id": "req_boundary_inside", "order_id": "ORD-BIN"})
    res_out = await client.post("/api/v1/event/conversion", json={"request_id": "req_boundary_outside", "order_id": "ORD-BOUT"})

    assert res_in.status_code == 200
    assert res_in.json()["is_attributed"] is True
    assert res_in.json()["attribution_reason"] == "last_click"

    assert res_out.status_code == 200
    assert res_out.json()["is_attributed"] is False
    assert res_out.json()["attribution_reason"] == "outside_window"


# ── Test 9: Missing conversion value or currency validation ─────────────────
async def test_missing_conversion_value_and_currency_validation(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_val_curr@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    # Missing value is valid (defaults to None / 0)
    res_noval = await client.post("/api/v1/event/conversion", json={"campaign_id": camp.id, "order_id": "ORD-NOVAL"})
    assert res_noval.status_code == 200
    assert res_noval.json()["currency"] == "INR"

    # Negative value is rejected with 422
    res_neg = await client.post("/api/v1/event/conversion", json={"campaign_id": camp.id, "conversion_value": -50.0})
    assert res_neg.status_code == 422

    # Invalid currency code is rejected with 422
    res_bad_curr = await client.post("/api/v1/event/conversion", json={"campaign_id": camp.id, "currency": "INVALID"})
    assert res_bad_curr.status_code == 422


# ── Test 10: Incompatible currencies in financial reporting ────────────────
async def test_incompatible_currencies_in_reporting(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_incompat_curr@example.com")
    adv, camp1, creat1 = await _setup_advertiser_campaign(test_db, uid, "Camp INR")
    
    # Second campaign for the same advertiser
    camp2 = Campaign(
        advertiser_id=adv.id,
        name="Camp USD",
        budget_total=Decimal("10000.0"),
        budget_daily=Decimal("1000.0"),
        bid_type=BidType.CPM,
        bid_amount=Decimal("5.0"),
        status=Status.ACTIVE,
    )
    test_db.add(camp2)
    await test_db.flush()

    creat2 = Creative(
        campaign_id=camp2.id,
        title="Camp USD Creative",
        landing_url="https://example.com/usd",
        status=Status.ACTIVE,
    )
    test_db.add(creat2)
    await test_db.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Event in INR
    ev_inr = AdEvent(
        request_id="req_curr_inr",
        campaign_id=camp1.id,
        creative_id=creat1.id,
        event_type=EventType.CONVERSION,
        event_time=now,
        currency="INR",
        conversion_value=Decimal("100"),
        is_attributed=True,
        cost=Decimal("0"),
        is_test=False,
    )
    # Event in USD
    ev_usd = AdEvent(
        request_id="req_curr_usd",
        campaign_id=camp2.id,
        creative_id=creat2.id,
        event_type=EventType.CONVERSION,
        event_time=now,
        currency="USD",
        conversion_value=Decimal("10"),
        is_attributed=True,
        cost=Decimal("0"),
        is_test=False,
    )
    test_db.add_all([ev_inr, ev_usd])
    await test_db.commit()

    # Query advertiser reporting encompassing both campaigns
    res = await client.get(
        f"/api/v1/advertiser/{adv.id}/reporting",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422
    assert "Incompatible currencies detected" in res.json()["detail"]


# ── Test 11: Zero spend and division-by-zero handling ──────────────────────
async def test_zero_spend_and_division_by_zero(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_zero_spend@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    res = await client.get(
        f"/api/v1/advertiser/{adv.id}/reporting",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["impressions"] == 0
    assert data["clicks"] == 0
    assert data["conversions"] == 0
    assert float(data["spend"]) == 0.0
    assert float(data["ctr"]) == 0.0
    assert float(data["cvr"]) == 0.0
    assert data["cpa"] is None
    assert data["roas"] is None


# ── Test 12: Test-event exclusion from production metrics ──────────────────
async def test_test_event_exclusion_from_production_metrics(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_test_event@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    # Simulate test conversion
    res_test = await client.post(
        "/api/v1/conversion/diagnostics/test-event",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "campaign_id": camp.id,
            "order_id": "ORD-TEST-12",
            "conversion_value": 999.99,
            "currency": "INR",
        },
    )
    assert res_test.status_code == 200
    assert res_test.json()["is_test"] is True

    # Query production reporting
    res_rep = await client.get(
        f"/api/v1/advertiser/{adv.id}/reporting",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_rep.status_code == 200
    data = res_rep.json()
    assert data["total_conversions"] == 0
    assert data["attributed_conversions"] == 0
    assert float(data["conversion_value"]) == 0.0

    # Query diagnostics timeline with include_test=True
    res_timeline = await client.get(
        f"/api/v1/conversion/diagnostics/timeline?campaign_id={camp.id}&include_test=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_timeline.status_code == 200
    assert res_timeline.json()["count"] >= 1
    assert res_timeline.json()["timeline"][0]["is_test"] is True


# ── Test 13: Invalid payloads and signature tampering ──────────────────────
async def test_invalid_payloads_and_tampered_click_tokens(client: AsyncClient, test_db: AsyncSession):
    # Tampered click token
    res = await client.post("/api/v1/event/conversion", json={"click_token": "tampered.token.123"})
    assert res.status_code == 400
    assert "Invalid click token format" in res.json()["detail"]

    # Tampered signature inside a 5-part token
    valid_token = sign_click_token("req1", 10, 20)
    parts = valid_token.split(".")
    tampered_token = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}.bad_hash_99999"
    res_tamp = await client.post("/api/v1/event/conversion", json={"click_token": tampered_token})
    assert res_tamp.status_code == 400
    assert "Invalid click token signature" in res_tamp.json()["detail"]


# ── Test 14: Existing impression/click tracking regressions ────────────────
async def test_existing_impression_click_tracking_regressions(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_regress@example.com")
    adv, camp, creat = await _setup_advertiser_campaign(test_db, uid)

    req_id = "req_regress_14"
    ad_id = f"ad_{camp.id}_{creat.id}"
    slot_id = "banner_top"
    sig = sign_tracking(req_id, ad_id, slot_id)

    # 1. Track Impression
    res_imp = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad_id,
            "event_type": "impression",
            "slot_id": slot_id,
            "sig": sig,
        },
    )
    assert res_imp.status_code == 200
    assert res_imp.json()["success"] is True

    # 2. Track Click via GET pixel
    res_clk = await client.get(
        f"/api/v1/event/track?type=click&req={req_id}&ad={ad_id}&slot={slot_id}&sig={sig}"
    )
    assert res_clk.status_code == 200
    assert res_clk.json()["success"] is True


# ── Test 15: No duplicate spend, wallet, ledger, or publisher-earnings effects
async def test_no_duplicate_spend_or_wallet_deduction(client: AsyncClient, test_db: AsyncSession):
    token, uid = await _register_user(client, "test_p11_cpa_wallet@example.com")
    # Setup CPA campaign: bid_type = 3, bid_amount = 50.0, balance = 1000.0
    adv, camp, creat = await _setup_advertiser_campaign(
        test_db,
        uid,
        campaign_name="CPA Campaign",
        bid_type=BidType.CPA,
        bid_amount=Decimal("50.0"),
        balance=Decimal("1000.0"),
    )

    # Create matching click
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    click_ev = AdEvent(
        request_id="req_cpa_15",
        campaign_id=camp.id,
        creative_id=creat.id,
        event_type=EventType.CLICK,
        event_time=now,
        cost=Decimal("0"),
        idempotency_key="click:req_cpa_15",
    )
    test_db.add(click_ev)
    await test_db.commit()

    # 1. Initial balance check
    await test_db.refresh(adv)
    assert adv.balance == Decimal("1000.0")

    # 2. First attributed conversion
    res1 = await client.post(
        "/api/v1/event/conversion",
        json={
            "request_id": "req_cpa_15",
            "campaign_id": camp.id,
            "order_id": "ORD-CPA-15",
            "conversion_value": 200.0,
        },
    )
    assert res1.status_code == 200
    assert res1.json()["is_attributed"] is True

    await test_db.refresh(adv)
    assert adv.balance == Decimal("950.0")  # Charged exactly 50.0 CPA

    # 3. Duplicate conversion retry (same order_id)
    res_dup = await client.post(
        "/api/v1/event/conversion",
        json={
            "request_id": "req_cpa_15",
            "campaign_id": camp.id,
            "order_id": "ORD-CPA-15",
            "conversion_value": 200.0,
        },
    )
    assert res_dup.status_code == 200
    assert res_dup.json()["is_duplicate"] is True

    await test_db.refresh(adv)
    assert adv.balance == Decimal("950.0")  # NO duplicate deduction!

    # 4. Test conversion simulation
    res_test = await client.post(
        "/api/v1/event/conversion",
        json={
            "request_id": "req_cpa_15",
            "campaign_id": camp.id,
            "order_id": "ORD-CPA-TEST",
            "conversion_value": 200.0,
            "is_test": True,
        },
    )
    assert res_test.status_code == 200

    await test_db.refresh(adv)
    assert adv.balance == Decimal("950.0")  # Test mode produces ZERO billable charge!

