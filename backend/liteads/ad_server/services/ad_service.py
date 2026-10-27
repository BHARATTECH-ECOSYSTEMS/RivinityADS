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

        # Get floor price and slot settings (including chat surface restriction)
        floor_price = 0.0
        is_chat_surface = False
        if request.slot_id:
            try:
                from liteads.models import AdSlot
                from sqlalchemy import select
                slot_res = await self.session.execute(
                    select(AdSlot.floor_price, AdSlot.is_chat_surface).where(AdSlot.slot_id == request.slot_id)
                )
                slot_row = slot_res.one_or_none()
                if slot_row is not None:
                    if slot_row[0] is not None:
                        floor_price = float(slot_row[0])
                    is_chat_surface = bool(slot_row[1])
            except Exception as e:
                logger.debug(f"Could not fetch slot info for {request.slot_id}: {e}")

        # Chat surface restriction: interstitial format (4) is completely forbidden on chat surfaces
        from liteads.models.base import CreativeType
        if is_chat_surface and request.format == CreativeType.INTERSTITIAL.value:
            logger.info("Interstitial ads prohibited on chat surface slots", slot_id=request.slot_id)
            return []

        # Get recommendations
        candidates, metrics = await self.engine.recommend(
            user_context=user_context,
            slot_id=request.slot_id,
            num_ads=request.num_ads,
            floor_price=floor_price,
        )

        # Enforce chat surface restrictions on retrieved candidates: interstitials are never permitted
        if is_chat_surface and candidates:
            candidates = [c for c in candidates if c.creative_type != CreativeType.INTERSTITIAL.value]

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
            if is_chat_surface and candidates:
                candidates = [c for c in candidates if c.creative_type != CreativeType.INTERSTITIAL.value]

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
            if request.geo.latitude is not None and request.geo.longitude is not None:
                ctx.location_source = request.geo.location_source or "device_gps"
                ctx.location_accuracy_meters = request.geo.accuracy_meters
                ctx.location_consent = bool(request.geo.consent)

        # Context info
        if request.context:
            ctx.app_id = request.context.app_id or ""
            ctx.app_name = request.context.app_name or ""
            ctx.network = request.context.network or ""
            ctx.carrier = request.context.carrier or ""
            ctx.sectors = list(request.context.sectors or [])

        # Map nested Phase 5 user context if provided
        if request.user:
            if request.user.id:
                ctx.user_id = request.user.id
                ctx.user_hash = hash_user_id(request.user.id)
            if request.user.sector_context:
                existing_sectors = set(ctx.sectors)
                for sec in request.user.sector_context:
                    if sec not in existing_sectors:
                        ctx.sectors.append(sec)
                        existing_sectors.add(sec)
            if request.user.geo:
                if not ctx.country and request.user.geo.country:
                    ctx.country = request.user.geo.country
                if not ctx.region and (request.user.geo.region or request.user.geo.state):
                    ctx.region = request.user.geo.region or request.user.geo.state or ""
                if not ctx.city and request.user.geo.city:
                    ctx.city = request.user.geo.city
                if not ctx.pincode and request.user.geo.pincode:
                    ctx.pincode = request.user.geo.pincode
                if request.user.geo.latitude is not None and request.user.geo.longitude is not None:
                    ctx.latitude = request.user.geo.latitude
                    ctx.longitude = request.user.geo.longitude
                    ctx.location_source = request.user.geo.location_source or "device_gps"
                    ctx.location_accuracy_meters = request.user.geo.accuracy_meters
                    ctx.location_consent = bool(request.user.geo.consent)

        # Server-side IP -> Geo resolution (overrides administrative values if found)
        # Note: IP coordinates represent coarse city/metro centroids (accuracy ~10-50km), NEVER precise GPS.
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
            # Only populate latitude/longitude from IP if coordinates were not already provided by device
            if ctx.latitude is None and geo_data.get("lat") is not None:
                ctx.latitude = geo_data["lat"]
                ctx.location_source = "ip"
                ctx.location_accuracy_meters = 10000.0
                ctx.location_consent = False
            if ctx.longitude is None and geo_data.get("lon") is not None:
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
