"""
Offline Calibration, Business-Value Lift, and Privacy-Safe Segment Evaluation (Phase 8).

Provides:
1. Expected Calibration Error (ECE) across probability confidence bins.
2. Business-value ranking lift (eCPM-weighted value compared to statistical baseline).
3. Segment fairness & accuracy evaluation with strict privacy suppression (min_segment_samples >= 30).
"""

from typing import Any
import numpy as np

from liteads.common.logger import get_logger
from liteads.ml_engine.evaluation.evaluator import (
    calculate_auc,
    calculate_brier_score,
    calculate_log_loss,
    calculate_pcoctr,
    calculate_psi,
)

logger = get_logger(__name__)


def calculate_ece(
    labels: list[int] | np.ndarray,
    predictions: list[float] | np.ndarray,
    num_bins: int = 10,
) -> tuple[float, list[dict[str, Any]]]:
    """
    Calculate Expected Calibration Error (ECE) over confidence bins.

    ECE = sum_{b=1}^B ( |B_b| / N ) * | acc(B_b) - conf(B_b) |
    """
    y = np.asarray(labels, dtype=np.int32)
    p = np.clip(np.asarray(predictions, dtype=np.float64), 0.0, 1.0)
    n = len(y)

    if n == 0:
        return 0.0, []

    bin_edges = np.linspace(0.0, 1.0, num_bins + 1)
    bin_details: list[dict[str, Any]] = []
    ece = 0.0

    for i in range(num_bins):
        low = bin_edges[i]
        high = bin_edges[i + 1]
        if i == num_bins - 1:
            mask = (p >= low) & (p <= high)
        else:
            mask = (p >= low) & (p < high)

        bin_count = int(np.sum(mask))
        if bin_count > 0:
            bin_acc = float(np.mean(y[mask]))
            bin_conf = float(np.mean(p[mask]))
            calibration_gap = abs(bin_acc - bin_conf)
            ece += (bin_count / n) * calibration_gap
            bin_details.append({
                "bin_index": i,
                "bin_low": round(float(low), 4),
                "bin_high": round(float(high), 4),
                "sample_count": bin_count,
                "observed_accuracy": round(bin_acc, 6),
                "mean_confidence": round(bin_conf, 6),
                "gap": round(calibration_gap, 6),
            })
        else:
            bin_details.append({
                "bin_index": i,
                "bin_low": round(float(low), 4),
                "bin_high": round(float(high), 4),
                "sample_count": 0,
                "observed_accuracy": 0.0,
                "mean_confidence": round((low + high) / 2.0, 4),
                "gap": 0.0,
            })

    return float(ece), bin_details


