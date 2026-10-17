"""Tests for Phase 6 Work Unit 1 (WU1):
Per-Slot Daily Statistics and Event Ingestion Fix.

Covers:
1. Impression tracking stores the correct non-null slot_id.
2. Click tracking updates the correct slot's daily row.
3. Two slots belonging to the same publisher can have separate rows for the same date.
4. Repeated events update the appropriate row and preserve metric semantics.
5. Publisher reporting combines multiple slot rows into exactly one entry per date, with correct sums.
6. Historical NULL-slot rows are preserved and included in reports.
7. An event without a valid slot ID does not create attributable publisher statistics or crash unexpectedly.
8. An unknown slot ID does not create a dangling statistics record.
9. POST /api/v1/event propagates a valid root-level slot_id.
10. Concurrent first events for the same slot/date do not cause an integrity error or lose increments.
11. Existing event idempotency/replay protections still work.
12. Model metadata contains the expected constraint and index.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import Index, UniqueConstraint, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.main import app
from liteads.ad_server.services.event_service import EventService
from liteads.common.database import get_session
from liteads.common.security import current_user
from liteads.common.tracking_sig import sign_tracking
from liteads.models.ad import Advertiser, Campaign, Creative
from liteads.models.auth import User
from liteads.models.base import CreativeType, ModerationStatus, Status
from liteads.models.domain import AdvertiserDomain, VerificationStatus
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat
from liteads.common.cache import redis_client


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    store = {}
    ttls = {}

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

    monkeypatch.setattr(redis_client, "incr", mock_incr)
    monkeypatch.setattr(redis_client, "expire", mock_expire)
    monkeypatch.setattr(redis_client, "ttl", mock_ttl)
    yield


@pytest.fixture(autouse=True)
async def setup_test_entities(test_db: AsyncSession):
    """Seed test publisher, advertiser, campaigns, and slots."""
    # Seed publisher
    pub = Publisher(id=101, name="Acme Publishing", status=1, is_sandbox=False, balance=Decimal("100.00"))
    test_db.add(pub)

    # Seed slots for publisher 101
    slot1 = AdSlot(
        id=201,
        publisher_id=101,
        slot_id="slot_header_banner",
        name="Header Banner",
        format=1,
        width=728,
        height=90,
        status=1,
    )
    slot2 = AdSlot(
        id=202,
        publisher_id=101,
        slot_id="slot_sidebar_rect",
        name="Sidebar Rectangle",
        format=1,
        width=300,
        height=250,
        status=1,
    )
    test_db.add_all([slot1, slot2])

    # Seed advertiser and campaign
    adv = Advertiser(id=301, name="Global Brand", balance=Decimal("1000.00"), daily_budget=Decimal("500.00"), status=1)
    test_db.add(adv)

    domain = AdvertiserDomain(advertiser_id=301, domain="example.com", status=VerificationStatus.VERIFIED, verification_token="tok_123")
    test_db.add(domain)

    camp = Campaign(
        id=401,
        advertiser_id=301,
        name="Q4 Growth",
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        spent_today=Decimal("0.00"),
        spent_total=Decimal("0.00"),
        bid_type=1,
        bid_amount=Decimal("2.00"),
        status=1,
    )
    test_db.add(camp)

    c1 = Creative(
        id=501,
        campaign_id=401,
        title="Creative 1",
        landing_url="https://example.com/landing",
        image_url="https://example.com/ad.png",
        creative_type=CreativeType.BANNER,
        moderation_status=ModerationStatus.APPROVED.value,
        status=Status.ACTIVE,
    )
    test_db.add(c1)

    await test_db.commit()


@pytest.mark.asyncio
async def test_impression_tracking_stores_correct_slot_id(test_db: AsyncSession):
    """1. Impression tracking stores the correct non-null slot_id."""
    service = EventService(session=test_db)
    success = await service.track_event(
        request_id="req_test_1",
        ad_id="ad_401_501",
        event_type="impression",
        extra={"slot_id": "slot_header_banner"},
    )
    assert success is True
    await test_db.commit()

    today = datetime.now(timezone.utc).date()
    result = await test_db.execute(
        select(PublisherDailyStat).where(
            PublisherDailyStat.publisher_id == 101,
            PublisherDailyStat.slot_id == 201,
            PublisherDailyStat.stat_date == today,
        )
    )
    stat = result.scalar_one_or_none()
    assert stat is not None
    assert stat.slot_id == 201
    assert stat.publisher_id == 101
    assert stat.impressions == 1
    assert stat.clicks == 0
    assert stat.earnings > Decimal("0")


@pytest.mark.asyncio
async def test_click_tracking_updates_correct_slot_daily_row(test_db: AsyncSession):
    """2. Click tracking updates the correct slot's daily row."""
    service = EventService(session=test_db)
    # First track impression
    await service.track_event(
        request_id="req_test_2a",
        ad_id="ad_401_501",
        event_type="impression",
        extra={"slot_id": "slot_sidebar_rect"},
    )
    # Then track click
    await service.track_event(
        request_id="req_test_2b",
        ad_id="ad_401_501",
        event_type="click",
        extra={"slot_id": "slot_sidebar_rect"},
    )
    await test_db.commit()

    today = datetime.now(timezone.utc).date()
    result = await test_db.execute(
        select(PublisherDailyStat).where(
            PublisherDailyStat.publisher_id == 101,
            PublisherDailyStat.slot_id == 202,
            PublisherDailyStat.stat_date == today,
        )
    )
    stat = result.scalar_one()
    assert stat.slot_id == 202
    assert stat.impressions == 1
    assert stat.clicks == 1


