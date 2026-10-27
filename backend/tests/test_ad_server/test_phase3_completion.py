"""
Phase 3 Completion Regression Tests:
- Retry safety after database failure (Work Unit A)
- Concurrency & single-billing guarantees (Work Unit A)
- Durable event-level uniqueness & Redis bypass protection (Work Unit B)
- Billing transaction reference collision avoidance across event types (Work Unit C)
- Stable 400 rejection for unknown event types before side effects (Work Unit D)
"""

import asyncio
from decimal import Decimal
from typing import Any
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.cache import redis_client
from liteads.common.tracking_sig import sign_tracking
from liteads.models import AdEvent, AdSlot, Advertiser, Campaign, Creative, PlatformSettings, Publisher
from liteads.models.finance import Transaction


@pytest.fixture(autouse=True)
def mock_redis_phase3(monkeypatch):
    """In-memory Redis mock supporting incr, expire, ttl, delete."""
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
                ttls.pop(k, None)
                count += 1
        return count

    monkeypatch.setattr(redis_client, "incr", mock_incr)
    monkeypatch.setattr(redis_client, "expire", mock_expire)
    monkeypatch.setattr(redis_client, "ttl", mock_ttl)
    monkeypatch.setattr(redis_client, "delete", mock_delete)
    yield


@pytest.fixture
async def phase3_test_entities(test_db: AsyncSession):
    adv = Advertiser(id=101, name="Phase 3 Adv", balance=Decimal("100.000000"))
    camp = Campaign(
        id=101,
        advertiser_id=101,
        name="Phase 3 Campaign",
        bid_type=1,  # CPM (impression)
        bid_amount=Decimal("2.000000"),  # $2 CPM => $0.002 per impression
        spent_today=Decimal("0.000000"),
        spent_total=Decimal("0.000000"),
    )
    creative1 = Creative(
        id=101,
        campaign_id=101,
        title="Creative 1",
        landing_url="https://example.com/1",
    )
    creative2 = Creative(
        id=102,
        campaign_id=101,
        title="Creative 2",
        landing_url="https://example.com/2",
    )
    pub = Publisher(id=101, name="Phase 3 Pub", balance=Decimal("50.000000"))
    slot = AdSlot(slot_id="slot_p3_1", publisher_id=101, name="Slot Phase 3")
    settings = PlatformSettings(id=10, publisher_rev_share=Decimal("0.700"))

    test_db.add_all([adv, camp, creative1, creative2, pub, slot, settings])
    await test_db.commit()


# ==============================================================================
# Work Unit A: Retry Safety After Database Failure
# ==============================================================================


@pytest.mark.asyncio
async def test_retry_after_database_failure_succeeds(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
    sample_event_request: dict[str, Any],
    monkeypatch,
):
    """
    Submits a valid tracking event.
    Forces first DB attempt to fail before commit.
    Verifies no partial event or billing mutation remains.
    Retries the exact same tracking event -> must succeed.
    Verifies subsequent replay after commit is rejected (429).
    """
    req_id = "req_retry_safety_01"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"
    sig = sign_tracking(req_id, ad_id, slot_id)

    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }

    # 1. Force first DB attempt to fail during flush
    from liteads.ad_server.services.event_service import EventService

    orig_flush = test_db.flush
    fail_once = True

    async def flush_fail_once(*args, **kwargs):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise SQLAlchemyError("Simulated database failure during flush")
        return await orig_flush(*args, **kwargs)

    monkeypatch.setattr(test_db, "flush", flush_fail_once)

    res1 = await client.post("/api/v1/event/track", json=payload)
    # Failed attempt should return 200 with success=False or an error, but not committed
    assert res1.json()["success"] is False

    # Verify no partial event or billing mutation exists
    adv = await test_db.get(Advertiser, 101)
    await test_db.refresh(adv)
    assert adv.balance == Decimal("100.000000")

    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 0

    txs = (
        await test_db.execute(
            select(Transaction).where(Transaction.advertiser_id == 101)
        )
    ).scalars().all()
    assert len(txs) == 0

    # 2. Retry the exact same legitimate tracking request -> MUST SUCCEED
    res2 = await client.post("/api/v1/event/track", json=payload)
    assert res2.status_code == 200
    assert res2.json()["success"] is True

    # Verify billing mutation occurred
    await test_db.refresh(adv)
    # CPM = $2.00 / 1000 = $0.002
    assert adv.balance == Decimal("99.998000")

    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 1
    assert events[0].idempotency_key == f"{req_id}:101:1:1"

    txs = (
        await test_db.execute(
            select(Transaction).where(Transaction.advertiser_id == 101)
        )
    ).scalars().all()
    assert len(txs) == 1
    assert txs[0].reference_id == f"{req_id}:101:1:1"

    # 3. Third request is a replay after successful persistence -> REJECTED (429)
    res3 = await client.post("/api/v1/event/track", json=payload)
    assert res3.status_code == 429
    assert res3.json()["detail"] == "Duplicate event rejected"

    # Balances remained unchanged
    await test_db.refresh(adv)
    assert adv.balance == Decimal("99.998000")


