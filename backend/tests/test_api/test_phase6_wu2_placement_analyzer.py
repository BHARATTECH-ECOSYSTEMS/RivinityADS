"""Tests for Phase 6 Work Unit 2 (WU2):
Placement Analyzer Service.

Covers:
1. No placement statistics in the analysis window.
2. Exactly 99 impressions: no performance verdict ("insufficient_data").
3. Exactly 100 impressions: eligible for classification.
4. Zero impressions without division errors.
5. Correct CTR calculation.
6. Correct eCPM calculation using the repository's actual earnings definition.
7. Separate format-specific benchmarks.
8. Correct median calculation with an odd number of eligible benchmark observations.
9. Correct median calculation with an even number of eligible benchmark observations.
10. Missing or insufficient platform benchmark data.
11. Each documented health classification boundary (low_visibility, healthy, great, great + raise floor).
12. No cross-publisher data leakage.
13. Historical NULL-slot rows are not attributed to a placement.
14. Rolling 30-day boundary handling (inclusive day 30, exclusive day 31).
15. Deterministic output for identical inputs.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.placement_analyzer import (
    CTR_GREAT_THRESHOLD,
    CTR_HIGH_DEMAND_THRESHOLD,
    CTR_LOW_VISIBILITY_THRESHOLD,
    MIN_IMPRESSIONS_FOR_VERDICT,
    REC_COLLECT_DATA,
    REC_MOVE_ABOVE_FOLD,
    REC_NO_ACTION,
    REC_RAISE_FLOOR,
    VERDICT_GREAT,
    VERDICT_HEALTHY,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_LOW_VISIBILITY,
    PlacementAnalyzerService,
)
from liteads.models.base import CreativeType, Status
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat


@pytest.fixture(autouse=True)
async def setup_test_publishers(test_db: AsyncSession):
    """Seed base publishers and slots."""
    pub1 = Publisher(id=101, name="Publisher Alpha", status=Status.ACTIVE, balance=Decimal("100.00"))
    pub2 = Publisher(id=102, name="Publisher Beta", status=Status.ACTIVE, balance=Decimal("200.00"))
    test_db.add_all([pub1, pub2])

    # Pub 1 slots: 1 Banner (201), 1 Video (202)
    slot1 = AdSlot(
        id=201,
        publisher_id=101,
        slot_id="slot_banner_alpha",
        name="Header Banner",
        format=CreativeType.BANNER,
        width=728,
        height=90,
        status=Status.ACTIVE,
    )
    slot2 = AdSlot(
        id=202,
        publisher_id=101,
        slot_id="slot_video_alpha",
        name="Video Stream",
        format=CreativeType.VIDEO,
        width=640,
        height=360,
        status=Status.ACTIVE,
    )

    # Pub 2 slots: 1 Banner (203)
    slot3 = AdSlot(
        id=203,
        publisher_id=102,
        slot_id="slot_banner_beta",
        name="Sidebar Banner",
        format=CreativeType.BANNER,
        width=300,
        height=250,
        status=Status.ACTIVE,
    )
    test_db.add_all([slot1, slot2, slot3])
    await test_db.commit()


@pytest.mark.asyncio
async def test_no_placement_statistics_in_window(test_db: AsyncSession):
    """1. No placement statistics in the analysis window."""
    analyzer = PlacementAnalyzerService(test_db)
    today = date(2026, 10, 17)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)

    assert len(results) == 2
    for r in results:
        assert r.impressions == 0
        assert r.clicks == 0
        assert r.earnings == 0.0
        assert r.ctr == 0.0
        assert r.ecpm == 0.0
        assert r.verdict == VERDICT_INSUFFICIENT_DATA
        assert r.recommendation == REC_COLLECT_DATA


@pytest.mark.asyncio
async def test_exactly_99_impressions_no_verdict(test_db: AsyncSession):
    """2. Exactly 99 impressions: no performance verdict (insufficient_data)."""
    today = date(2026, 10, 17)
    stat = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=today,
        impressions=99,
        clicks=10,  # 10% CTR would be great, but impressions < 100
        conversions=2,
        earnings=Decimal("1.500000"),
    )
    test_db.add(stat)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    banner_res = next(r for r in results if r.slot_db_id == 201)

    assert banner_res.impressions == 99
    assert banner_res.verdict == VERDICT_INSUFFICIENT_DATA
    assert banner_res.recommendation == REC_COLLECT_DATA


@pytest.mark.asyncio
async def test_exactly_100_impressions_eligible_for_classification(test_db: AsyncSession):
    """3. Exactly 100 impressions: eligible for classification."""
    today = date(2026, 10, 17)
    stat = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=today,
        impressions=100,
        clicks=1,  # CTR = 1% = 0.0100 -> "healthy"
        conversions=0,
        earnings=Decimal("0.500000"),
    )
    test_db.add(stat)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    banner_res = next(r for r in results if r.slot_db_id == 201)

    assert banner_res.impressions == 100
    assert banner_res.verdict == VERDICT_HEALTHY
    assert banner_res.recommendation == REC_NO_ACTION


@pytest.mark.asyncio
async def test_zero_impressions_without_division_errors(test_db: AsyncSession):
    """4. Zero impressions without division errors."""
    today = date(2026, 10, 17)
    # Slot with a row that explicitly recorded 0 impressions and 0 clicks
    stat = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=today,
        impressions=0,
        clicks=0,
        conversions=0,
        earnings=Decimal("0.000000"),
    )
    test_db.add(stat)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    banner_res = next(r for r in results if r.slot_db_id == 201)

    assert banner_res.impressions == 0
    assert banner_res.ctr == 0.0
    assert banner_res.ecpm == 0.0
    assert banner_res.verdict == VERDICT_INSUFFICIENT_DATA


@pytest.mark.asyncio
async def test_correct_ctr_calculation(test_db: AsyncSession):
    """5. Correct CTR calculation."""
    today = date(2026, 10, 17)
    # 250 impressions, 5 clicks -> CTR = 5 / 250 = 0.0200
    stat = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=today,
        impressions=250,
        clicks=5,
        conversions=1,
        earnings=Decimal("1.000000"),
    )
    test_db.add(stat)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    banner_res = next(r for r in results if r.slot_db_id == 201)

    assert banner_res.ctr == 0.02
    assert banner_res.verdict == VERDICT_GREAT


@pytest.mark.asyncio
async def test_correct_ecpm_calculation_using_earnings(test_db: AsyncSession):
    """6. Correct eCPM calculation using the repository's actual earnings definition."""
    today = date(2026, 10, 17)
    # 500 impressions, earnings = 1.75 -> eCPM = (1.75 / 500) * 1000 = 3.50
    stat = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=today,
        impressions=500,
        clicks=2,
        conversions=0,
        earnings=Decimal("1.750000"),
    )
    test_db.add(stat)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    banner_res = next(r for r in results if r.slot_db_id == 201)

    assert banner_res.ecpm == 3.50