@pytest.mark.asyncio
async def test_two_slots_same_publisher_same_date(test_db: AsyncSession):
    """3. Two slots belonging to the same publisher can have separate rows for the same date."""
    service = EventService(session=test_db)
    # Track on slot 1
    await service.track_event(
        request_id="req_slot1",
        ad_id="ad_401_501",
        event_type="impression",
        extra={"slot_id": "slot_header_banner"},
    )
    # Track on slot 2
    await service.track_event(
        request_id="req_slot2",
        ad_id="ad_401_501",
        event_type="impression",
        extra={"slot_id": "slot_sidebar_rect"},
    )
    await test_db.commit()

    today = datetime.now(timezone.utc).date()
    result = await test_db.execute(
        select(PublisherDailyStat).where(
            PublisherDailyStat.publisher_id == 101,
            PublisherDailyStat.stat_date == today,
        ).order_by(PublisherDailyStat.slot_id)
    )
    rows = result.scalars().all()
    assert len(rows) == 2
    assert rows[0].slot_id == 201
    assert rows[1].slot_id == 202


@pytest.mark.asyncio
async def test_repeated_events_preserve_metric_semantics(test_db: AsyncSession):
    """4. Repeated events update the appropriate row and preserve metric semantics."""
    service = EventService(session=test_db)
    for i in range(5):
        await service.track_event(
            request_id=f"req_rep_imp_{i}",
            ad_id="ad_401_501",
            event_type="impression",
            extra={"slot_id": "slot_header_banner"},
        )
    for i in range(2):
        await service.track_event(
            request_id=f"req_rep_clk_{i}",
            ad_id="ad_401_501",
            event_type="click",
            extra={"slot_id": "slot_header_banner"},
        )
    await test_db.commit()

    today = datetime.now(timezone.utc).date()
    result = await test_db.execute(
        select(PublisherDailyStat).where(
            PublisherDailyStat.publisher_id == 101,
            PublisherDailyStat.slot_id == 201,
            PublisherDailyStat.stat_date == today,
        )
    )
    stat = result.scalar_one()
    assert stat.impressions == 5
    assert stat.clicks == 2


@pytest.mark.asyncio
async def test_publisher_reporting_combines_multiple_slots_into_one_entry_per_date(client: AsyncClient, test_db: AsyncSession):
    """5. Publisher reporting combines multiple slot rows into exactly one entry per date, with correct sums."""
    today = datetime.now(timezone.utc).date()

    # Create 2 separate slot rows for today
    stat1 = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=today,
        impressions=100,
        clicks=10,
        conversions=2,
        earnings=Decimal("15.500000"),
    )
    stat2 = PublisherDailyStat(
        publisher_id=101,
        slot_id=202,
        stat_date=today,
        impressions=200,
        clicks=25,
        conversions=5,
        earnings=Decimal("30.000000"),
    )
    test_db.add_all([stat1, stat2])
    await test_db.commit()

    # Authenticate as publisher 101
    async def mock_pub_user():
        return User(id=999, role="publisher", publisher_id=101, email="pub@test.com")

    app.dependency_overrides[current_user] = mock_pub_user
    try:
        res = await client.get(f"/api/v1/publisher/101/reporting?start_date={today}&end_date={today}")
        assert res.status_code == 200
        data = res.json()

        # Overall totals
        assert data["impressions"] == 300
        assert data["clicks"] == 35
        assert data["conversions"] == 7
        assert float(data["earnings"]) == 45.5

        # Exactly 1 daily breakdown entry for today
        assert len(data["daily_breakdown"]) == 1
        day_entry = data["daily_breakdown"][0]
        assert day_entry["stat_date"] == str(today)
        assert day_entry["impressions"] == 300
        assert day_entry["clicks"] == 35
        assert day_entry["conversions"] == 7
        assert float(day_entry["earnings"]) == 45.5
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_historical_null_slot_rows_included_in_reports(client: AsyncClient, test_db: AsyncSession):
    """6. Historical NULL-slot rows are preserved and included in reports."""
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    legacy_stat = PublisherDailyStat(
        publisher_id=101,
        slot_id=None,  # Legacy row
        stat_date=yesterday,
        impressions=50,
        clicks=5,
        conversions=1,
        earnings=Decimal("7.000000"),
    )
    test_db.add(legacy_stat)
    await test_db.commit()

    async def mock_pub_user():
        return User(id=999, role="publisher", publisher_id=101, email="pub@test.com")

    app.dependency_overrides[current_user] = mock_pub_user
    try:
        res = await client.get(f"/api/v1/publisher/101/reporting?start_date={yesterday}&end_date={yesterday}")
        assert res.status_code == 200
        data = res.json()
        assert data["impressions"] == 50
        assert len(data["daily_breakdown"]) == 1
        assert data["daily_breakdown"][0]["impressions"] == 50
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_event_without_slot_id_gracefully_handled(test_db: AsyncSession):
    """7. An event without a valid slot ID does not create attributable publisher statistics or crash."""
    service = EventService(session=test_db)
    success = await service.track_event(
        request_id="req_no_slot",
        ad_id="ad_401_501",
        event_type="impression",
        extra={},  # No slot_id
    )
    assert success is True
    await test_db.commit()

    today = datetime.now(timezone.utc).date()
    result = await test_db.execute(
        select(PublisherDailyStat).where(PublisherDailyStat.publisher_id == 101, PublisherDailyStat.stat_date == today)
    )
    stats = result.scalars().all()
    assert len(stats) == 0


