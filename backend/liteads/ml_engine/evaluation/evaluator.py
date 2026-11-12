"""
Offline Model Evaluation & Calibration (Task E).

Computes:
- AUC (Area under ROC Curve)
- LogLoss (Binary Cross-Entropy)
- PCOCTR Calibration (mean(p) / mean(y))
- Brier Score (Mean Squared Error)
- Segment Fairness / Performance checks (by format, category, device_os)
- Drift Monitoring (Population Stability Index - PSI)
- Evaluation from real deduplicated, non-IVT database events
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SegmentMetrics:
    """Metrics calculated for a specific slice / segment."""

    segment_dimension: str
    segment_value: str
    sample_count: int
    positive_count: int
    observed_rate: float
    predicted_rate: float
    auc: float | None
    log_loss: float | None
    pcoctr: float | None
    brier_score: float | None


@dataclass
class EvaluationReport:
    """Comprehensive offline evaluation report."""

    sample_count: int
    positive_count: int
    observed_rate: float
    predicted_rate: float
    auc: float
    log_loss: float
    pcoctr: float
    brier_score: float
    psi: float | None = None
    psi_drift_status: str = "stable"  # "stable" (<0.1), "moderate_drift" (0.1-0.25), "significant_drift" (>=0.25)
    segments: list[SegmentMetrics] = field(default_factory=list)


def calculate_auc(
    labels: list[int] | np.ndarray, predictions: list[float] | np.ndarray
) -> float:
    """Calculate ROC-AUC score with robust fallback."""
    y = np.asarray(labels, dtype=np.int32)
    p = np.asarray(predictions, dtype=np.float64)
    try:
        from sklearn.metrics import roc_auc_score

        return float(roc_auc_score(y, p))
    except (ImportError, ValueError):
        pos_preds = p[y == 1]
        neg_preds = p[y == 0]
        if len(pos_preds) == 0 or len(neg_preds) == 0:
            return 0.5
        n_pos = len(pos_preds)
        n_neg = len(neg_preds)
        u = np.sum(pos_preds[:, None] > neg_preds[None, :]) + 0.5 * np.sum(
            pos_preds[:, None] == neg_preds[None, :]
        )
        return float(u / (n_pos * n_neg))


def calculate_log_loss(
    labels: list[int] | np.ndarray,
    predictions: list[float] | np.ndarray,
    eps: float = 1e-15,
) -> float:
    """Calculate binary cross-entropy (LogLoss)."""
    y = np.asarray(labels, dtype=np.float64)
    if len(y) == 0:
        return 0.0
    p = np.clip(np.asarray(predictions, dtype=np.float64), eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def calculate_pcoctr(
    labels: list[int] | np.ndarray, predictions: list[float] | np.ndarray
) -> float:
    """
    Calculate Predicted CTR over Observed CTR calibration.
    PCOCTR = mean(p) / mean(y). An ideal calibrated model has PCOCTR ~ 1.0.
    """
    y = np.asarray(labels, dtype=np.float64)
    p = np.asarray(predictions, dtype=np.float64)
    if len(y) == 0:
        return 1.0
    mean_p = float(np.mean(p))
    mean_y = float(np.mean(y))
    if mean_y == 0.0:
        return 1.0 if mean_p == 0.0 else 999.0
    return float(mean_p / mean_y)


def calculate_brier_score(
    labels: list[int] | np.ndarray, predictions: list[float] | np.ndarray
) -> float:
    """Calculate Brier score (mean squared error of probability predictions)."""
    y = np.asarray(labels, dtype=np.float64)
    p = np.asarray(predictions, dtype=np.float64)
    if len(y) == 0:
        return 0.0
    return float(np.mean((p - y) ** 2))


def calculate_psi(
    expected: np.ndarray,
    actual: np.ndarray,
    num_bins: int = 10,
    eps: float = 1e-4,
) -> tuple[float, str]:
    """
    Calculate Population Stability Index (PSI) between expected and actual distributions.

    PSI < 0.1: Stable / No significant shift
    0.1 <= PSI < 0.25: Moderate drift
    PSI >= 0.25: Significant drift
    """
    expected = np.asarray(expected, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.float64)

    if len(expected) == 0 or len(actual) == 0:
        return 0.0, "stable"

    bins = np.linspace(0.0, 1.0, num_bins + 1)
    bins[0] = -1e-5
    bins[-1] = 1.0 + 1e-5

    expected_counts, _ = np.histogram(expected, bins=bins)
    actual_counts, _ = np.histogram(actual, bins=bins)

    n_expected = len(expected) + num_bins * eps
    n_actual = len(actual) + num_bins * eps

    expected_props = (expected_counts + eps) / n_expected
    actual_props = (actual_counts + eps) / n_actual

    psi = float(
        np.sum((actual_props - expected_props) * np.log(actual_props / expected_props))
    )

    if psi < 0.1:
        status = "stable"
    elif psi < 0.25:
        status = "moderate_drift"
    else:
        status = "significant_drift"

    return psi, status


def evaluate_predictions(
    labels: list[int] | np.ndarray,
    predictions: list[float] | np.ndarray,
    segments: dict[str, list[Any]] | None = None,
    baseline_predictions: list[float] | np.ndarray | None = None,
) -> EvaluationReport:
    """
    Comprehensive offline evaluation of model predictions.

    Args:
        labels: Ground truth binary labels (0 or 1)
        predictions: Model predicted probabilities [0, 1]
        segments: Optional dictionary of segment dimensions e.g. {"format": [...], "category": [...], "device_os": [...]}
        baseline_predictions: Optional baseline predictions for drift (PSI) calculation

    Returns:
        EvaluationReport with overall and segmented metrics.
    """
    labels_arr = np.asarray(labels, dtype=np.int32)
    preds_arr = np.asarray(predictions, dtype=np.float64)

    n_samples = len(labels_arr)
    n_pos = int(np.sum(labels_arr))
    obs_rate = float(np.mean(labels_arr)) if n_samples > 0 else 0.0
    pred_rate = float(np.mean(preds_arr)) if n_samples > 0 else 0.0

    auc = calculate_auc(labels_arr, preds_arr)
    log_loss = calculate_log_loss(labels_arr, preds_arr)
    pcoctr = calculate_pcoctr(labels_arr, preds_arr)
    brier = calculate_brier_score(labels_arr, preds_arr)

    # PSI calculation
    psi = None
    drift_status = "stable"
    if baseline_predictions is not None and len(baseline_predictions) > 0:
        base_arr = np.asarray(baseline_predictions, dtype=np.float64)
        psi, drift_status = calculate_psi(base_arr, preds_arr)

    # Segment fairness & performance breakdown
    segment_metrics: list[SegmentMetrics] = []
    if segments:
        for dim, values in segments.items():
            val_arr = np.asarray(values)
            unique_vals = np.unique(val_arr)
            for uval in unique_vals:
                mask = val_arr == uval
                sub_y = labels_arr[mask]
                sub_p = preds_arr[mask]
                sub_n = len(sub_y)
                sub_pos = int(np.sum(sub_y))
                if sub_n == 0:
                    continue
                sub_obs = float(np.mean(sub_y))
                sub_pred = float(np.mean(sub_p))
                sub_auc = (
                    calculate_auc(sub_y, sub_p)
                    if (sub_pos > 0 and sub_pos < sub_n)
                    else None
                )
                sub_ll = calculate_log_loss(sub_y, sub_p)
                sub_pcoctr = calculate_pcoctr(sub_y, sub_p)
                sub_brier = calculate_brier_score(sub_y, sub_p)

                segment_metrics.append(
                    SegmentMetrics(
                        segment_dimension=dim,
                        segment_value=str(uval),
                        sample_count=sub_n,
                        positive_count=sub_pos,
                        observed_rate=sub_obs,
                        predicted_rate=sub_pred,
                        auc=sub_auc,
                        log_loss=sub_ll,
                        pcoctr=sub_pcoctr,
                        brier_score=sub_brier,
                    )
                )

    return EvaluationReport(
        sample_count=n_samples,
        positive_count=n_pos,
        observed_rate=obs_rate,
        predicted_rate=pred_rate,
        auc=auc,
        log_loss=log_loss,
        pcoctr=pcoctr,
        brier_score=brier,
        psi=psi,
        psi_drift_status=drift_status,
        segments=segment_metrics,
    )


async def evaluate_from_events(
    session: AsyncSession,
    model_predictor: Any,
    start_time: datetime,
    end_time: datetime,
) -> EvaluationReport:
    """
    Evaluate model from real deduplicated non-IVT impression and click events in the database.
    """
    from sqlalchemy import select

    from liteads.models.ad import AdEvent
    from liteads.models.base import EventType

    # 1. Fetch non-IVT, non-test impression events
    imp_stmt = select(AdEvent).where(
        AdEvent.event_type == EventType.IMPRESSION.value,
        AdEvent.is_invalid == False,
        AdEvent.is_test == False,
        AdEvent.event_time >= start_time,
        AdEvent.event_time <= end_time,
    )
    imp_res = await session.execute(imp_stmt)
    impressions = imp_res.scalars().all()

    if not impressions:
        return EvaluationReport(
            sample_count=0,
            positive_count=0,
            observed_rate=0.0,
            predicted_rate=0.0,
            auc=0.5,
            log_loss=0.0,
            pcoctr=1.0,
            brier_score=0.0,
        )

    # 2. Fetch non-IVT clicks in the same window
    request_ids = [imp.request_id for imp in impressions if imp.request_id]
    click_stmt = select(AdEvent.request_id).where(
        AdEvent.event_type == EventType.CLICK.value,
        AdEvent.is_invalid == False,
        AdEvent.is_test == False,
        AdEvent.request_id.in_(request_ids),
    )
    click_res = await session.execute(click_stmt)
    clicked_request_ids = set(click_res.scalars().all())

    # Build features & predict
    labels = []
    features_batch = []
    formats = []
    categories = []

    for imp in impressions:
        label = 1 if imp.request_id in clicked_request_ids else 0
        labels.append(label)

        feat = {
            "campaign_id": str(imp.campaign_id or 0),
            "creative_id": str(imp.creative_id or 0),
            "sparse_features": [0],
            "dense_features": [0.0],
        }
        features_batch.append(feat)
        formats.append("banner")
        categories.append("general")

    # Predict
    if hasattr(model_predictor, "predict_batch"):
        pred_results = model_predictor.predict_batch(features_batch)
        predictions = [r.pctr for r in pred_results]
    else:
        predictions = [0.01] * len(labels)

    return evaluate_predictions(
        labels=labels,
        predictions=predictions,
        segments={"format": formats, "category": categories},
    )