@pytest.mark.asyncio
async def test_separate_format_specific_benchmarks(test_db: AsyncSession):
    """7. Separate format-specific benchmarks for Banner vs Video."""
    today = date(2026, 10, 17)
    # Banner slot (201): 1000 impr, 10 clicks (CTR=0.01), earnings=2.0 (eCPM=2.0)
    # Video slot (202): 1000 impr, 50 clicks (CTR=0.05), earnings=15.0 (eCPM=15.0)
    stat1 = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=today,
        impressions=1000,
        clicks=10,
        conversions=0,
        earnings=Decimal("2.000000"),
    )
    stat2 = PublisherDailyStat(
        publisher_id=101,
        slot_id=202,
        stat_date=today,
        impressions=1000,
        clicks=50,
        conversions=0,
        earnings=Decimal("15.000000"),
    )
    test_db.add_all([stat1, stat2])
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    benchmarks = await analyzer.calculate_platform_benchmarks(as_of_date=today)

    assert CreativeType.BANNER in benchmarks
    assert CreativeType.VIDEO in benchmarks
    assert benchmarks[CreativeType.BANNER].median_ctr == 0.0100
    assert benchmarks[CreativeType.BANNER].median_ecpm == 2.00
    assert benchmarks[CreativeType.VIDEO].median_ctr == 0.0500
    assert benchmarks[CreativeType.VIDEO].median_ecpm == 15.00


@pytest.mark.asyncio
async def test_correct_median_odd_observations(test_db: AsyncSession):
    """8. Correct median calculation with an odd number of eligible benchmark observations."""
    today = date(2026, 10, 17)
    # Create 3 eligible Banner slots across publishers with CTRs: 0.01, 0.02, 0.03 -> Median = 0.02
    slot_extra = AdSlot(
        id=204,
        publisher_id=102,
        slot_id="slot_banner_extra",
        name="Extra Banner",
        format=CreativeType.BANNER,
        width=300,
        height=250,
        status=Status.ACTIVE,
    )
    test_db.add(slot_extra)
    await test_db.flush()

    s1 = PublisherDailyStat(publisher_id=101, slot_id=201, stat_date=today, impressions=100, clicks=1, conversions=0, earnings=Decimal("1.00")) # CTR 0.01, eCPM 10.0
    s2 = PublisherDailyStat(publisher_id=102, slot_id=203, stat_date=today, impressions=100, clicks=2, conversions=0, earnings=Decimal("2.00")) # CTR 0.02, eCPM 20.0
    s3 = PublisherDailyStat(publisher_id=102, slot_id=204, stat_date=today, impressions=100, clicks=3, conversions=0, earnings=Decimal("3.00")) # CTR 0.03, eCPM 30.0
    test_db.add_all([s1, s2, s3])
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    benchmarks = await analyzer.calculate_platform_benchmarks(as_of_date=today)
    banner_bm = benchmarks[CreativeType.BANNER]

    assert banner_bm.eligible_slots_count == 3
    assert banner_bm.median_ctr == 0.0200
    assert banner_bm.median_ecpm == 20.00