@pytest.mark.asyncio
async def test_event_with_unknown_slot_id(test_db: AsyncSession):
    """8. An unknown slot ID does not create a dangling statistics record."""
    service = EventService(session=test_db)
    success = await service.track_event(
        request_id="req_unknown_slot",
        ad_id="ad_401_501",
        event_type="impression",
        extra={"slot_id": "non_existent_slot_slug"},
    )
    assert success is True
    await test_db.commit()

    today = datetime.now(timezone.utc).date()
    result = await test_db.execute(
        select(PublisherDailyStat).where(PublisherDailyStat.stat_date == today)
    )
    stats = result.scalars().all()
    assert len(stats) == 0


@pytest.mark.asyncio
async def test_post_event_propagates_root_slot_id(client: AsyncClient, test_db: AsyncSession):
    """9. POST /api/v1/event propagates a valid root-level slot_id."""
    req_id = "req_post_propagate_1"
    ad_id = "401_501"
    slot_id = "slot_header_banner"
    sig = sign_tracking(req_id, ad_id, slot_id)

    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,  # Root-level slot_id
        "sig": sig,
        "extra": {"device": "mobile"},
    }

    res = await client.post("/api/v1/event/track", json=payload)
    assert res.status_code == 200
    assert res.json()["success"] is True

    today = datetime.now(timezone.utc).date()
    result = await test_db.execute(
        select(PublisherDailyStat).where(
            PublisherDailyStat.publisher_id == 101,
            PublisherDailyStat.slot_id == 201,
            PublisherDailyStat.stat_date == today,
        )
    )
    stat = result.scalar_one_or_none()
    assert stat is not None
    assert stat.slot_id == 201
    assert stat.impressions == 1


@pytest.mark.asyncio
async def test_concurrent_first_events_concurrency_safe(test_db: AsyncSession):
    """10. Concurrent first events for the same slot/date do not cause an integrity error or lose increments."""
    # Test concurrency safety using two independent sessions executing simultaneously
    session_factory = sessionmaker(test_db.bind, class_=AsyncSession, expire_on_commit=False)

    async def worker(req_id: str):
        async with session_factory() as session:
            svc = EventService(session=session)
            res = await svc.track_event(
                request_id=req_id,
                ad_id="ad_401_501",
                event_type="impression",
                extra={"slot_id": "slot_header_banner"},
            )
            assert res is True
            await session.commit()

    # Launch genuinely concurrent requests across separate transactions
    await asyncio.gather(worker("req_conc_1"), worker("req_conc_2"))

    today = datetime.now(timezone.utc).date()
    result = await test_db.execute(
        select(PublisherDailyStat).where(
            PublisherDailyStat.publisher_id == 101,
            PublisherDailyStat.slot_id == 201,
            PublisherDailyStat.stat_date == today,
        )
    )
    stat = result.scalar_one()
    assert stat.impressions == 2


@pytest.mark.asyncio
async def test_existing_event_replay_protection_still_works(client: AsyncClient, test_db: AsyncSession):
    """11. Existing event idempotency/replay protections still work."""
    req_id = "req_replay_guard_test"
    ad_id = "401_501"
    slot_id = "slot_sidebar_rect"
    sig = sign_tracking(req_id, ad_id, slot_id)

    payload = {
        "request_id": req_id,
        "ad_id": ad_id,
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": sig,
    }

    # First event succeeds
    res1 = await client.post("/api/v1/event/track", json=payload)
    assert res1.status_code == 200
    assert res1.json()["success"] is True

    # Replay event with identical request_id and ad_id must be rejected (429)
    res2 = await client.post("/api/v1/event/track", json=payload)
    assert res2.status_code == 429
    assert "Duplicate" in res2.json()["detail"]


def test_model_metadata_contains_expected_constraint_and_index():
    """12. Model metadata contains the expected constraint and index."""
    table_args = PublisherDailyStat.__table_args__
    uq_names = [arg.name for arg in table_args if isinstance(arg, UniqueConstraint)]
    idx_names = [arg.name for arg in table_args if isinstance(arg, Index)]

    assert "uq_pub_daily_stats_slot_date" in uq_names
    assert "idx_pub_daily_stats_lookup" in idx_names