@pytest.mark.asyncio
async def test_concurrent_identical_requests_single_billing(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
):
    """
    Fire 5 concurrent identical impression requests.
    Exactly 1 must succeed, 4 must be rejected with 429.
    Advertiser is billed exactly once.
    """
    req_id = "req_concurrent_p3_01"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"
    sig = sign_tracking(req_id, ad_id, slot_id)

    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }

    tasks = [client.post("/api/v1/event/track", json=payload) for _ in range(5)]
    responses = await asyncio.gather(*tasks)

    success_count = sum(1 for r in responses if r.status_code == 200 and r.json().get("success") is True)
    fail_count = sum(1 for r in responses if r.status_code == 429)

    assert success_count == 1
    assert fail_count == 4

    adv = await test_db.get(Advertiser, 101)
    await test_db.refresh(adv)
    assert adv.balance == Decimal("99.998000")

    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 1

    txs = (
        await test_db.execute(
            select(Transaction).where(Transaction.advertiser_id == 101)
        )
    ).scalars().all()
    assert len(txs) == 1


# ==============================================================================
# Work Unit B: Durable Event-Level Uniqueness & Redis Bypass Protection
# ==============================================================================


@pytest.mark.asyncio
async def test_db_unique_constraint_enforced_when_redis_bypassed(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
    monkeypatch,
):
    """
    Even if Redis replay guard is completely bypassed or returns 0 (e.g. cache flush),
    the database-level idempotency key prevents duplicate event persistence and duplicate billing.
    """
    req_id = "req_redis_bypass_01"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"
    sig = sign_tracking(req_id, ad_id, slot_id)

    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }

    # 1. Normal first impression
    res1 = await client.post("/api/v1/event/track", json=payload)
    assert res1.status_code == 200
    assert res1.json()["success"] is True
    await test_db.commit()

    # 2. Simulate Redis state loss / flush by wiping the mock redis store
    # Monkeypatch redis incr to return 0 so replay guard does not block at Redis level
    async def mock_incr_always_zero(key: str, amount: int = 1):
        return 0

    monkeypatch.setattr(redis_client, "incr", mock_incr_always_zero)

    # 3. Second request passes Redis, hits Database unique constraint
    res2 = await client.post("/api/v1/event/track", json=payload)
    assert res2.status_code == 429
    assert res2.json()["detail"] == "Duplicate event rejected"

    # Verify no double charge
    adv = await test_db.get(Advertiser, 101)
    await test_db.refresh(adv)
    assert adv.balance == Decimal("99.998000")

    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 1


# ==============================================================================
# Work Unit C: Billing Transaction Reference Collision Avoidance
# ==============================================================================