@pytest.mark.asyncio
async def test_correct_median_even_observations(test_db: AsyncSession):
    """9. Correct median calculation with an even number of eligible benchmark observations."""
    today = date(2026, 10, 17)
    # Create 2 eligible Banner slots with CTRs: 0.01, 0.02 -> Median = 0.0150
    # eCPMs: 10.00, 20.00 -> Median = 15.00
    s1 = PublisherDailyStat(publisher_id=101, slot_id=201, stat_date=today, impressions=100, clicks=1, conversions=0, earnings=Decimal("1.00"))
    s2 = PublisherDailyStat(publisher_id=102, slot_id=203, stat_date=today, impressions=100, clicks=2, conversions=0, earnings=Decimal("2.00"))
    test_db.add_all([s1, s2])
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    benchmarks = await analyzer.calculate_platform_benchmarks(as_of_date=today)
    banner_bm = benchmarks[CreativeType.BANNER]

    assert banner_bm.eligible_slots_count == 2
    assert banner_bm.median_ctr == 0.0150
    assert banner_bm.median_ecpm == 15.00


@pytest.mark.asyncio
async def test_missing_or_insufficient_benchmark_data(test_db: AsyncSession):
    """10. Missing or insufficient platform benchmark data."""
    today = date(2026, 10, 17)
    # Only 50 impressions on slot 201 (< 100), so 0 slots qualify for benchmark
    s = PublisherDailyStat(publisher_id=101, slot_id=201, stat_date=today, impressions=50, clicks=1, conversions=0, earnings=Decimal("0.50"))
    test_db.add(s)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    benchmarks = await analyzer.calculate_platform_benchmarks(as_of_date=today)

    assert CreativeType.BANNER not in benchmarks or benchmarks[CreativeType.BANNER].eligible_slots_count == 0

    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    banner_res = next(r for r in results if r.slot_db_id == 201)
    assert banner_res.benchmark["eligible_slots_count"] == 0
    assert banner_res.benchmark["median_ctr"] is None
    assert banner_res.benchmark["median_ecpm"] is None


@pytest.mark.asyncio
async def test_each_documented_health_classification_boundary(test_db: AsyncSession):
    """11. Each documented health classification boundary:
    - low_visibility: ctr < 0.002
    - healthy: 0.002 <= ctr <= 0.01
    - great (no raise floor): 0.01 < ctr <= 0.02
    - great (raise floor): ctr > 0.02
    """
    today = date(2026, 10, 17)
    # Create 4 slots for testing boundaries
    s_low = AdSlot(id=210, publisher_id=101, slot_id="slot_low", name="Low", format=1, width=300, height=250, status=1)
    s_healthy = AdSlot(id=211, publisher_id=101, slot_id="slot_healthy", name="Healthy", format=1, width=300, height=250, status=1)
    s_great = AdSlot(id=212, publisher_id=101, slot_id="slot_great", name="Great", format=1, width=300, height=250, status=1)
    s_floor = AdSlot(id=213, publisher_id=101, slot_id="slot_floor", name="Floor", format=1, width=300, height=250, status=1)
    test_db.add_all([s_low, s_healthy, s_great, s_floor])
    await test_db.flush()

    # 1. ctr = 1 / 1000 = 0.0010 (< 0.002) -> low_visibility + Move slot above the fold
    d1 = PublisherDailyStat(publisher_id=101, slot_id=210, stat_date=today, impressions=1000, clicks=1, conversions=0, earnings=Decimal("1.00"))
    # 2. ctr = 5 / 1000 = 0.0050 (0.002 <= ctr <= 0.01) -> healthy + No action
    d2 = PublisherDailyStat(publisher_id=101, slot_id=211, stat_date=today, impressions=1000, clicks=5, conversions=0, earnings=Decimal("1.00"))
    # 3. ctr = 15 / 1000 = 0.0150 (0.01 < ctr <= 0.02) -> great + No action
    d3 = PublisherDailyStat(publisher_id=101, slot_id=212, stat_date=today, impressions=1000, clicks=15, conversions=0, earnings=Decimal("1.00"))
    # 4. ctr = 25 / 1000 = 0.0250 (ctr > 0.02) -> great + Raise floor price
    d4 = PublisherDailyStat(publisher_id=101, slot_id=213, stat_date=today, impressions=1000, clicks=25, conversions=0, earnings=Decimal("1.00"))
    test_db.add_all([d1, d2, d3, d4])
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    res_map = {r.slot_db_id: r for r in results}

    assert res_map[210].verdict == VERDICT_LOW_VISIBILITY
    assert res_map[210].recommendation == REC_MOVE_ABOVE_FOLD

    assert res_map[211].verdict == VERDICT_HEALTHY
    assert res_map[211].recommendation == REC_NO_ACTION

    assert res_map[212].verdict == VERDICT_GREAT
    assert res_map[212].recommendation == REC_NO_ACTION

    assert res_map[213].verdict == VERDICT_GREAT
    assert res_map[213].recommendation == REC_RAISE_FLOOR


