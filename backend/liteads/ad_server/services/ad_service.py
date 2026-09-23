"""
Ad serving service.

Handles the core ad serving logic using the recommendation engine.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import get_settings
from liteads.common.logger import get_logger
from liteads.common.utils import hash_user_id
from liteads.rec_engine import RecommendationConfig, RecommendationEngine
from liteads.rec_engine.ranking.bidding import RankingStrategy
from liteads.schemas.internal import AdCandidate, UserContext
from liteads.schemas.request import AdRequest

logger = get_logger(__name__)


class AdService:
    """Ad serving service using recommendation engine."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self._engine: RecommendationEngine | None = None

    @property
    def engine(self) -> RecommendationEngine:
        """Lazy initialization of recommendation engine."""
        if self._engine is None:
            config = RecommendationConfig(
                max_retrieval=100,
                enable_budget_filter=True,
                enable_frequency_filter=True,
                enable_quality_filter=True,
                enable_ml_prediction=self.settings.ad_serving.enable_ml_prediction,
                fallback_ctr=0.01,
                fallback_cvr=0.001,
                ranking_strategy=RankingStrategy.ECPM,
                enable_diversity_rerank=True,
                enable_exploration=True,
                exploration_epsilon=0.1,
            )
            self._engine = RecommendationEngine(
                session=self.session,
                config=config,
            )
        return self._engine

    async def serve_ads(
        self,
        request: AdRequest,
        request_id: str,
    ) -> list[AdCandidate]:
        """
        Main ad serving method.

        Uses the recommendation engine to:
        1. Retrieve candidate ads
        2. Filter by targeting, budget, frequency
        3. Predict CTR/CVR
        4. Rank and re-rank
        5. Return top candidates
        """
        # Build user context
        user_context = await self._build_user_context(request)

        # Get floor price for the slot
        floor_price = 0.0
        if request.slot_id:
            try:
                from liteads.models import AdSlot
                from sqlalchemy import select
                slot_res = await self.session.execute(
                    select(AdSlot.floor_price).where(AdSlot.slot_id == request.slot_id)
                )
                fp = slot_res.scalar_one_or_none()
                if fp is not None:
                    floor_price = float(fp)
            except Exception as e:
                logger.debug(f"Could not fetch floor price for {request.slot_id}: {e}")

        # Get recommendations
        candidates, metrics = await self.engine.recommend(
            user_context=user_context,
            slot_id=request.slot_id,
            num_ads=request.num_ads,
            floor_price=floor_price,
        )

        # STEP 4.1 — per-request format selection
        # Filter by requested creative_type; fall back to unfiltered if no match.
        if request.format is not None and candidates:
            filtered = [c for c in candidates if c.creative_type == request.format]
            if filtered:
                candidates = filtered
            else:
                logger.debug(
                    "No candidates matched requested format; using unfiltered set",
                    requested_format=request.format,
                    candidate_types=list({c.creative_type for c in candidates}),
                )

        logger.info(
            "Ad serving completed",
            request_id=request_id,
            retrieval_count=metrics.retrieval_count,
            final_count=metrics.final_count,
            total_ms=round(metrics.total_ms, 2),
        )

        return candidates

    async def _build_user_context(self, request: AdRequest) -> UserContext:
        """Build user context from request."""
        ctx = UserContext(
            user_id=request.user_id,
            user_hash=hash_user_id(request.user_id) if request.user_id else 0,
        )

        # Device info
        if request.device:
            ctx.os = request.device.os
            ctx.os_version = request.device.os_version or ""
            ctx.device_model = request.device.model or ""
            ctx.device_brand = request.device.brand or ""

        # Geo info
        if request.geo:
            ctx.ip = request.geo.ip or ""
            ctx.country = request.geo.country or ""
            ctx.region = request.geo.region or ""
            ctx.city = request.geo.city or ""
            ctx.pincode = request.geo.pincode or ""
            ctx.latitude = request.geo.latitude
            ctx.longitude = request.geo.longitude

        # Context info
        if request.context:
            ctx.app_id = request.context.app_id or ""
            ctx.app_name = request.context.app_name or ""
            ctx.network = request.context.network or ""
            ctx.carrier = request.context.carrier or ""
            ctx.sectors = request.context.sectors or []

        # Server-side IP -> Geo resolution (overrides request values if found)
        if ctx.ip:
            from liteads.ad_server.services.geo_resolver import resolve_ip
            geo_data = await resolve_ip(ctx.ip)
            if geo_data.get("country"):
                ctx.country = geo_data["country"]
            if geo_data.get("state"):
                ctx.region = geo_data["state"]
            if geo_data.get("city"):
                ctx.city = geo_data["city"]
            if geo_data.get("pincode"):
                ctx.pincode = geo_data["pincode"]
            if geo_data.get("lat") is not None:
                ctx.latitude = geo_data["lat"]
            if geo_data.get("lon") is not None:
                ctx.longitude = geo_data["lon"]

        # User features
        if request.user_features:
            ctx.age = request.user_features.age
            ctx.gender = request.user_features.gender
            ctx.interests = request.user_features.interests or []
            ctx.app_categories = request.user_features.app_categories or []
            ctx.custom_features = request.user_features.custom or {}

        # User sector affinities (for Phase 3 personalization)
        if ctx.user_id:
            try:
                from liteads.ad_server.services.user_profile import get_affinities
                ctx.sector_affinities = await get_affinities(ctx.user_id)
            except Exception as e:
                logger.error(f"Failed to fetch user affinities: {e}")

        return ctx

    async def refresh_cache(self) -> None:
        """Refresh recommendation engine cache."""
        await self.engine.refresh_cache()