@pytest.mark.asyncio
async def test_billing_reference_no_collision_across_event_types(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
):
    """
    Verifies that distinct legitimate billable event types (impression and click)
    for the exact same request_id do NOT collide on Transaction.reference_id.
    """
    req_id = "req_bill_collision_01"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"

    # Make campaign bill clicks as well by changing bid_type to CPC temporarily
    camp = await test_db.get(Campaign, 101)
    camp.bid_type = 1  # CPM for impression
    await test_db.commit()

    sig = sign_tracking(req_id, ad_id, slot_id)

    # 1. Impression event
    res_imp = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad_id,
            "event_type": "impression",
            "slot_id": slot_id,
            "sig": sig,
            "extra": {"slot_id": slot_id},
        },
    )
    assert res_imp.status_code == 200
    assert res_imp.json()["success"] is True

    # Update campaign to CPC for click test so click also generates a billable charge
    camp.bid_type = 2  # CPC
    camp.bid_amount = Decimal("0.500000")
    await test_db.commit()

    # 2. Click event for the EXACT SAME request_id
    res_clk = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad_id,
            "event_type": "click",
            "slot_id": slot_id,
            "sig": sig,
            "extra": {"slot_id": slot_id},
        },
    )
    assert res_clk.status_code == 200
    assert res_clk.json()["success"] is True

    # Verify both transactions were persisted with distinct, stable reference_ids
    txs = (
        await test_db.execute(
            select(Transaction)
            .where(Transaction.advertiser_id == 101)
            .order_by(Transaction.id.asc())
        )
    ).scalars().all()

    assert len(txs) == 2
    assert txs[0].reference_id == f"{req_id}:101:1:1"
    assert txs[1].reference_id == f"{req_id}:101:2:1"
    assert txs[0].amount == Decimal("-0.002000")
    assert txs[1].amount == Decimal("-0.500000")

    # Verify 2 distinct AdEvents were persisted
    events = (
        await test_db.execute(
            select(AdEvent)
            .where(AdEvent.request_id == req_id)
            .order_by(AdEvent.id.asc())
        )
    ).scalars().all()
    assert len(events) == 2
    assert events[0].idempotency_key == f"{req_id}:101:1:1"
    assert events[1].idempotency_key == f"{req_id}:101:2:1"


@pytest.mark.asyncio
async def test_distinct_creatives_same_request_do_not_collide(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
):
    """
    In multi-ad responses, different creatives served under the same request_id
    can both record events and charges without collision.
    """
    req_id = "req_multi_ad_01"
    ad1 = "ad_101_101"
    ad2 = "ad_101_102"
    slot_id = "slot_p3_1"

    sig1 = sign_tracking(req_id, ad1, slot_id)
    sig2 = sign_tracking(req_id, ad2, slot_id)

    res1 = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad1,
            "event_type": "impression",
            "slot_id": slot_id,
            "sig": sig1,
            "extra": {"slot_id": slot_id},
        },
    )
    assert res1.status_code == 200

    res2 = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad2,
            "event_type": "impression",
            "slot_id": slot_id,
            "sig": sig2,
            "extra": {"slot_id": slot_id},
        },
    )
    assert res2.status_code == 200

    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 2
    keys = {e.idempotency_key for e in events}
    assert f"{req_id}:101:1:1" in keys
    assert f"{req_id}:102:1:1" in keys


# ==============================================================================
# Work Unit D: Stable 400 Response for Unknown Event Types
# ==============================================================================


@pytest.mark.asyncio
async def test_unknown_event_type_rejected_http_400_post(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
):
    """
    POST with unknown event type must return HTTP 400 before replay guard or side effects.
    """
    req_id = "req_unknown_type_post"
    ad_id = "ad_101_101"
    sig = sign_tracking(req_id, ad_id, "")

    res = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad_id,
            "event_type": "unknown_event_type",
            "sig": sig,
        },
    )
    assert res.status_code == 400
    assert "invalid or unknown event type" in res.json()["detail"].lower()

    # Verify no replay allowance was consumed: legitimate impression can still proceed
    res_valid = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad_id,
            "event_type": "impression",
            "sig": sig,
        },
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["success"] is True