@pytest.mark.asyncio
async def test_no_cross_publisher_data_leakage(test_db: AsyncSession):
    """12. No cross-publisher data leakage."""
    today = date(2026, 10, 17)
    # Pub 102 has high earnings and sensitive slot name
    s_pub2 = PublisherDailyStat(
        publisher_id=102,
        slot_id=203,
        stat_date=today,
        impressions=1000,
        clicks=20,
        conversions=5,
        earnings=Decimal("500.000000"),
    )
    test_db.add(s_pub2)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results_pub1 = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)

    # Confirm only Pub 101 slots returned
    assert all(r.publisher_id == 101 for r in results_pub1)
    slot_ids = [r.slot_id for r in results_pub1]
    assert "slot_banner_beta" not in slot_ids
    # Confirm benchmark does not leak slot names or publisher identities
    for r in results_pub1:
        bm = r.benchmark
        assert "publisher_id" not in bm
        assert "slot_id" not in bm
        assert "name" not in bm


@pytest.mark.asyncio
async def test_historical_null_slot_rows_not_attributed(test_db: AsyncSession):
    """13. Historical NULL-slot rows are not attributed to a placement."""
    today = date(2026, 10, 17)
    # Legacy NULL-slot row
    null_stat = PublisherDailyStat(
        publisher_id=101,
        slot_id=None,
        stat_date=today,
        impressions=5000,
        clicks=250,
        conversions=50,
        earnings=Decimal("100.000000"),
    )
    test_db.add(null_stat)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)

    for r in results:
        # None of the slots should have received the 5000 impressions
        assert r.impressions == 0
        assert r.clicks == 0
        assert r.verdict == VERDICT_INSUFFICIENT_DATA


@pytest.mark.asyncio
async def test_rolling_30_day_boundary_handling(test_db: AsyncSession):
    """14. Rolling 30-day boundary handling (day 0 and day 29 inclusive, day 30 ago exclusive)."""
    today = date(2026, 10, 17)
    day_29_ago = today - timedelta(days=29)  # Day 30 in 1-indexed count (inclusive)
    day_30_ago = today - timedelta(days=30)  # Day 31 in 1-indexed count (outside window)

    # In-window event on day_29_ago: 100 impressions
    s_in = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=day_29_ago,
        impressions=100,
        clicks=2,
        conversions=0,
        earnings=Decimal("1.00"),
    )
    # Out-of-window event on day_30_ago: 500 impressions
    s_out = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=day_30_ago,
        impressions=500,
        clicks=10,
        conversions=0,
        earnings=Decimal("5.00"),
    )
    test_db.add_all([s_in, s_out])
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    results = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    banner_res = next(r for r in results if r.slot_db_id == 201)

    # Exactly 100 impressions from day_29_ago should be included, 500 from day_30_ago excluded
    assert banner_res.impressions == 100
    assert banner_res.clicks == 2
    assert banner_res.verdict == VERDICT_GREAT


@pytest.mark.asyncio
async def test_deterministic_output_for_identical_inputs(test_db: AsyncSession):
    """15. Deterministic output for identical inputs."""
    today = date(2026, 10, 17)
    s = PublisherDailyStat(
        publisher_id=101,
        slot_id=201,
        stat_date=today,
        impressions=200,
        clicks=3,
        conversions=0,
        earnings=Decimal("0.75"),
    )
    test_db.add(s)
    await test_db.commit()

    analyzer = PlacementAnalyzerService(test_db)
    run1 = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)
    run2 = await analyzer.analyze_publisher_placements(publisher_id=101, as_of_date=today)

    assert len(run1) == len(run2)
    assert [r.to_dict() for r in run1] == [r.to_dict() for r in run2]
