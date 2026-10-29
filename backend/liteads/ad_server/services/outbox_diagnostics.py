"""Outbox diagnostics and operational metrics service (Phase 12B).

Provides bounded, safe operational visibility into persistent outbox backlogs,
queue depths, retry backoff delays, processing leases, and oldest pending event age
without performing expensive full-table scans or exposing sensitive payload data.
"""

from datetime import datetime, timezone
import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.middleware.metrics import update_outbox_gauge
from liteads.common.database import db
from liteads.models.notification import NotificationDelivery
from liteads.models.webhook import WebhookDelivery
from liteads.schemas.response import OutboxHealthResponse, OutboxQueueMetrics

logger = logging.getLogger(__name__)

# Operational alert threshold: alert if oldest pending event age exceeds 600s (10 minutes)
MAX_HEALTHY_PENDING_AGE_SECONDS = 600.0


async def get_queue_metrics(session: AsyncSession, model: type) -> OutboxQueueMetrics:
    """Calculate bounded metrics for a specific outbox table model.

    Executes:
    1. An indexed GROUP BY status count to capture status totals in a single pass.
    2. If pending count > 0, an indexed count of delayed items (next_attempt_at > now).
    3. If pending count > 0, a bounded (LIMIT 1) query for the oldest pending event timestamp.

    No payloads, credentials, or customer data are ever fetched or logged.
    """
    now = datetime.now(timezone.utc)

    # 1. Aggregate status counts in a single indexed query
    stmt_counts = select(model.status, func.count(model.id)).group_by(model.status)
    res_counts = await session.execute(stmt_counts)
    counts = dict(res_counts.all())

    pending_count = counts.get("pending", 0)
    processing_count = counts.get("processing", 0)
    failed_count = counts.get("failed", 0)

    delayed_count = 0
    ready_count = 0
    oldest_age = 0.0

    if pending_count > 0:
        # 2. Count delayed items (status='pending' and next_attempt_at > now)
        stmt_delayed = select(func.count(model.id)).where(
            model.status == "pending",
            model.next_attempt_at > now,
        )
        res_del = await session.execute(stmt_delayed)
        delayed_count = res_del.scalar_one_or_none() or 0
        ready_count = max(0, pending_count - delayed_count)

        # 3. Oldest pending item age (LIMIT 1, bounded)
        stmt_oldest = (
            select(model.created_at)
            .where(model.status == "pending")
            .order_by(model.created_at.asc())
            .limit(1)
        )
        res_old = await session.execute(stmt_oldest)
        oldest_dt = res_old.scalar_one_or_none()
        if oldest_dt:
            if oldest_dt.tzinfo is None:
                oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
            oldest_age = max(0.0, (now - oldest_dt).total_seconds())

    return OutboxQueueMetrics(
        pending_count=pending_count,
        delayed_count=delayed_count,
        ready_count=ready_count,
        processing_count=processing_count,
        failed_count=failed_count,
        oldest_pending_age_seconds=round(oldest_age, 2),
    )


async def collect_outbox_diagnostics(
    session: Optional[AsyncSession] = None,
    update_metrics: bool = True,
) -> OutboxHealthResponse:
    """Collect diagnostics for all persistent outbox queues in the application.

    Optionally updates Prometheus gauges for operational scrape readiness.
    """
    if session is None:
        async with db.session() as s:
            return await _collect_outbox_diagnostics_session(s, update_metrics=update_metrics)
    return await _collect_outbox_diagnostics_session(session, update_metrics=update_metrics)


async def _collect_outbox_diagnostics_session(
    session: AsyncSession,
    update_metrics: bool = True,
) -> OutboxHealthResponse:
    try:
        webhook_metrics = await get_queue_metrics(session, WebhookDelivery)
    except Exception as exc:
        logger.error(f"Error querying WebhookDelivery outbox metrics: {exc}")
        webhook_metrics = OutboxQueueMetrics()

    try:
        notif_metrics = await get_queue_metrics(session, NotificationDelivery)
    except Exception as exc:
        logger.error(f"Error querying NotificationDelivery outbox metrics: {exc}")
        notif_metrics = OutboxQueueMetrics()

    queues = {
        "webhook_deliveries": webhook_metrics,
        "notification_deliveries": notif_metrics,
    }

    if update_metrics:
        update_outbox_gauge(
            queue="webhook_deliveries",
            pending=webhook_metrics.pending_count,
            delayed=webhook_metrics.delayed_count,
            processing=webhook_metrics.processing_count,
            failed=webhook_metrics.failed_count,
            oldest_age_seconds=webhook_metrics.oldest_pending_age_seconds,
        )
        update_outbox_gauge(
            queue="notification_deliveries",
            pending=notif_metrics.pending_count,
            delayed=notif_metrics.delayed_count,
            processing=notif_metrics.processing_count,
            failed=notif_metrics.failed_count,
            oldest_age_seconds=notif_metrics.oldest_pending_age_seconds,
        )

    # Determine status: "degraded" if backlog is too old, else "healthy"
    is_degraded = (
        webhook_metrics.oldest_pending_age_seconds > MAX_HEALTHY_PENDING_AGE_SECONDS
        or notif_metrics.oldest_pending_age_seconds > MAX_HEALTHY_PENDING_AGE_SECONDS
    )
    status = "degraded" if is_degraded else "healthy"

    return OutboxHealthResponse(status=status, queues=queues)
