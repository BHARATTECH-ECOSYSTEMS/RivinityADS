"""Placement Auto-Analysis Service (Phase 6 WU2).

Analyzes publisher ad placements over a rolling 30-day window,
computes CTR and eCPM, calculates format-specific platform benchmark medians,
and assigns health verdicts and actionable recommendations according to
RivinityAds Phase 2 Engineering requirements.
"""

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
import statistics
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.logger import get_logger
from liteads.models.base import Status
from liteads.models.publisher import AdSlot, PublisherDailyStat

logger = get_logger(__name__)

# --- Canonical Thresholds & Configuration ---
MIN_IMPRESSIONS_FOR_VERDICT: int = 100
CTR_GREAT_THRESHOLD: float = 0.01          # CTR > 1.0% -> "great"
CTR_LOW_VISIBILITY_THRESHOLD: float = 0.002 # CTR < 0.2% -> "low_visibility"
CTR_HIGH_DEMAND_THRESHOLD: float = 0.02    # CTR > 2.0% -> "Raise floor price — demand exceeds supply"
DEFAULT_WINDOW_DAYS: int = 30

# --- Classification Verdicts ---
VERDICT_GREAT = "great"
VERDICT_HEALTHY = "healthy"
VERDICT_LOW_VISIBILITY = "low_visibility"
VERDICT_INSUFFICIENT_DATA = "insufficient_data"

# --- Recommendations ---
REC_RAISE_FLOOR = "Raise floor price — demand exceeds supply"
REC_MOVE_ABOVE_FOLD = "Move slot above the fold or widen it"
REC_NO_ACTION = "No action"
REC_COLLECT_DATA = "Collect more data (minimum 100 impressions required)"