def calculate_business_value_lift(
    bids: list[float] | np.ndarray,
    model_pctr: list[float] | np.ndarray,
    baseline_pctr: list[float] | np.ndarray,
    bid_types: list[int] | None = None,
    group_size: int = 5,
) -> dict[str, float]:
    """
    Calculate business-value lift metrics:
    - Expected revenue / eCPM lift between model and statistical baseline
    - Top-1 ranking agreement across synthetic auction slates

    Args:
        bids: Candidate bids (in currency units, e.g. INR)
        model_pctr: Predicted CTR from the candidate ML model
        baseline_pctr: Predicted CTR from the authoritative baseline
        bid_types: 1 for CPM, 2 for CPC (defaults to CPC)
        group_size: Size of simulated auction groups to measure top-1 rank agreement
    """
    bids_arr = np.asarray(bids, dtype=np.float64)
    m_pctr = np.asarray(model_pctr, dtype=np.float64)
    b_pctr = np.asarray(baseline_pctr, dtype=np.float64)
    n = len(bids_arr)

    if n == 0:
        return {
            "model_ecpm_mean": 0.0,
            "baseline_ecpm_mean": 0.0,
            "ecpm_lift_ratio": 0.0,
            "top1_agreement_rate": 1.0,
        }

    if bid_types is None:
        # Default all to CPC
        model_ecpm = bids_arr * m_pctr * 1000.0
        base_ecpm = bids_arr * b_pctr * 1000.0
    else:
        types_arr = np.asarray(bid_types, dtype=np.int32)
        model_ecpm = np.where(types_arr == 1, bids_arr, bids_arr * m_pctr * 1000.0)
        base_ecpm = np.where(types_arr == 1, bids_arr, bids_arr * b_pctr * 1000.0)

    model_mean = float(np.mean(model_ecpm))
    base_mean = float(np.mean(base_ecpm))
    lift_ratio = float((model_mean - base_mean) / (base_mean + 1e-9))

    # Top-1 agreement in chunks
    agreements = 0
    total_slates = 0
    for start in range(0, n, group_size):
        end = min(start + group_size, n)
        if end - start <= 1:
            continue
        m_top = int(np.argmax(model_ecpm[start:end]))
        b_top = int(np.argmax(base_ecpm[start:end]))
        if m_top == b_top:
            agreements += 1
        total_slates += 1

    top1_agreement = float(agreements / total_slates) if total_slates > 0 else 1.0

    return {
        "model_ecpm_mean": round(model_mean, 4),
        "baseline_ecpm_mean": round(base_mean, 4),
        "ecpm_lift_ratio": round(lift_ratio, 4),
        "top1_agreement_rate": round(top1_agreement, 4),
    }


def evaluate_segments_privacy_safe(
    labels: list[int] | np.ndarray,
    predictions: list[float] | np.ndarray,
    segments: dict[str, list[Any]],
    min_segment_samples: int = 30,
) -> list[dict[str, Any]]:
    """
    Evaluate slices / cohorts across dimensions with strict privacy suppression:
    Cohorts with fewer than min_segment_samples (default 30) are suppressed from returning
    individual accuracy / AUC / log-loss metrics to eliminate re-identification risks.
    """
    labels_arr = np.asarray(labels, dtype=np.int32)
    preds_arr = np.asarray(predictions, dtype=np.float64)

    results: list[dict[str, Any]] = []

    for dim_name, values in segments.items():
        val_arr = np.asarray(values)
        unique_vals = np.unique(val_arr)

        for uval in unique_vals:
            mask = val_arr == uval
            sub_y = labels_arr[mask]
            sub_p = preds_arr[mask]
            sub_n = len(sub_y)

            entry: dict[str, Any] = {
                "dimension": dim_name,
                "value": str(uval),
                "sample_count": sub_n,
                "is_suppressed": False,
                "suppression_reason": None,
            }

            if sub_n < min_segment_samples:
                # Privacy suppression gate
                entry["is_suppressed"] = True
                entry["suppression_reason"] = (
                    f"Sample count ({sub_n}) is below privacy threshold ({min_segment_samples})"
                )
                entry["auc"] = None
                entry["log_loss"] = None
                entry["pcoctr"] = None
                entry["brier_score"] = None
                entry["ece"] = None
            else:
                pos_count = int(np.sum(sub_y))
                entry["positive_count"] = pos_count
                entry["observed_rate"] = round(float(np.mean(sub_y)), 6)
                entry["predicted_rate"] = round(float(np.mean(sub_p)), 6)
                entry["auc"] = (
                    round(calculate_auc(sub_y, sub_p), 4)
                    if (0 < pos_count < sub_n)
                    else 0.5
                )
                entry["log_loss"] = round(calculate_log_loss(sub_y, sub_p), 4)
                entry["pcoctr"] = round(calculate_pcoctr(sub_y, sub_p), 4)
                entry["brier_score"] = round(calculate_brier_score(sub_y, sub_p), 6)
                ece_val, _ = calculate_ece(sub_y, sub_p)
                entry["ece"] = round(ece_val, 4)

            results.append(entry)

    return results
