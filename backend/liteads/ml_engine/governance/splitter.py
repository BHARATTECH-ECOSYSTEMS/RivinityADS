"""
Time-Based Dataset Splitting and Label Leakage Detection (Phase 8).

Guarantees:
1. Strict temporal split invariant: train_end <= val_start <= test_start (with optional embargo window).
2. Fail-closed rejection of inverted or overlapping temporal splits.
3. Label leakage detection: rejects post-event outcomes (conversion_time, click_timestamp)
   and future campaign rollups (spent_total, campaign_total_conversions) from training features.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
import pandas as pd

from liteads.common.logger import get_logger

logger = get_logger(__name__)

PROHIBITED_LEAKAGE_COLUMNS: frozenset[str] = frozenset({
    # Future campaign aggregate rollups
    "spent_total",
    "campaign_total_conversions",
    "campaign_total_clicks",
    "campaign_total_impressions",
    "total_spend",
    "total_conversions",
    "total_clicks",
    "total_impressions",
    "daily_spent_final",
    # Post-event outcomes & attribution leakage
    "conversion_time",
    "conversion_timestamp",
    "click_timestamp",
    "click_time",
    "attribution_window",
    "post_click_event",
    "is_converted",
    "conversion_value",
    "revenue_generated",
    "post_click_duration",
    "payout_amount",
})


class TimeSplitError(ValueError):
    """Raised when temporal train/val/test split boundaries are invalid or violate ordering."""
    pass


class LabelLeakageError(ValueError):
    """Raised when label leakage columns or future features are detected."""

    def __init__(self, message: str, leakage_columns: list[str] | None = None):
        super().__init__(message)
        self.leakage_columns = leakage_columns or []


def _parse_dt(dt: datetime | str) -> datetime:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    # Parse string
    dt_clean = str(dt).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(dt_clean)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class TimeBasedSplitter:
    """
    Splits datasets temporally to prevent lookahead bias and future data leakage.
    Enforces train_end <= val_start and val_end <= test_start.
    """

    def __init__(
        self,
        train_end: datetime | str,
        val_start: datetime | str,
        val_end: datetime | str,
        test_start: datetime | str,
        test_end: datetime | str,
        embargo_hours: float = 0.0,
    ):
        self.train_end = _parse_dt(train_end)
        self.val_start = _parse_dt(val_start)
        self.val_end = _parse_dt(val_end)
        self.test_start = _parse_dt(test_start)
        self.test_end = _parse_dt(test_end)
        self.embargo = timedelta(hours=embargo_hours)

        self._validate_boundaries()

    def _validate_boundaries(self) -> None:
        """Validate strict time ordering and embargo spacing."""
        if self.val_start < self.train_end + self.embargo:
            raise TimeSplitError(
                f"Temporal split violation: val_start ({self.val_start.isoformat()}) must be >= "
                f"train_end ({self.train_end.isoformat()}) + embargo ({self.embargo})"
            )

        if self.val_end < self.val_start:
            raise TimeSplitError(
                f"Validation range inverted: val_end ({self.val_end.isoformat()}) < "
                f"val_start ({self.val_start.isoformat()})"
            )

        if self.test_start < self.val_end + self.embargo:
            raise TimeSplitError(
                f"Temporal split violation: test_start ({self.test_start.isoformat()}) must be >= "
                f"val_end ({self.val_end.isoformat()}) + embargo ({self.embargo})"
            )

        if self.test_end < self.test_start:
            raise TimeSplitError(
                f"Test range inverted: test_end ({self.test_end.isoformat()}) < "
                f"test_start ({self.test_start.isoformat()})"
            )

    def split_records(
        self,
        records: list[dict[str, Any]],
        time_key: str = "event_time",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Split a list of event dictionaries into train, val, and test subsets based on time_key.
        """
        train, val, test = [], [], []

        for record in records:
            if time_key not in record:
                raise KeyError(f"Record missing temporal key '{time_key}': {record}")
            t = _parse_dt(record[time_key])

            if t <= self.train_end:
                train.append(record)
            elif self.val_start <= t <= self.val_end:
                val.append(record)
            elif self.test_start <= t <= self.test_end:
                test.append(record)
            # Events in embargo gaps are intentionally discarded

        return train, val, test

    def split_dataframe(
        self,
        df: pd.DataFrame,
        time_col: str = "event_time",
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Split a pandas DataFrame temporally.
        """
        if time_col not in df.columns:
            raise KeyError(f"DataFrame missing temporal column '{time_col}'")

        series_dt = pd.to_datetime(df[time_col], utc=True)
        train_mask = series_dt <= self.train_end
        val_mask = (series_dt >= self.val_start) & (series_dt <= self.val_end)
        test_mask = (series_dt >= self.test_start) & (series_dt <= self.test_end)

        return df[train_mask].copy(), df[val_mask].copy(), df[test_mask].copy()


def check_label_leakage(
    columns_or_features: Iterable[str] | dict[str, Any] | pd.DataFrame,
) -> tuple[bool, list[str]]:
    """
    Inspect feature columns for future lookahead signals or post-event leakage.

    Returns:
        (is_clean: bool, detected_leakage: list[str])
    """
    detected: list[str] = []

    if isinstance(columns_or_features, pd.DataFrame):
        names = list(columns_or_features.columns)
    elif isinstance(columns_or_features, dict):
        names = list(columns_or_features.keys())
    else:
        names = list(columns_or_features)

    for col in names:
        norm = str(col).strip().lower()
        if norm in PROHIBITED_LEAKAGE_COLUMNS:
            detected.append(f"prohibited_leakage_column:{col}")
        elif any(norm.startswith(p) for p in ("future_", "post_click_", "conversion_post_")):
            detected.append(f"suspicious_future_column:{col}")
        elif norm.endswith(("_total_conversions", "_total_clicks", "_total_spend")):
            detected.append(f"campaign_rollup_leakage:{col}")

    return len(detected) == 0, detected


def assert_no_label_leakage(
    columns_or_features: Iterable[str] | dict[str, Any] | pd.DataFrame,
) -> None:
    """
    Assert that features do not contain label leakage; raises LabelLeakageError if detected.
    """
    is_clean, detected = check_label_leakage(columns_or_features)
    if not is_clean:
        msg = f"Label Leakage Detected in Features: {detected}"
        logger.error(msg, leakage=detected)
        raise LabelLeakageError(msg, leakage_columns=detected)
