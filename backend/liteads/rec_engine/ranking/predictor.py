"""
CTR/CVR prediction module.

Provides prediction interfaces for ML models, statistical baselines,
shadow predictors, and latency-budgeted fallbacks.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from liteads.common.logger import get_logger
from liteads.common.utils import Timer
from liteads.ml_engine.governance.pii_guard import sanitize_features
from liteads.ml_engine.governance.telemetry import get_ml_telemetry
from liteads.ml_engine.serving.validator import validate_model_artifact
from liteads.schemas.internal import AdCandidate, PredictionResult, UserContext

logger = get_logger(__name__)


class BasePredictor(ABC):
    """Abstract base class for CTR/CVR predictors."""

    @abstractmethod
    async def predict(
        self,
        user_context: UserContext,
        candidate: AdCandidate,
    ) -> PredictionResult:
        """Predict CTR/CVR for a single candidate."""
        pass

    @abstractmethod
    async def predict_batch(
        self,
        user_context: UserContext,
        candidates: list[AdCandidate],
    ) -> list[PredictionResult]:
        """Predict CTR/CVR for multiple candidates."""
        pass


class StatisticalPredictor(BasePredictor):
    """
    Statistical predictor using historical data.

    Uses smoothed CTR based on historical impressions and clicks.
    Good as a baseline or fallback.
    """

    def __init__(
        self,
        default_ctr: float = 0.01,
        default_cvr: float = 0.001,
        smoothing_impressions: int = 1000,
        smoothing_clicks: int = 100,
        prediction_mode: str = "statistical",
        is_fallback: bool = False,
        fallback_reason: str | None = None,
    ):
        """
        Initialize statistical predictor.

        Args:
            default_ctr: Default CTR for new ads
            default_cvr: Default CVR for new ads
            smoothing_impressions: Smoothing factor for impressions
            smoothing_clicks: Smoothing factor for clicks
            prediction_mode: Mode label ("statistical", "fallback", "shadow")
            is_fallback: Whether this prediction is serving as a fallback
            fallback_reason: Reason for fallback if applicable
        """
        self.default_ctr = default_ctr
        self.default_cvr = default_cvr
        self.smoothing_impressions = smoothing_impressions
        self.smoothing_clicks = smoothing_clicks
        self.prediction_mode = prediction_mode
        self.is_fallback = is_fallback
        self.fallback_reason = fallback_reason

    async def predict(
        self,
        user_context: UserContext,
        candidate: AdCandidate,
    ) -> PredictionResult:
        """Predict using smoothed historical CTR."""
        impressions = candidate.metadata.get("impressions", 0)
        clicks = candidate.metadata.get("clicks", 0)
        conversions = candidate.metadata.get("conversions", 0)

        # Smoothed CTR: (clicks + prior_clicks) / (impressions + prior_impressions)
        smoothed_ctr = (clicks + self.smoothing_clicks * self.default_ctr) / (
            impressions + self.smoothing_clicks
        )

        # Smoothed CVR
        if clicks > 0:
            smoothed_cvr = (conversions + self.smoothing_clicks * self.default_cvr) / (
                clicks + self.smoothing_clicks
            )
        else:
            smoothed_cvr = self.default_cvr

        return PredictionResult(
            campaign_id=candidate.campaign_id,
            creative_id=candidate.creative_id,
            pctr=smoothed_ctr,
            pcvr=smoothed_cvr,
            model_version="statistical_v1",
            latency_ms=0.1,
            prediction_mode=self.prediction_mode,
            is_fallback=self.is_fallback,
            fallback_reason=self.fallback_reason,
        )

    async def predict_batch(
        self,
        user_context: UserContext,
        candidates: list[AdCandidate],
    ) -> list[PredictionResult]:
        """Predict for multiple candidates."""
        results = []
        for candidate in candidates:
            result = await self.predict(user_context, candidate)
            results.append(result)
        return results


class MLPredictor(BasePredictor):
    """
    ML-based predictor using trained DeepFM/LR models with strict latency budget,
    artifact validation, and safe fallback.
    """

    def __init__(
        self,
        model_path: str | None = None,
        feature_builder_path: str | None = None,
        model_version: str = "v1",
        timeout_ms: float = 20.0,
        fallback_ctr: float = 0.01,
        fallback_cvr: float = 0.001,
        device: str = "cpu",
    ):
        """
        Initialize ML predictor.

        Args:
            model_path: Path to trained model checkpoint
            feature_builder_path: Path to fitted feature builder
            model_version: Model version identifier
            timeout_ms: Strict latency budget in milliseconds
            fallback_ctr: Fallback CTR if prediction fails or times out
            fallback_cvr: Fallback CVR if prediction fails or times out
            device: Device for inference (default cpu)
        """
        self.model_path = model_path
        self.feature_builder_path = feature_builder_path
        self.model_version = model_version
        self.timeout_ms = timeout_ms
        self.fallback_ctr = fallback_ctr
        self.fallback_cvr = fallback_cvr
        self.device = device
        self._model_predictor = None
        self._is_loaded = False
        self._validation_error: str | None = None
        self.fallback_predictor = StatisticalPredictor(
            default_ctr=fallback_ctr,
            default_cvr=fallback_cvr,
            prediction_mode="fallback",
            is_fallback=True,
            fallback_reason="ml_fallback",
        )

    async def load_model(self) -> None:
        """Validate and load model and feature builder."""
        if self._is_loaded:
            return

        if not self.model_path:
            self._validation_error = "model_path_empty"
            self._is_loaded = True
            logger.warning("MLPredictor initialized without model_path, using statistical fallback")
            return

        # Validate artifact integrity and CPU compatibility (Task B)
        is_valid, reason = validate_model_artifact(self.model_path, self.feature_builder_path)
        if not is_valid:
            self._validation_error = reason
            self._is_loaded = True
            logger.warning(
                f"Model artifact validation failed: {reason}. Using statistical fallback.",
                model_path=self.model_path,
            )
            return

        try:
            from liteads.ml_engine.serving import ModelPredictor

            self._model_predictor = ModelPredictor(
                model_path=self.model_path,
                feature_builder_path=self.feature_builder_path,
                device=self.device,
            )
            self._model_predictor.load()
            self._is_loaded = True
            logger.info(f"Loaded ML model version {self.model_version} from {self.model_path}")
        except Exception as e:
            logger.warning(f"Failed to load ML model: {e}. Using fallback predictions.")
            self._validation_error = str(e)
            self._model_predictor = None
            self._is_loaded = True

    async def predict(
        self,
        user_context: UserContext,
        candidate: AdCandidate,
    ) -> PredictionResult:
        """Predict using ML model."""
        results = await self.predict_batch(user_context, [candidate])
        return results[0]

    async def predict_batch(
        self,
        user_context: UserContext,
        candidates: list[AdCandidate],
    ) -> list[PredictionResult]:
        """Predict for multiple candidates with latency budget and safe fallback."""
        if not candidates:
            return []

        if not self._is_loaded:
            await self.load_model()

        # If model is not loaded or not ready, fallback safely immediately
        if self._model_predictor is None or not self._model_predictor.is_ready:
            reason = self._validation_error or "model_not_ready"
            fallback_results = await self.fallback_predictor.predict_batch(user_context, candidates)
            telemetry = get_ml_telemetry()
            for res in fallback_results:
                res.prediction_mode = "fallback"
                res.is_fallback = True
                res.fallback_reason = reason
                telemetry.record_inference(latency_ms=0.0, is_fallback=True, fallback_reason=reason)
            return fallback_results

        with Timer("ml_prediction") as timer:
            try:
                features_batch = self._build_features(user_context, candidates)
                ml_results = await asyncio.wait_for(
                    self._model_predictor.predict_batch_async(features_batch),
                    timeout=self.timeout_ms / 1000.0,
                )

                telemetry = get_ml_telemetry()
                results = []
                for candidate, ml_result in zip(candidates, ml_results):
                    telemetry.record_inference(
                        latency_ms=ml_result.latency_ms,
                        is_fallback=False,
                        pctr=ml_result.pctr,
                    )
                    results.append(
                        PredictionResult(
                            campaign_id=candidate.campaign_id,
                            creative_id=candidate.creative_id,
                            pctr=ml_result.pctr,
                            pcvr=ml_result.pcvr or self.fallback_cvr,
                            model_version=ml_result.model_version or self.model_version,
                            latency_ms=ml_result.latency_ms,
                            prediction_mode="ml",
                            is_fallback=False,
                            fallback_reason=None,
                        )
                    )
                return results

            except asyncio.TimeoutError:
                logger.warning(
                    f"ML prediction timed out after {self.timeout_ms}ms; safe fallback to statistical baseline"
                )
                telemetry = get_ml_telemetry()
                fallback_results = await self.fallback_predictor.predict_batch(user_context, candidates)
                for res in fallback_results:
                    res.prediction_mode = "fallback"
                    res.is_fallback = True
                    res.fallback_reason = "timeout"
                    res.latency_ms = timer.elapsed_ms / len(candidates) if timer.elapsed_ms else self.timeout_ms
                    telemetry.record_inference(latency_ms=res.latency_ms, is_fallback=True, fallback_reason="timeout")
                return fallback_results

            except Exception as e:
                logger.warning(
                    f"ML prediction failed: {e}; safe fallback to statistical baseline"
                )
                telemetry = get_ml_telemetry()
                fallback_results = await self.fallback_predictor.predict_batch(user_context, candidates)
                for res in fallback_results:
                    res.prediction_mode = "fallback"
                    res.is_fallback = True
                    res.fallback_reason = f"error: {str(e)}"
                    res.latency_ms = timer.elapsed_ms / len(candidates) if timer.elapsed_ms else 0.0
                    telemetry.record_inference(latency_ms=res.latency_ms, is_fallback=True, fallback_reason=f"error: {str(e)}")
                return fallback_results

    def _build_features(
        self,
        user_context: UserContext,
        candidates: list[AdCandidate],
    ) -> list[dict[str, Any]]:
        """
        Build feature dictionaries for prediction.
        Enforces truthful features (no fabricated constants) and privacy controls.
        """
        features = []

        for candidate in candidates:
            # Privacy & Consent (Task D)
            # Minors under 18: age bucket is restricted and gender is masked to unspecified
            if user_context.age is not None and user_context.age < 18:
                age_bucket = "restricted"
                user_gender = "unspecified"
            elif user_context.gender:
                age_bucket = self._get_age_bucket(user_context.age)
                user_gender = user_context.gender
            else:
                age_bucket = self._get_age_bucket(user_context.age)
                user_gender = "unknown"

            # Coarse Geo only (Task D - never raw GPS coordinates in ML tensors)
            geo_country = user_context.country or "unknown"
            geo_region = user_context.region or "unknown"
            geo_city = user_context.city or "unknown"

            # Coarse sector tags only (strictly zero conversation text or prompts)
            sector_tags = [s for s in user_context.sectors if s] if user_context.sectors else []
            ad_tags = candidate.metadata.get("tags", []) if isinstance(candidate.metadata.get("tags"), list) else []

            # Truthful features (Task C - remove fabricated constants)
            network_type = user_context.network if user_context.network else "unknown"
            landing_page = candidate.metadata.get("landing_page_type", "unknown")
            quality_score = float(candidate.metadata.get("quality_score", 1.0))
            session_duration = float(user_context.custom_features.get("session_duration", 0.0)) if user_context.custom_features else 0.0

            custom_feat = getattr(user_context, "custom_features", {}) or {}
            user_meta = getattr(user_context, "metadata", {}) or custom_feat

            feature_dict = {
                # User features
                "user_id": user_context.user_id or "unknown",
                "user_gender": user_gender,
                "user_age_bucket": age_bucket,
                "user_device_os": user_context.os or "unknown",
                "user_network_type": network_type,
                "user_click_count_7d": int(custom_feat.get("clicks_7d", 0)),
                "user_click_count_30d": int(custom_feat.get("clicks_30d", 0)),
                "user_conversion_count_7d": int(custom_feat.get("conv_7d", 0)),
                "user_ctr_7d": candidate.metadata.get("user_ctr_7d", None),
                "user_cvr_7d": candidate.metadata.get("user_cvr_7d", None),
                "user_avg_session_duration": session_duration,
                "user_interest_tags": sector_tags or (user_context.interests or []),
                "user_clicked_categories": [],
                # Ad features
                "campaign_id": str(candidate.campaign_id),
                "creative_id": str(candidate.creative_id),
                "advertiser_id": str(candidate.advertiser_id),
                "ad_category": str(candidate.metadata.get("category", "unknown")),
                "creative_type": candidate.metadata.get("creative_type", "banner"),
                "bid_type": "cpm" if candidate.bid_type == 1 else "cpc",
                "landing_page_type": landing_page,
                "ad_bid": float(candidate.bid),
                "ad_ctr_7d": candidate.pctr if candidate.pctr is not None else None,
                "ad_cvr_7d": candidate.pcvr if candidate.pcvr is not None else None,
                "ad_impression_count_7d": candidate.metadata.get("impressions", 0),
                "ad_click_count_7d": candidate.metadata.get("clicks", 0),
                "creative_ctr": candidate.pctr if candidate.pctr is not None else None,
                "advertiser_quality_score": quality_score,
                "ad_tags": ad_tags,
                # Context features
                "slot_id": user_meta.get("slot_id", "default"),
                "request_hour": user_meta.get("hour", None),
                "request_day_of_week": user_meta.get("day_of_week", None),
                "is_weekend": user_meta.get("is_weekend", 0),
                "is_peak_hour": user_meta.get("is_peak_hour", 0),
                "geo_country": geo_country,
                "geo_region": geo_region,
                "geo_city": geo_city,
                "slot_ctr": candidate.metadata.get("slot_ctr", None),
                "hour_ctr": candidate.metadata.get("hour_ctr", None),
            }
            features.append(sanitize_features(feature_dict))

        return features

    def _get_age_bucket(self, age: int | None) -> str:
        """Convert age to age bucket with privacy controls."""
        if age is None:
            return "unknown"
        if age < 18:
            return "restricted"
        elif age < 25:
            return "18-24"
        elif age < 35:
            return "25-34"
        elif age < 45:
            return "35-44"
        else:
            return "45+"


class ShadowPredictor(BasePredictor):
    """
    Shadow predictor for safe online ML evaluation (Task F).

    Executes both baseline (StatisticalPredictor) and shadow (MLPredictor).
    Logs comparison metrics between predictions.
    Authoritatively returns the baseline prediction results for live auction ranking
    so live bids are never distorted by experimental or shadow ML models.
    """

    def __init__(
        self,
        baseline: BasePredictor,
        shadow: BasePredictor,
        timeout_ms: float = 20.0,
    ):
        self.baseline = baseline
        self.shadow = shadow
        self.timeout_ms = timeout_ms

    async def predict(
        self,
        user_context: UserContext,
        candidate: AdCandidate,
    ) -> PredictionResult:
        results = await self.predict_batch(user_context, [candidate])
        return results[0]

    async def predict_batch(
        self,
        user_context: UserContext,
        candidates: list[AdCandidate],
    ) -> list[PredictionResult]:
        # 1. Authoritative baseline prediction for live auction ranking
        baseline_results = await self.baseline.predict_batch(user_context, candidates)

        # 2. Concurrently or bounded execute shadow predictions
        try:
            shadow_results = await asyncio.wait_for(
                self.shadow.predict_batch(user_context, candidates),
                timeout=self.timeout_ms / 1000.0,
            )
            # Log comparison metrics
            self._log_comparison(baseline_results, shadow_results)
        except asyncio.TimeoutError:
            logger.warning(
                f"Shadow prediction timed out after {self.timeout_ms}ms (live serving unaffected)"
            )
        except Exception as e:
            logger.warning(
                f"Shadow prediction error: {e} (live serving unaffected)"
            )

        # Invariant: Return baseline results for live ranking, marked with mode="shadow"
        for res in baseline_results:
            res.prediction_mode = "shadow"

        return baseline_results

    def _log_comparison(
        self,
        baseline_results: list[PredictionResult],
        shadow_results: list[PredictionResult],
    ) -> None:
        """Compare baseline and shadow predictions for telemetry."""
        top1_match = False
        if baseline_results and shadow_results:
            b_top_idx = max(range(len(baseline_results)), key=lambda i: baseline_results[i].pctr)
            s_top_idx = max(range(len(shadow_results)), key=lambda i: shadow_results[i].pctr)
            top1_match = (b_top_idx == s_top_idx)

        telemetry = get_ml_telemetry()
        for b, s in zip(baseline_results, shadow_results):
            delta_pctr = s.pctr - b.pctr
            telemetry.record_shadow_comparison(
                baseline_pctr=b.pctr,
                shadow_pctr=s.pctr,
                is_top1_match=top1_match,
            )
            logger.info(
                "Shadow prediction comparison",
                campaign_id=b.campaign_id,
                creative_id=b.creative_id,
                baseline_pctr=round(b.pctr, 4),
                shadow_pctr=round(s.pctr, 4),
                delta_pctr=round(delta_pctr, 4),
                baseline_version=b.model_version,
                shadow_version=s.model_version,
                shadow_latency_ms=round(s.latency_ms, 2),
                shadow_is_fallback=s.is_fallback,
            )


class EnsemblePredictor(BasePredictor):
    """
    Ensemble predictor combining multiple predictors.

    Supports weighted averaging of predictions from multiple models.
    """

    def __init__(
        self,
        predictors: list[tuple[BasePredictor, float]],
    ):
        """
        Initialize ensemble predictor.

        Args:
            predictors: List of (predictor, weight) tuples
        """
        self.predictors = predictors
        total_weight = sum(w for _, w in predictors)
        self.weights = [w / total_weight for _, w in predictors]

    async def predict(
        self,
        user_context: UserContext,
        candidate: AdCandidate,
    ) -> PredictionResult:
        """Predict using weighted ensemble."""
        results = await self.predict_batch(user_context, [candidate])
        return results[0]

    async def predict_batch(
        self,
        user_context: UserContext,
        candidates: list[AdCandidate],
    ) -> list[PredictionResult]:
        """Predict using weighted ensemble for batch."""
        all_predictions: list[list[PredictionResult]] = []

        for predictor, _ in self.predictors:
            preds = await predictor.predict_batch(user_context, candidates)
            all_predictions.append(preds)

        results = []
        for i, candidate in enumerate(candidates):
            weighted_ctr = sum(
                all_predictions[j][i].pctr * self.weights[j]
                for j in range(len(self.predictors))
            )
            weighted_cvr = sum(
                all_predictions[j][i].pcvr * self.weights[j]
                for j in range(len(self.predictors))
            )

            results.append(
                PredictionResult(
                    campaign_id=candidate.campaign_id,
                    creative_id=candidate.creative_id,
                    pctr=weighted_ctr,
                    pcvr=weighted_cvr,
                    model_version="ensemble",
                    latency_ms=max(p[i].latency_ms for p in all_predictions),
                    prediction_mode="ensemble",
                    is_fallback=False,
                )
            )

        return results