@pytest.mark.asyncio
async def test_unknown_event_type_rejected_http_400_get(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
):
    """
    GET with unknown event type must return HTTP 400 before replay guard or side effects.
    """
    req_id = "req_unknown_type_get"
    ad_id = "ad_101_101"
    sig = sign_tracking(req_id, ad_id, "")

    res = await client.get(
        "/api/v1/event/track",
        params={
            "type": "bogus_type",
            "req": req_id,
            "ad": ad_id,
            "sig": sig,
        },
    )
    assert res.status_code == 400
    assert "invalid or unknown event type" in res.json()["detail"].lower()

    # Legitimate event still succeeds
    res_valid = await client.get(
        "/api/v1/event/track",
        params={
            "type": "impression",
            "req": req_id,
            "ad": ad_id,
            "sig": sig,
        },
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["success"] is True


# ==============================================================================
# Cross-Cutting & Migration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_tampered_or_invalid_signature_rejected_with_400(
    client: AsyncClient,
    phase3_test_entities,
):
    """
    Tracking signatures must be verified and reject tampered, missing, or expired tokens.
    """
    req_id = "req_sig_check_01"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"

    # 1. Missing signature
    res1 = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad_id,
            "event_type": "impression",
            "slot_id": slot_id,
        },
    )
    assert res1.status_code == 400
    assert "missing signature" in res1.json()["detail"].lower()

    # 2. Tampered signature
    valid_sig = sign_tracking(req_id, ad_id, slot_id)
    tampered_sig = valid_sig[:-4] + "dead"
    res2 = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad_id,
            "event_type": "impression",
            "slot_id": slot_id,
            "sig": tampered_sig,
        },
    )
    assert res2.status_code == 400
    assert "bad signature" in res2.json()["detail"].lower()

    # 3. Signature for different ad_id
    diff_sig = sign_tracking(req_id, "ad_101_999", slot_id)
    res3 = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": req_id,
            "ad_id": ad_id,
            "event_type": "impression",
            "slot_id": slot_id,
            "sig": diff_sig,
        },
    )
    assert res3.status_code == 400
    assert "bad signature" in res3.json()["detail"].lower()


@pytest.mark.asyncio
async def test_uniqueness_prevents_duplicate_non_billable_events(test_db: AsyncSession):
    """
    Verifies that non-billable events (cost = 0) with no transaction ledger entry
    are still durably protected from duplication at the database level via AdEvent.idempotency_key.
    """
    from datetime import datetime, timezone
    from sqlalchemy.exc import IntegrityError

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    event1 = AdEvent(
        request_id="req_free_01",
        campaign_id=None,
        creative_id=None,
        event_type=1,
        event_time=now,
        cost=Decimal("0.000000"),
        idempotency_key="req_free_01:0:1:1",
    )
    test_db.add(event1)
    await test_db.commit()

    # Attempting to insert another event with the same idempotency key must raise IntegrityError
    event2 = AdEvent(
        request_id="req_free_01",
        campaign_id=None,
        creative_id=None,
        event_type=1,
        event_time=now,
        cost=Decimal("0.000000"),
        idempotency_key="req_free_01:0:1:1",
    )
    test_db.add(event2)
    with pytest.raises(IntegrityError):
        await test_db.commit()

    await test_db.rollback()