@dataclass(frozen=True)
class FormatBenchmark:
    """Aggregated platform benchmark metrics for a placement format."""
    format: int
    eligible_slots_count: int
    median_ctr: float | None
    median_ecpm: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlacementAnalysisResult:
    """Structured deterministic result of placement health analysis."""
    slot_id: str
    slot_db_id: int
    slot_name: str
    publisher_id: int
    format: int
    window_days: int
    start_date: str
    end_date: str
    impressions: int
    clicks: int
    conversions: int
    earnings: float
    ctr: float
    ecpm: float
    verdict: str
    recommendation: str
    benchmark: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlacementAnalyzerService:
    """Service for analyzing placement performance and calculating platform benchmarks."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def calculate_platform_benchmarks(
        self,
        window_days: int = DEFAULT_WINDOW_DAYS,
        as_of_date: date | None = None,
    ) -> dict[int, FormatBenchmark]:
        """
        Calculate format-specific median CTR and eCPM benchmarks across the platform.

        Only placements with at least MIN_IMPRESSIONS_FOR_VERDICT (100) within the
        bounded 30-day window are eligible for inclusion in the platform benchmark pool.
        This prevents low-volume outliers (e.g. 1 impression, 1 click = 100% CTR)
        from skewing platform benchmarks.

        Historical rows with slot_id IS NULL are excluded.
        """
        end_date = as_of_date or date.today()
        start_date = end_date - timedelta(days=window_days - 1)

        # Database-side aggregation by slot and format
        stmt = (
            select(
                PublisherDailyStat.slot_id,
                AdSlot.format,
                func.sum(PublisherDailyStat.impressions).label("total_impressions"),
                func.sum(PublisherDailyStat.clicks).label("total_clicks"),
                func.sum(PublisherDailyStat.earnings).label("total_earnings"),
            )
            .join(AdSlot, AdSlot.id == PublisherDailyStat.slot_id)
            .where(
                PublisherDailyStat.slot_id.isnot(None),
                PublisherDailyStat.stat_date >= start_date,
                PublisherDailyStat.stat_date <= end_date,
            )
            .group_by(PublisherDailyStat.slot_id, AdSlot.format)
            .having(func.sum(PublisherDailyStat.impressions) >= MIN_IMPRESSIONS_FOR_VERDICT)
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        # Group observations by format
        format_ctrs: dict[int, list[float]] = {}
        format_ecpms: dict[int, list[float]] = {}

        for row in rows:
            fmt = row.format
            impr = int(row.total_impressions)
            clicks = int(row.total_clicks)
            earnings = Decimal(row.total_earnings or 0)

            ctr = float(clicks) / impr if impr > 0 else 0.0
            ecpm = (float(earnings) / impr * 1000.0) if impr > 0 else 0.0

            format_ctrs.setdefault(fmt, []).append(ctr)
            format_ecpms.setdefault(fmt, []).append(ecpm)

        benchmarks: dict[int, FormatBenchmark] = {}

        # Collect all formats present or query known formats
        for fmt, ctr_list in format_ctrs.items():
            ecpm_list = format_ecpms[fmt]
            count = len(ctr_list)
            if count > 0:
                med_ctr = round(float(statistics.median(ctr_list)), 4)
                med_ecpm = round(float(statistics.median(ecpm_list)), 2)
            else:
                med_ctr = None
                med_ecpm = None

            benchmarks[fmt] = FormatBenchmark(
                format=fmt,
                eligible_slots_count=count,
                median_ctr=med_ctr,
                median_ecpm=med_ecpm,
            )

        return benchmarks

    async def analyze_publisher_placements(
        self,
        publisher_id: int,
        window_days: int = DEFAULT_WINDOW_DAYS,
        as_of_date: date | None = None,
    ) -> list[PlacementAnalysisResult]:
        """
        Analyze all placements for a specific publisher over a bounded rolling window.

        Enforces tenant isolation by strictly scoping slots and stats to publisher_id.
        Placements below MIN_IMPRESSIONS_FOR_VERDICT (100) receive VERDICT_INSUFFICIENT_DATA.
        """
        end_date = as_of_date or date.today()
        start_date = end_date - timedelta(days=window_days - 1)

        # 1. Fetch platform benchmarks for format comparison
        benchmarks = await self.calculate_platform_benchmarks(window_days, as_of_date)

        # 2. Fetch all slots belonging to this publisher (ordered deterministically)
        slots_stmt = (
            select(AdSlot)
            .where(
                AdSlot.publisher_id == publisher_id,
                AdSlot.status != Status.DELETED,
            )
            .order_by(AdSlot.id)
        )
        slots_res = await self.session.execute(slots_stmt)
        slots = slots_res.scalars().all()

        if not slots:
            return []

        # 3. Database-side aggregation of daily stats for this publisher's slots in window
        stats_stmt = (
            select(
                PublisherDailyStat.slot_id,
                func.sum(PublisherDailyStat.impressions).label("impressions"),
                func.sum(PublisherDailyStat.clicks).label("clicks"),
                func.sum(PublisherDailyStat.conversions).label("conversions"),
                func.sum(PublisherDailyStat.earnings).label("earnings"),
            )
            .where(
                PublisherDailyStat.publisher_id == publisher_id,
                PublisherDailyStat.slot_id.isnot(None),
                PublisherDailyStat.stat_date >= start_date,
                PublisherDailyStat.stat_date <= end_date,
            )
            .group_by(PublisherDailyStat.slot_id)
        )
        stats_res = await self.session.execute(stats_stmt)
        stats_by_slot_pk = {
            row.slot_id: row for row in stats_res.all()
        }

        # 4. Classify each placement
        analyses: list[PlacementAnalysisResult] = []

        for slot in slots:
            stat_row = stats_by_slot_pk.get(slot.id)
            impressions = int(stat_row.impressions) if stat_row else 0
            clicks = int(stat_row.clicks) if stat_row else 0
            conversions = int(stat_row.conversions) if stat_row else 0
            earnings_dec = Decimal(stat_row.earnings or 0) if stat_row else Decimal("0")

            # Metrics calculation handling zero impressions safely
            if impressions > 0:
                ctr = float(clicks) / impressions
                ecpm = (float(earnings_dec) / impressions) * 1000.0
            else:
                ctr = 0.0
                ecpm = 0.0

            # Classification & recommendation logic
            if impressions < MIN_IMPRESSIONS_FOR_VERDICT:
                verdict = VERDICT_INSUFFICIENT_DATA
                recommendation = REC_COLLECT_DATA
            elif ctr > CTR_GREAT_THRESHOLD:
                verdict = VERDICT_GREAT
                recommendation = (
                    REC_RAISE_FLOOR if ctr > CTR_HIGH_DEMAND_THRESHOLD else REC_NO_ACTION
                )
            elif ctr < CTR_LOW_VISIBILITY_THRESHOLD:
                verdict = VERDICT_LOW_VISIBILITY
                recommendation = REC_MOVE_ABOVE_FOLD
            else:
                verdict = VERDICT_HEALTHY
                recommendation = REC_NO_ACTION

            # Benchmark for slot's format (defaulting to empty benchmark if format has none)
            slot_benchmark = benchmarks.get(
                slot.format,
                FormatBenchmark(
                    format=slot.format,
                    eligible_slots_count=0,
                    median_ctr=None,
                    median_ecpm=None,
                ),
            )

            analyses.append(
                PlacementAnalysisResult(
                    slot_id=slot.slot_id,
                    slot_db_id=slot.id,
                    slot_name=slot.name,
                    publisher_id=publisher_id,
                    format=slot.format,
                    window_days=window_days,
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                    impressions=impressions,
                    clicks=clicks,
                    conversions=conversions,
                    earnings=float(round(earnings_dec, 6)),
                    ctr=round(ctr, 4),
                    ecpm=round(ecpm, 2),
                    verdict=verdict,
                    recommendation=recommendation,
                    benchmark=slot_benchmark.to_dict(),
                )
            )

        return analyses

    async def analyze_all_placements(
        self,
        window_days: int = DEFAULT_WINDOW_DAYS,
        as_of_date: date | None = None,
    ) -> list[PlacementAnalysisResult]:
        """
        Analyze all eligible publisher placements across the entire platform.
        Useful for scheduled nightly runs or administrative audits.
        """
        # Fetch all unique publisher IDs with active slots
        publishers_stmt = (
            select(AdSlot.publisher_id)
            .where(AdSlot.status != Status.DELETED)
            .distinct()
            .order_by(AdSlot.publisher_id)
        )
        pub_res = await self.session.execute(publishers_stmt)
        publisher_ids = pub_res.scalars().all()

        results: list[PlacementAnalysisResult] = []
        for pub_id in publisher_ids:
            pub_results = await self.analyze_publisher_placements(
                publisher_id=pub_id,
                window_days=window_days,
                as_of_date=as_of_date,
            )
            results.extend(pub_results)

        return results
