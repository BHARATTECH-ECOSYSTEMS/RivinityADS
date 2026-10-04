"""
Budget filter for checking campaign budget availability.
"""

from decimal import Decimal
from typing import Any

from liteads.common.cache import CacheKeys, redis_client
from liteads.common.logger import get_logger
from liteads.common.utils import current_date
from liteads.rec_engine.filter.base import BaseFilter
from liteads.schemas.internal import AdCandidate, BudgetInfo, UserContext

logger = get_logger(__name__)


class BudgetFilter(BaseFilter):
    """
    Filter candidates by budget availability.

    Checks:
    1. Daily budget not exceeded
    2. Total budget not exceeded
    3. Advertiser balance sufficient
    """

    def __init__(self, buffer_ratio: float = 0.95, session=None):
        """
        Initialize budget filter.

        Args:
            buffer_ratio: Budget buffer to prevent overspending (0.95 = 95%)
            session: SQLAlchemy AsyncSession for DB queries (balance checks)
        """
        self.buffer_ratio = buffer_ratio
        self.session = session
        self._budget_cache: dict[int, BudgetInfo] = {}

    async def filter(
        self,
        candidates: list[AdCandidate],
        user_context: UserContext,
        **kwargs: Any,
    ) -> list[AdCandidate]:
        """Filter candidates by budget."""
        if not candidates:
            return []

        # Batch get budget info
        campaign_ids = list(set(c.campaign_id for c in candidates))
        budget_infos = await self._get_budget_batch(campaign_ids)

        # Filter by campaign budget
        temp_result = []
        for candidate in candidates:
            budget_info = budget_infos.get(candidate.campaign_id)
            if budget_info and budget_info.has_budget:
                temp_result.append(candidate)

        # Filter by advertiser balance
        result = []
        if temp_result and self.session:
            from sqlalchemy import select
            from liteads.models import Advertiser
            advertiser_ids = list(set(c.advertiser_id for c in temp_result))
            stmt = select(Advertiser.id, Advertiser.balance).where(Advertiser.id.in_(advertiser_ids))
            db_res = await self.session.execute(stmt)
            balances = {row.id: row.balance for row in db_res.all()}

            for candidate in temp_result:
                balance = balances.get(candidate.advertiser_id, 0)
                if balance > 0:
                    result.append(candidate)
        else:
            result = temp_result

        filtered_count = len(candidates) - len(result)
        if filtered_count > 0:
            logger.debug(f"Budget filter removed {filtered_count} candidates")

        return result

    async def filter_single(
        self,
        candidate: AdCandidate,
        user_context: UserContext,
        **kwargs: Any,
    ) -> bool:
        """Check if single candidate has budget."""
        budget_info = await self._get_budget(candidate.campaign_id)
        if not budget_info.has_budget:
            return False

        if self.session:
            from sqlalchemy import select
            from liteads.models import Advertiser
            stmt = select(Advertiser.balance).where(Advertiser.id == candidate.advertiser_id)
            db_res = await self.session.execute(stmt)
            balance = db_res.scalar_one_or_none()
            if balance is None or balance <= 0:
                return False

        return True

    async def _get_budget_batch(
        self, campaign_ids: list[int]
    ) -> dict[int, BudgetInfo]:
        """Get budget info for multiple campaigns."""
        result: dict[int, BudgetInfo] = {}
        today = current_date()

        # Try Redis first
        pipeline = redis_client.pipeline()
        for campaign_id in campaign_ids:
            key = f"budget:{campaign_id}:{today}"
            pipeline.hgetall(key)

        try:
            cached_results = await pipeline.execute()

            for campaign_id, cached in zip(campaign_ids, cached_results):
                if cached:
                    result[campaign_id] = BudgetInfo(
                        campaign_id=campaign_id,
                        budget_daily=float(cached.get("budget_daily", 0)) or None,
                        budget_total=float(cached.get("budget_total", 0)) or None,
                        spent_today=float(cached.get("spent_today", 0)),
                        spent_total=float(cached.get("spent_total", 0)),
                    )
                else:
                    # Default: assume has budget (will be filtered later if not)
                    result[campaign_id] = BudgetInfo(
                        campaign_id=campaign_id,
                        budget_daily=None,
                        budget_total=None,
                        spent_today=0.0,
                        spent_total=0.0,
                    )
        except Exception as e:
            logger.warning(f"Failed to get budget from cache: {e}")
            # Return default budget info
            for campaign_id in campaign_ids:
                result[campaign_id] = BudgetInfo(
                    campaign_id=campaign_id,
                    budget_daily=None,
                    budget_total=None,
                    spent_today=0.0,
                    spent_total=0.0,
                )

        return result

    async def _get_budget(self, campaign_id: int) -> BudgetInfo:
        """Get budget info for a single campaign."""
        result = await self._get_budget_batch([campaign_id])
        return result.get(
            campaign_id,
            BudgetInfo(campaign_id=campaign_id),
        )

    async def update_spent(
        self,
        campaign_id: int,
        cost: Decimal,
    ) -> None:
        """
        Update campaign spent amount.

        Called after ad is served/clicked.
        """
        today = current_date()
        key = f"budget:{campaign_id}:{today}"

        try:
            await redis_client.hincrbyfloat(key, "spent_today", float(cost))
            await redis_client.hincrbyfloat(key, "spent_total", float(cost))
            await redis_client.expire(key, 86400 * 2)  # 2 days TTL
        except Exception as e:
            logger.error(f"Failed to update spent for campaign {campaign_id}: {e}")

    async def set_budget(
        self,
        campaign_id: int,
        budget_daily: float | None,
        budget_total: float | None,
    ) -> None:
        """
        Set campaign budget in cache.

        Called when campaign is created/updated.
        """
        today = current_date()
        key = f"budget:{campaign_id}:{today}"

        mapping = {}
        if budget_daily is not None:
            mapping["budget_daily"] = str(budget_daily)
        if budget_total is not None:
            mapping["budget_total"] = str(budget_total)

        if mapping:
            await redis_client.hmset(key, mapping)
            await redis_client.expire(key, 86400 * 2)