@pytest.mark.asyncio
async def test_migration_historical_deduplication_preserves_records(test_db: AsyncSession):
    """
    Tests the migration backfill logic on synthetic historical duplicate rows:
    - Verifies zero rows are deleted
    - Verifies all rows receive deterministic unique idempotency_key values
    - Verifies duplicate rows receive deterministic disambiguation suffixes (:dup:{id})
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Insert 3 historical rows with the same (request_id, creative_id, event_type) and NULL idempotency_key
    ev1 = AdEvent(request_id="req_hist_01", creative_id=50, event_type=1, event_time=now, idempotency_key=None)
    ev2 = AdEvent(request_id="req_hist_01", creative_id=50, event_type=1, event_time=now, idempotency_key=None)
    ev3 = AdEvent(request_id="req_hist_01", creative_id=50, event_type=1, event_time=now, idempotency_key=None)

    test_db.add_all([ev1, ev2, ev3])
    await test_db.commit()
    await test_db.refresh(ev1)
    await test_db.refresh(ev2)
    await test_db.refresh(ev3)

    # Run the exact backfill algorithm from migration e6f7a8b9c0d1
    from sqlalchemy import text
    rows = (await test_db.execute(
        text("SELECT id, request_id, creative_id, event_type FROM ad_events WHERE request_id = 'req_hist_01' ORDER BY id ASC")
    )).fetchall()

    assert len(rows) == 3

    seen_counts: dict[str, int] = {}
    for r in rows:
        row_id = r[0]
        req_id = r[1]
        creative_id = r[2] if r[2] is not None else 0
        event_type = r[3]
        base_key = f"{req_id}:{creative_id}:{event_type}"

        seen_counts[base_key] = seen_counts.get(base_key, 0) + 1
        seq = seen_counts[base_key]
        if seq == 1:
            final_key = f"{base_key}:1"
        else:
            final_key = f"{base_key}:{seq}:dup:{row_id}"

        await test_db.execute(
            text("UPDATE ad_events SET idempotency_key = :k WHERE id = :id"),
            {"k": final_key, "id": row_id},
        )
    await test_db.commit()
    test_db.expire_all()

    # Re-query all rows and verify keys
    updated_rows = (await test_db.execute(
        select(AdEvent).where(AdEvent.request_id == "req_hist_01").order_by(AdEvent.id.asc())
    )).scalars().all()


    assert len(updated_rows) == 3  # ZERO ROWS DELETED
    assert updated_rows[0].idempotency_key == "req_hist_01:50:1:1"
    assert updated_rows[1].idempotency_key == f"req_hist_01:50:1:2:dup:{updated_rows[1].id}"
    assert updated_rows[2].idempotency_key == f"req_hist_01:50:1:3:dup:{updated_rows[2].id}"

    # Keys must be distinct
    all_keys = [r.idempotency_key for r in updated_rows]
    assert len(set(all_keys)) == 3


# ==============================================================================
# Phase 3 Final Hardening: Failure-Path & Concurrency Regression Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_concurrent_click_events_no_sequence_collision(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
):
    """
    Scenario 1:
    Two or more concurrent click events with replay limit greater than one:
    Verify that concurrent legitimate click events take distinct sequence numbers
    (seq=1, 2, 3), do not collide on the database unique constraint, and all
    permitted clicks succeed without duplicate billing or premature rejection.
    A 4th click beyond the limit is rejected with HTTP 429.
    """
    req_id = "req_concurrent_clicks_multi_slot"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"

    # Set campaign to CPC ($0.50 per click)
    camp = await test_db.get(Campaign, 101)
    camp.bid_type = 2  # CPC
    camp.bid_amount = Decimal("0.500000")
    await test_db.commit()

    sig = sign_tracking(req_id, ad_id, slot_id)
    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "click",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }

    from sqlalchemy.orm import sessionmaker
    from liteads.common.database import get_session
    from liteads.ad_server.main import app

    session_factory = sessionmaker(test_db.bind, class_=AsyncSession, expire_on_commit=False)

    async def per_request_session():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_session] = per_request_session

    try:
        # Fire 3 concurrent click requests (limit = 3)
        tasks = [client.post("/api/v1/event/track", json=payload) for _ in range(3)]
        responses = await asyncio.gather(*tasks)
    finally:
        async def default_override():
            yield test_db
        app.dependency_overrides[get_session] = default_override

    for r in responses:
        assert r.status_code == 200
        assert r.json()["success"] is True

    # Verify 3 distinct AdEvents persisted with distinct sequence numbers :1, :2, :3
    events = (
        await test_db.execute(
            select(AdEvent)
            .where(AdEvent.request_id == req_id)
            .order_by(AdEvent.id.asc())
        )
    ).scalars().all()
    assert len(events) == 3
    event_keys = {e.idempotency_key for e in events}
    assert event_keys == {
        f"{req_id}:101:2:1",
        f"{req_id}:101:2:2",
        f"{req_id}:101:2:3",
    }

    # Verify 3 distinct billing ledger transactions
    txs = (
        await test_db.execute(
            select(Transaction)
            .where(Transaction.advertiser_id == 101)
            .order_by(Transaction.id.asc())
        )
    ).scalars().all()
    assert len(txs) == 3
    tx_refs = {t.reference_id for t in txs}
    assert tx_refs == {
        f"{req_id}:101:2:1",
        f"{req_id}:101:2:2",
        f"{req_id}:101:2:3",
    }
    total_debited = sum(t.amount for t in txs)
    assert total_debited == Decimal("-1.500000")

    # A 4th click attempt arrives -> must be rejected with 429
    res4 = await client.post("/api/v1/event/track", json=payload)
    assert res4.status_code == 429
    assert res4.json()["detail"] == "Duplicate event rejected"


@pytest.mark.asyncio
async def test_db_failure_before_commit_click_retry_succeeds(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
    monkeypatch,
):
    """
    Scenario 2:
    A database write fails before commit, followed by a retry of the same logical event:
    Verify that the retry succeeds without duplicate billing, correctly releasing
    the in-flight lease so the retry can reuse the slot cleanly.
    """
    req_id = "req_click_db_fail_retry"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"

    camp = await test_db.get(Campaign, 101)
    camp.bid_type = 2  # CPC
    camp.bid_amount = Decimal("0.750000")
    await test_db.commit()

    sig = sign_tracking(req_id, ad_id, slot_id)
    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "click",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }

    # Force DB attempt to fail during flush
    orig_flush = test_db.flush
    fail_first = True

    async def fail_flush_once(*args, **kwargs):
        nonlocal fail_first
        if fail_first:
            fail_first = False
            raise SQLAlchemyError("Simulated DB failure before commit")
        return await orig_flush(*args, **kwargs)

    monkeypatch.setattr(test_db, "flush", fail_flush_once)

    # Attempt 1 fails
    res1 = await client.post("/api/v1/event/track", json=payload)
    assert res1.json()["success"] is False

    # Verify no partial debit
    adv = await test_db.get(Advertiser, 101)
    await test_db.refresh(adv)
    assert adv.balance == Decimal("100.000000")

    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 0

    # Attempt 2 (Retry of the same logical event) MUST SUCCEED
    res2 = await client.post("/api/v1/event/track", json=payload)
    assert res2.status_code == 200
    assert res2.json()["success"] is True

    # Verify debited exactly once
    await test_db.refresh(adv)
    assert adv.balance == Decimal("99.250000")

    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 1
    assert events[0].idempotency_key == f"{req_id}:101:2:1"


@pytest.mark.asyncio
async def test_commit_succeeds_redis_finalize_failure_replay_prevented(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
    monkeypatch,
):
    """
    Scenario 3:
    The database commit succeeds but reservation.finalize() raises or Redis becomes unavailable:
    Verify that replaying the same logical event cannot create another event or ledger debit.
    PostgreSQL unique constraint rejects the duplicate, and the system returns HTTP 429.
    """
    req_id = "req_finalize_fail_replay"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"

    sig = sign_tracking(req_id, ad_id, slot_id)
    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }

    # 1. First request succeeds in DB, but reservation.finalize() raises an exception
    from liteads.ad_server.services.replay_guard import ReplayReservation
    orig_finalize = ReplayReservation.finalize
    raised_once = False

    async def finalize_with_error(self):
        nonlocal raised_once
        if not raised_once:
            raised_once = True
            # Simulate Redis failure during finalize
            raise RuntimeError("Simulated Redis network timeout during finalize")
        return await orig_finalize(self)

    monkeypatch.setattr(ReplayReservation, "finalize", finalize_with_error)

    # First request raises because finalize failed AFTER DB flush
    try:
        await client.post("/api/v1/event/track", json=payload)
    except RuntimeError:
        pass

    # Ensure DB committed the initial event
    await test_db.commit()

    # Verify DB has 1 event and 1 transaction
    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 1
    assert events[0].idempotency_key == f"{req_id}:101:1:1"

    adv = await test_db.get(Advertiser, 101)
    await test_db.refresh(adv)
    # 100.00 - 0.002 = 99.998
    assert adv.balance == Decimal("99.998000")

    # Restore finalize
    monkeypatch.setattr(ReplayReservation, "finalize", orig_finalize)

    # 2. Client replays the request -> MUST BE REJECTED (HTTP 429)
    # Even if Redis was unfinalized, DB idempotency constraint protects from duplicate billing
    res_replay = await client.post("/api/v1/event/track", json=payload)
    assert res_replay.status_code == 429
    assert res_replay.json()["detail"] == "Duplicate event rejected"

    # Verify still exactly 1 event and 1 charge (NO DUPLICATE BILLING)
    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 1

    await test_db.refresh(adv)
    assert adv.balance == Decimal("99.998000")


@pytest.mark.asyncio
async def test_unrelated_db_integrity_failure_not_misclassified_as_duplicate(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
    monkeypatch,
):
    """
    Scenario 4:
    An unrelated database integrity failure, such as a foreign-key violation,
    is not misclassified as a duplicate-event conflict.
    Verifies that it returns success=False (HTTP 200), NOT HTTP 429,
    and releases the reservation properly.
    """
    req_id = "req_unrelated_fk_fail"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"

    sig = sign_tracking(req_id, ad_id, slot_id)
    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }

    # Simulate an unrelated IntegrityError (e.g. foreign key constraint failure)
    orig_flush = test_db.flush

    async def flush_fk_violation(*args, **kwargs):
        from sqlalchemy.exc import IntegrityError
        raise IntegrityError(
            "INSERT INTO ad_events ...",
            params={},
            orig=Exception("FOREIGN KEY constraint failed: foreign key violation on table ad_events"),
        )

    monkeypatch.setattr(test_db, "flush", flush_fk_violation)

    res = await client.post("/api/v1/event/track", json=payload)
    # MUST NOT be 429 (not a duplicate conflict)
    assert res.status_code == 200
    assert res.json()["success"] is False
    assert "failed to record event" in res.json()["message"].lower()

    # Verify reservation was released and not permanently locked
    monkeypatch.setattr(test_db, "flush", orig_flush)
    res_retry = await client.post("/api/v1/event/track", json=payload)
    assert res_retry.status_code == 200
    assert res_retry.json()["success"] is True


@pytest.mark.asyncio
async def test_concurrent_retries_cannot_create_duplicate_billable_events(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
):
    """
    Scenario 5:
    Concurrent retries of the same logical event cannot create duplicate billable events.
    Fires 5 simultaneous requests for the exact same impression (limit = 1).
    Exactly 1 request succeeds; 4 are rejected with 429.
    Advertiser is debited exactly once.
    """
    req_id = "req_concurrent_retries_single_charge"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"

    sig = sign_tracking(req_id, ad_id, slot_id)
    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }

    tasks = [client.post("/api/v1/event/track", json=payload) for _ in range(5)]
    responses = await asyncio.gather(*tasks)

    successes = [r for r in responses if r.status_code == 200 and r.json().get("success") is True]
    duplicates = [r for r in responses if r.status_code == 429]

    assert len(successes) == 1
    assert len(duplicates) == 4

    # Verify single debit
    adv = await test_db.get(Advertiser, 101)
    await test_db.refresh(adv)
    assert adv.balance == Decimal("99.998000")

    events = (
        await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    ).scalars().all()
    assert len(events) == 1
    assert events[0].idempotency_key == f"{req_id}:101:1:1"


@pytest.mark.asyncio
async def test_all_existing_impression_and_conversion_replay_protections_intact(
    client: AsyncClient,
    test_db: AsyncSession,
    phase3_test_entities,
):
    """
    Scenario 6:
    All existing impression and conversion replay protections continue to work:
    - Impression allows 1 event; subsequent replays return 429.
    - Conversion allows 1 event; subsequent replays return 429.
    - Neither event allows double-billing or extra event records.
    """
    req_id = "req_all_replay_protections_intact"
    ad_id = "ad_101_101"
    slot_id = "slot_p3_1"

    sig = sign_tracking(req_id, ad_id, slot_id)

    # 1. Impression event
    imp_payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }
    r1 = await client.post("/api/v1/event/track", json=imp_payload)
    assert r1.status_code == 200
    assert r1.json()["success"] is True

    # Replay impression -> 429
    r2 = await client.post("/api/v1/event/track", json=imp_payload)
    assert r2.status_code == 429
    assert r2.json()["detail"] == "Duplicate event rejected"

    # 2. Conversion event for same ad request
    conv_payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "conversion",
        "slot_id": slot_id,
        "sig": sig,
        "extra": {"slot_id": slot_id},
    }
    c1 = await client.post("/api/v1/event/track", json=conv_payload)
    assert c1.status_code == 200
    assert c1.json()["success"] is True

    # Replay conversion -> 429
    c2 = await client.post("/api/v1/event/track", json=conv_payload)
    assert c2.status_code == 429
    assert c2.json()["detail"] == "Duplicate event rejected"

    # Verify AdEvents in DB: exactly 1 impression and 1 conversion
    events = (
        await test_db.execute(
            select(AdEvent)
            .where(AdEvent.request_id == req_id)
            .order_by(AdEvent.id.asc())
        )
    ).scalars().all()
    assert len(events) == 2
    types = {e.event_type for e in events}
    assert types == {1, 3}  # IMPRESSION=1, CONVERSION=3


