"""Tests for Phase 6 Work Unit 3 (WU3): Placement Health API & Authorization.

Verifies:
1. Authorized publisher can retrieve their placement health.
2. Unauthenticated requests are rejected (401).
3. A publisher cannot retrieve another publisher's health (403).
4. Manipulating publisher_id in the URL does not bypass ownership enforcement (403).
5. Empty publisher data returns the documented empty response.
6. Insufficient-data placements (<100 impressions) retain the WU2 verdict and recommendation.
7. Format-specific benchmark data is returned without leaking individual competitor data.
8. Legacy NULL-slot rows are not attributed to placements.
9. The endpoint uses the analyzer service rather than duplicating calculations.
10. Invalid or nonexistent publisher identifiers follow existing API conventions (404).
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.main import app
from liteads.ad_server.services.placement_analyzer import (
    PlacementAnalyzerService,
    VERDICT_GREAT,
    VERDICT_HEALTHY,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_LOW_VISIBILITY,
    REC_COLLECT_DATA,
    REC_RAISE_FLOOR,
    REC_MOVE_ABOVE_FOLD,
    REC_NO_ACTION,
)
from liteads.common.security import current_user, verified_user
from liteads.models.auth import User
from liteads.models.base import CreativeType, Status
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat


def set_user_override(
    user_id: int = 1,
    role: str = "publisher",
    publisher_id: int | None = 1,
    advertiser_id: int | None = None,
):
    async def mock_user():
        return User(
            id=user_id,
            email=f"user_{user_id}@example.com",
            name=f"User {user_id}",
            password_hash="test",
            role=role,
            publisher_id=publisher_id,
            advertiser_id=advertiser_id,
            is_active=True,
            email_verified=True,
        )

    app.dependency_overrides[verified_user] = mock_user
    app.dependency_overrides[current_user] = mock_user


def clear_user_override():
    app.dependency_overrides.pop(verified_user, None)
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
async def setup_placement_data(test_db: AsyncSession):
    today = date.today()
    day_5_ago = today - timedelta(days=5)
    day_35_ago = today - timedelta(days=35)

    # 1. Create Publishers
    pub1 = Publisher(id=1, name="Publisher One", status=Status.ACTIVE)
    pub2 = Publisher(id=2, name="Publisher Two", status=Status.ACTIVE)
    pub_empty = Publisher(id=3, name="Publisher Empty", status=Status.ACTIVE)
    test_db.add_all([pub1, pub2, pub_empty])
    await test_db.flush()

    # 2. Create Slots for Publisher 1
    # Slot 1: Banner, 1000 impr, 25 clicks -> great (CTR = 2.5%)
    slot1 = AdSlot(
        id=1,
        publisher_id=1,
        slot_id="slot_pub1_banner",
        name="Header Banner",
        format=CreativeType.BANNER,
        width=728,
        height=90,
        status=Status.ACTIVE,
    )
    # Slot 2: Banner, 99 impr -> insufficient_data
    slot2 = AdSlot(
        id=2,
        publisher_id=1,
        slot_id="slot_pub1_small",
        name="Sidebar Banner",
        format=CreativeType.BANNER,
        width=300,
        height=250,
        status=Status.ACTIVE,
    )
    # Slot 3: Video, 500 impr, 0 clicks -> low_visibility
    slot3 = AdSlot(
        id=3,
        publisher_id=1,
        slot_id="slot_pub1_video",
        name="Outstream Video",
        format=CreativeType.VIDEO,
        width=640,
        height=360,
        status=Status.ACTIVE,
    )

    # 3. Create Slots for Publisher 2 (Competitor)
    slot4 = AdSlot(
        id=4,
        publisher_id=2,
        slot_id="slot_pub2_banner",
        name="Competitor Banner",
        format=CreativeType.BANNER,
        width=728,
        height=90,
        status=Status.ACTIVE,
    )
    slot5 = AdSlot(
        id=5,
        publisher_id=2,
        slot_id="slot_pub2_video",
        name="Competitor Video",
        format=CreativeType.VIDEO,
        width=640,
        height=360,
        status=Status.ACTIVE,
    )

    test_db.add_all([slot1, slot2, slot3, slot4, slot5])
    await test_db.flush()

    # 4. Insert Daily Stats
    stats = [
        # Slot 1: 1000 impr, 25 clicks, $5.00 earnings (inside window)
        PublisherDailyStat(
            publisher_id=1,
            slot_id=1,
            stat_date=day_5_ago,
            impressions=1000,
            clicks=25,
            conversions=2,
            earnings=Decimal("5.000000"),
        ),
        # Slot 1: 5000 impr outside window (day 35 ago)
        PublisherDailyStat(
            publisher_id=1,
            slot_id=1,
            stat_date=day_35_ago,
            impressions=5000,
            clicks=100,
            conversions=10,
            earnings=Decimal("25.000000"),
        ),
        # Slot 2: 99 impressions (insufficient_data test)
        PublisherDailyStat(
            publisher_id=1,
            slot_id=2,
            stat_date=day_5_ago,
            impressions=99,
            clicks=10,
            conversions=1,
            earnings=Decimal("1.500000"),
        ),
        # Slot 3: 500 impr, 0 clicks (low_visibility test)
        PublisherDailyStat(
            publisher_id=1,
            slot_id=3,
            stat_date=day_5_ago,
            impressions=500,
            clicks=0,
            conversions=0,
            earnings=Decimal("1.000000"),
        ),
        # Slot 4 (Pub 2): 2000 impr, 20 clicks (CTR=1.0%)
        PublisherDailyStat(
            publisher_id=2,
            slot_id=4,
            stat_date=day_5_ago,
            impressions=2000,
            clicks=20,
            conversions=5,
            earnings=Decimal("8.000000"),
        ),
        # Slot 5 (Pub 2): 1000 impr, 10 clicks (CTR=1.0%)
        PublisherDailyStat(
            publisher_id=2,
            slot_id=5,
            stat_date=day_5_ago,
            impressions=1000,
            clicks=10,
            conversions=2,
            earnings=Decimal("6.000000"),
        ),
        # Legacy NULL-slot row for Pub 1 (must be excluded from placement stats)
        PublisherDailyStat(
            publisher_id=1,
            slot_id=None,
            stat_date=day_5_ago,
            impressions=9999,
            clicks=999,
            conversions=99,
            earnings=Decimal("999.000000"),
        ),
    ]
    test_db.add_all(stats)
    await test_db.commit()


@pytest.mark.asyncio
async def test_1_authorized_publisher_retrieves_health(
    client: AsyncClient, setup_placement_data: None
):
    """Test 1: An authorized publisher can retrieve their placement health."""
    set_user_override(user_id=1, role="publisher", publisher_id=1)
    try:
        response = await client.get("/api/v1/publisher/1/placements/health")
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["publisher_id"] == 1
        assert data["window_days"] == 30
        assert data["total_placements"] == 3
        assert len(data["placements"]) == 3

        # Check placements map by slot_id
        slots = {p["slot_id"]: p for p in data["placements"]}
        assert "slot_pub1_banner" in slots
        assert "slot_pub1_small" in slots
        assert "slot_pub1_video" in slots

        # Verify Slot 1
        s1 = slots["slot_pub1_banner"]
        assert s1["slot_name"] == "Header Banner"
        assert s1["format_name"] == "BANNER"
        assert s1["impressions"] == 1000
        assert s1["clicks"] == 25
        assert abs(s1["ctr"] - 0.025) < 1e-4
        assert abs(s1["ecpm"] - 5.0) < 1e-2
        assert s1["verdict"] == VERDICT_GREAT
        assert s1["recommendation"] == REC_RAISE_FLOOR

        # Verify Slot 2 (<100 impressions)
        s2 = slots["slot_pub1_small"]
        assert s2["impressions"] == 99
        assert s2["verdict"] == VERDICT_INSUFFICIENT_DATA
        assert s2["recommendation"] == REC_COLLECT_DATA

        # Verify Slot 3 (low visibility)
        s3 = slots["slot_pub1_video"]
        assert s3["impressions"] == 500
        assert s3["ctr"] == 0.0
        assert s3["verdict"] == VERDICT_LOW_VISIBILITY
        assert s3["recommendation"] == REC_MOVE_ABOVE_FOLD
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_2_unauthenticated_requests_rejected(
    client: AsyncClient, setup_placement_data: None
):
    """Test 2: Unauthenticated requests without credentials are rejected (401)."""
    clear_user_override()
    response = await client.get("/api/v1/publisher/1/placements/health")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_3_publisher_cannot_retrieve_another_publisher_health(
    client: AsyncClient, setup_placement_data: None
):
    """Test 3: A publisher cannot access another publisher's placement health (403)."""
    # User belongs to publisher 1, attempts to access publisher 2
    set_user_override(user_id=1, role="publisher", publisher_id=1)
    try:
        response = await client.get("/api/v1/publisher/2/placements/health")
        assert response.status_code == 403
        data = response.json()
        assert "outside your account" in data.get("detail", "")
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_4_manipulating_url_publisher_id_rejected(
    client: AsyncClient, setup_placement_data: None
):
    """Test 4: Manipulating publisher_id in URL path does not bypass ownership."""
    set_user_override(user_id=2, role="publisher", publisher_id=2)
    try:
        # User 2 attempts to query publisher 1
        response = await client.get("/api/v1/publisher/1/placements/health")
        assert response.status_code == 403

        # User 2 attempts to query publisher 3
        response3 = await client.get("/api/v1/publisher/3/placements/health")
        assert response3.status_code == 403
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_5_empty_publisher_data_returns_documented_response(
    client: AsyncClient, setup_placement_data: None
):
    """Test 5: Empty publisher data returns valid empty response without errors."""
    set_user_override(user_id=3, role="publisher", publisher_id=3)
    try:
        response = await client.get("/api/v1/publisher/3/placements/health")
        assert response.status_code == 200
        data = response.json()
        assert data["publisher_id"] == 3
        assert data["total_placements"] == 0
        assert data["placements"] == []
        assert isinstance(data["benchmarks"], dict)
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_6_insufficient_data_retains_wu2_verdict(
    client: AsyncClient, setup_placement_data: None
):
    """Test 6: Exactly 99 impressions retain VERDICT_INSUFFICIENT_DATA and REC_COLLECT_DATA."""
    set_user_override(user_id=1, role="publisher", publisher_id=1)
    try:
        response = await client.get("/api/v1/publisher/1/placements/health")
        assert response.status_code == 200
        data = response.json()
        small_slot = next(p for p in data["placements"] if p["slot_id"] == "slot_pub1_small")
        assert small_slot["impressions"] == 99
        assert small_slot["verdict"] == "insufficient_data"
        assert small_slot["recommendation"] == "Collect more data (minimum 100 impressions required)"
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_7_benchmark_data_returned_without_competitor_leakage(
    client: AsyncClient, setup_placement_data: None
):
    """Test 7: Benchmark data aggregates format medians without leaking competitor IDs or slots."""
    set_user_override(user_id=1, role="publisher", publisher_id=1)
    try:
        response = await client.get("/api/v1/publisher/1/placements/health")
        assert response.status_code == 200
        data = response.json()

        # Benchmarks dictionary
        benchmarks = data["benchmarks"]
        assert "BANNER" in benchmarks
        banner_bm = benchmarks["BANNER"]
        assert banner_bm["eligible_slots_count"] >= 2  # slot1 and slot4 have >= 100 impressions
        assert banner_bm["median_ctr"] is not None
        assert banner_bm["median_ecpm"] is not None

        # Ensure no competitor fields in data payload
        raw_text = response.text
        assert "slot_pub2_banner" not in raw_text
        assert "Competitor Banner" not in raw_text
        assert "Competitor Video" not in raw_text
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_8_legacy_null_slot_rows_not_attributed(
    client: AsyncClient, setup_placement_data: None
):
    """Test 8: Legacy NULL-slot rows (9999 impressions) are not attributed to any placement."""
    set_user_override(user_id=1, role="publisher", publisher_id=1)
    try:
        response = await client.get("/api/v1/publisher/1/placements/health")
        assert response.status_code == 200
        data = response.json()

        total_impr = sum(p["impressions"] for p in data["placements"])
        # Expected: 1000 (slot1) + 99 (slot2) + 500 (slot3) = 1599
        assert total_impr == 1599, f"Expected 1599, got {total_impr} (9999 legacy was not excluded!)"
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_9_endpoint_uses_placement_analyzer_service(
    client: AsyncClient, setup_placement_data: None
):
    """Test 9: Verifies that the endpoint delegates to PlacementAnalyzerService."""
    set_user_override(user_id=1, role="publisher", publisher_id=1)
    try:
        orig_analyze = PlacementAnalyzerService.analyze_publisher_placements
        calls = []

        async def spy_analyze(self, *args, **kwargs):
            calls.append((args, kwargs))
            return await orig_analyze(self, *args, **kwargs)

        with patch.object(PlacementAnalyzerService, "analyze_publisher_placements", spy_analyze):
            response = await client.get("/api/v1/publisher/1/placements/health")
            assert response.status_code == 200
            assert len(calls) == 1
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_10_nonexistent_publisher_returns_404(
    client: AsyncClient, setup_placement_data: None
):
    """Test 10: Nonexistent publisher ID returns 404 for admin user."""
    # Admin can access any publisher ID, so accessing nonexistent publisher 99999 yields 404
    set_user_override(user_id=999, role="admin", publisher_id=None)
    try:
        response = await client.get("/api/v1/publisher/99999/placements/health")
        assert response.status_code == 404
        assert "Publisher 99999 not found" in response.json()["detail"]
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_11_admin_role_can_access_any_publisher_health(
    client: AsyncClient, setup_placement_data: None
):
    """Admin role can inspect placement health for any valid publisher."""
    set_user_override(user_id=999, role="admin", publisher_id=None)
    try:
        response1 = await client.get("/api/v1/publisher/1/placements/health")
        assert response1.status_code == 200
        assert response1.json()["publisher_id"] == 1

        response2 = await client.get("/api/v1/publisher/2/placements/health")
        assert response2.status_code == 200
        assert response2.json()["publisher_id"] == 2
    finally:
        clear_user_override()


@pytest.mark.asyncio
async def test_12_advertiser_role_forbidden(
    client: AsyncClient, setup_placement_data: None
):
    """Advertiser role cannot access publisher placement health."""
    set_user_override(user_id=50, role="advertiser", publisher_id=None, advertiser_id=1)
    try:
        response = await client.get("/api/v1/publisher/1/placements/health")
        assert response.status_code == 403
    finally:
        clear_user_override()
