"""
Operational Inference Telemetry and Drift Monitoring (Phase 8).

Collects and reports:
1. Online inference latency percentiles (p50, p95, p99).
2. Timeouts, fallback breakdown counters, and fallback rates.
3. Feature freshness / staleness in seconds.
4. Missing feature rates.
5. Online score distribution drift (PSI) vs reference baseline.
6. Shadow mode ranking agreement telemetry.
"""

from collections import deque
from datetime import datetime, timezone
from typing import Any
import threading
import numpy as np

from liteads.common.logger import get_logger
from liteads.ml_engine.evaluation.evaluator import calculate_psi

logger = get_logger(__name__)


class MLTelemetryCollector:
    """
    Thread-safe operational telemetry collector for online ML serving and shadow mode.
    """

    def __init__(self, max_samples: int = 5000):
        self._lock = threading.Lock()
        self.max_samples = max_samples

        self._latencies: deque[float] = deque(maxlen=max_samples)
        self._predictions: deque[float] = deque(maxlen=max_samples)
        self._reference_distribution: np.ndarray = np.linspace(0.01, 0.05, 100)

        self._total_inferences: int = 0
        self._total_timeouts: int = 0
        self._fallback_breakdown: dict[str, int] = {
            "ml_disabled": 0,
            "timeout": 0,
            "model_unloaded": 0,
            "invalid_output": 0,
            "feature_missing": 0,
            "exception": 0,
        }

        self._feature_staleness_max_sec: float = 0.0
        self._total_features_evaluated: int = 0
        self._total_features_missing: int = 0

        # Shadow mode comparison
        self._shadow_evaluations: int = 0
        self._shadow_agreements: int = 0
        self._shadow_deltas: deque[float] = deque(maxlen=max_samples)

    def record_inference(
        self,
        latency_ms: float,
        is_fallback: bool = False,
        fallback_reason: str | None = None,
        pctr: float | None = None,
    ) -> None:
        """Record an online prediction event."""
        with self._lock:
            self._total_inferences += 1
            if latency_ms >= 0:
                self._latencies.append(float(latency_ms))

            if is_fallback:
                reason_key = "exception"
                if fallback_reason:
                    fb_lower = fallback_reason.lower()
                    if "disable" in fb_lower:
                        reason_key = "ml_disabled"
                    elif "timeout" in fb_lower:
                        reason_key = "timeout"
                        self._total_timeouts += 1
                    elif "unload" in fb_lower or "empty" in fb_lower or "not_ready" in fb_lower:
                        reason_key = "model_unloaded"
                    elif "nan" in fb_lower or "inf" in fb_lower or "invalid" in fb_lower:
                        reason_key = "invalid_output"
                    elif "feature" in fb_lower or "missing" in fb_lower:
                        reason_key = "feature_missing"
                    else:
                        reason_key = "exception"
                self._fallback_breakdown[reason_key] = self._fallback_breakdown.get(reason_key, 0) + 1

            if pctr is not None and not (np.isnan(pctr) or np.isinf(pctr)):
                self._predictions.append(float(pctr))

    def record_timeout(self, latency_ms: float = 20.0) -> None:
        """Convenience method for recording a prediction timeout."""
        self.record_inference(latency_ms=latency_ms, is_fallback=True, fallback_reason="timeout")

    def record_shadow_comparison(
        self,
        baseline_pctr: float,
        shadow_pctr: float,
        is_top1_match: bool = True,
    ) -> None:
        """Record a shadow evaluation event."""
        with self._lock:
            self._shadow_evaluations += 1
            if is_top1_match:
                self._shadow_agreements += 1
            delta = abs(shadow_pctr - baseline_pctr)
            self._shadow_deltas.append(float(delta))

    def record_feature_staleness(self, staleness_seconds: float) -> None:
        """Record the freshness/staleness of the feature vector."""
        with self._lock:
            if staleness_seconds > self._feature_staleness_max_sec:
                self._feature_staleness_max_sec = float(staleness_seconds)

    def record_feature_completeness(self, missing_count: int, total_count: int) -> None:
        """Record missing feature statistics."""
        with self._lock:
            self._total_features_missing += max(0, missing_count)
            self._total_features_evaluated += max(1, total_count)

    def set_reference_distribution(self, reference_scores: list[float] | np.ndarray) -> None:
        """Set baseline score distribution for PSI drift calculation."""
        with self._lock:
            self._reference_distribution = np.asarray(reference_scores, dtype=np.float64)

    def get_telemetry_summary(self) -> dict[str, Any]:
        """Compute full telemetry summary report."""
        with self._lock:
            lats = np.array(self._latencies) if self._latencies else np.array([0.0])
            total_inf = max(1, self._total_inferences)
            total_fallbacks = sum(self._fallback_breakdown.values())

            # Latency percentiles
            p50 = float(np.percentile(lats, 50)) if len(self._latencies) > 0 else 0.0
            p95 = float(np.percentile(lats, 95)) if len(self._latencies) > 0 else 0.0
            p99 = float(np.percentile(lats, 99)) if len(self._latencies) > 0 else 0.0
            mean_lat = float(np.mean(lats)) if len(self._latencies) > 0 else 0.0

            # PSI Drift calculation
            psi_score = 0.0
            drift_status = "stable"
            if len(self._predictions) >= 50 and len(self._reference_distribution) >= 50:
                preds_arr = np.array(self._predictions)
                psi_score, drift_status = calculate_psi(self._reference_distribution, preds_arr)

            # Missing feature rate
            feat_missing_rate = 0.0
            if self._total_features_evaluated > 0:
                feat_missing_rate = float(self._total_features_missing / self._total_features_evaluated)

            # Shadow metrics
            shadow_agreement_rate = 1.0
            mean_delta = 0.0
            if self._shadow_evaluations > 0:
                shadow_agreement_rate = float(self._shadow_agreements / self._shadow_evaluations)
                mean_delta = float(np.mean(self._shadow_deltas)) if self._shadow_deltas else 0.0

            return {
                "total_inferences": self._total_inferences,
                "latency_ms": {
                    "p50": round(p50, 2),
                    "p95": round(p95, 2),
                    "p99": round(p99, 2),
                    "mean": round(mean_lat, 2),
                },
                "timeouts": {
                    "count": self._total_timeouts,
                    "rate": round(float(self._total_timeouts / total_inf), 4),
                },
                "fallbacks": {
                    "total": total_fallbacks,
                    "rate": round(float(total_fallbacks / total_inf), 4),
                    "breakdown": dict(self._fallback_breakdown),
                },
                "features": {
                    "max_staleness_seconds": round(self._feature_staleness_max_sec, 2),
                    "missing_feature_rate": round(feat_missing_rate, 4),
                },
                "drift": {
                    "psi": round(psi_score, 4),
                    "status": drift_status,
                },
                "shadow_comparison": {
                    "total_evaluations": self._shadow_evaluations,
                    "top1_agreement_rate": round(shadow_agreement_rate, 4),
                    "mean_pctr_delta": round(mean_delta, 4),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def reset(self) -> None:
        """Reset collector state for testing or daily reset."""
        with self._lock:
            self._latencies.clear()
            self._predictions.clear()
            self._total_inferences = 0
            self._total_timeouts = 0
            for k in self._fallback_breakdown:
                self._fallback_breakdown[k] = 0
            self._feature_staleness_max_sec = 0.0
            self._total_features_evaluated = 0
            self._total_features_missing = 0
            self._shadow_evaluations = 0
            self._shadow_agreements = 0
            self._shadow_deltas.clear()


# Global Singleton
_ml_telemetry_singleton: MLTelemetryCollector | None = None
_telemetry_lock = threading.Lock()


def get_ml_telemetry() -> MLTelemetryCollector:
    """Get or initialize global ML telemetry collector singleton."""
    global _ml_telemetry_singleton
    if _ml_telemetry_singleton is None:
        with _telemetry_lock:
            if _ml_telemetry_singleton is None:
                _ml_telemetry_singleton = MLTelemetryCollector()
    return _ml_telemetry_singleton
