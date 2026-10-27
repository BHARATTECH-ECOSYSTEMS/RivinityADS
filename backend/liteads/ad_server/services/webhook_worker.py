"""Durable Webhook Outbox Worker (Phase 4B).

Processes queued webhook deliveries using lease/claim semantics with
FOR UPDATE SKIP LOCKED, exponential backoff with jitter for transient errors,
dead-letter classification for permanent 4xx / SSRF failures, and
protection against stale worker lease overwrites.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import random
from typing import Any, Optional
import uuid

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.webhook_dispatcher import _deliver, _sign_payload
from liteads.common.cache import redis_client
from liteads.common.database import db
from liteads.models.webhook import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)

# ── Configuration Constants ──────────────────────────────────────────────────
DEFAULT_CLAIM_BATCH_SIZE = 20
DEFAULT_LEASE_SECONDS = 60  # Worker lease timeout for crash recovery
DEFAULT_POLL_INTERVAL_SECONDS = 1.0  # Polling interval when idle
BASE_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 3600.0  # 1 hour max retry backoff
DISTRIBUTED_LOCK_KEY = "lock:webhook_outbox_worker_leader"
DISTRIBUTED_LOCK_TTL = 30  # seconds


def calculate_backoff(attempt: int) -> float:
    """Calculate capped exponential backoff with full jitter.

    formula: min(MAX_BACKOFF, (BASE_BACKOFF ** attempt) * uniform(0.5, 1.5))
    """
    factor = BASE_BACKOFF_SECONDS ** max(1, attempt)
    jitter = random.uniform(0.5, 1.5)
    return min(MAX_BACKOFF_SECONDS, factor * jitter)


def is_permanent_failure(status_code: int | None, error: str | None) -> bool:
    """Determine whether an HTTP delivery error is permanent (do not retry).

    Permanent errors:
    - Destination rejected by SSRF validation or transport
    - HTTP 4xx client errors (400, 401, 403, 404, 405, 410, 422), except 429 Too Many Requests
    """
    if error and ("Destination rejected" in error or "SSRF" in error):
        return True
    if status_code is not None:
        if 400 <= status_code < 500 and status_code != 429:
            return True
    return False


class WebhookOutboxWorker:
    """Worker engine for claiming and processing durable webhook delivery jobs."""

    def __init__(
        self,
        session_factory: Any = None,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        lease_duration_seconds: Optional[int] = None,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = lease_duration_seconds or lease_seconds

    def _get_session_factory(self, passed_factory: Any = None) -> Any:
        if passed_factory is not None:
            return passed_factory
        if self.session_factory is not None:
            return self.session_factory
        from liteads.common.database import db
        return db.session_factory

    async def claim_jobs(
        self,
        session: Optional[AsyncSession] = None,
        batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
    ) -> list[WebhookDelivery]:
        """Claim a batch of ready or abandoned jobs using FOR UPDATE SKIP LOCKED.

        Selects:
        - Ready jobs: status == 'pending' AND next_attempt_at <= now
        - Abandoned jobs: status == 'processing' AND lease_expires_at < now (worker crash recovery)
        where attempts < max_attempts.

        Returns list of claimed WebhookDelivery instances.
        """
        if session is None:
            factory = self._get_session_factory()
            async with factory() as s:
                return await self._claim_jobs_session(s, batch_size)
        return await self._claim_jobs_session(session, batch_size)

    async def _claim_jobs_session(
        self,
        session: AsyncSession,
        batch_size: int,
    ) -> list[WebhookDelivery]:
        now = datetime.now(timezone.utc)
        lease_expires = now + timedelta(seconds=self.lease_seconds)

        stmt = (
            select(WebhookDelivery)
            .where(
                or_(
                    and_(
                        WebhookDelivery.status == "pending",
                        WebhookDelivery.next_attempt_at <= now,
                    ),
                    and_(
                        WebhookDelivery.status == "processing",
                        WebhookDelivery.lease_expires_at < now,
                    ),
                ),
                WebhookDelivery.attempts < WebhookDelivery.max_attempts,
            )
            .order_by(WebhookDelivery.next_attempt_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        result = await session.execute(stmt)
        jobs = list(result.scalars().all())

        for job in jobs:
            job.status = "processing"
            job.lease_token = uuid.uuid4().hex
            job.lease_expires_at = lease_expires

        await session.commit()
        return jobs

    async def _persist_delivery_outcome(
        self,
        delivery_id: int,
        success: bool,
        http_status: Optional[int],
        error_msg: Optional[str],
        is_permanent_err: bool = False,
        claim_token: Optional[str] = None,
        session_factory: Any = None,
    ) -> bool:
        """Persist delivery outcome with database-enforced atomic lease token fencing."""
        token_to_check = claim_token or self.worker_id
        if not token_to_check:
            logger.warning("Outcome persistence rejected: no claim token provided")
            return False

        now = datetime.now(timezone.utc)
        factory = self._get_session_factory(session_factory)

        async with factory() as session:
            # 1. Fetch current attempt count and config without binding mutable ORM entity to the session.
            stmt_select = select(
                WebhookDelivery.webhook_id,
                WebhookDelivery.attempts,
                WebhookDelivery.max_attempts,
                WebhookDelivery.next_attempt_at,
            ).where(
                WebhookDelivery.id == delivery_id,
                WebhookDelivery.lease_token == token_to_check,
            )
            res_select = await session.execute(stmt_select)
            row = res_select.first()
            if not row:
                logger.warning(
                    "Fenced update aborted: job not found or lease token no longer matches",
                    extra={"delivery_id": delivery_id, "claim_token": token_to_check},
                )
                return False

            webhook_id, attempts, max_attempts, current_next_attempt = row
            new_attempts = attempts + 1
            webhook_update_col = None
            next_attempt_at = current_next_attempt

            if success:
                new_status = "delivered"
                delivered_at = now
                new_error = None
                webhook_update_col = Webhook.deliveries_ok
            else:
                delivered_at = None
                new_error = error_msg[:500] if error_msg else "Delivery failed"

                if is_permanent_err or is_permanent_failure(http_status, error_msg):
                    new_status = "failed"
                    webhook_update_col = Webhook.deliveries_failed
                elif new_attempts >= max_attempts:
                    new_status = "failed"  # Retry exhaustion / dead-letter
                    webhook_update_col = Webhook.deliveries_failed
                else:
                    new_status = "pending"
                    delay = calculate_backoff(new_attempts)
                    next_attempt_at = now + timedelta(seconds=delay)
                    webhook_update_col = None

            # 2. Database-enforced atomic conditional update:
            # Row is updated if and only if lease_token still matches token_to_check.
            stmt_update = (
                update(WebhookDelivery)
                .where(
                    WebhookDelivery.id == delivery_id,
                    WebhookDelivery.lease_token == token_to_check,
                )
                .values(
                    status=new_status,
                    attempts=new_attempts,
                    last_attempt_at=now,
                    last_http_status=http_status,
                    last_error=new_error,
                    delivered_at=delivered_at,
                    next_attempt_at=next_attempt_at,
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            res_update = await session.execute(stmt_update)

            # 3. Check affected-row count. If 0, another claim took over the lease.
            if res_update.rowcount == 0:
                logger.warning(
                    "Fenced update rejected stale attempt; claim_token no longer matches in DB",
                    extra={"delivery_id": delivery_id, "claim_token": token_to_check},
                )
                await session.rollback()
                return False

            # 4. Update stats counter on parent webhook transactionally consistent with fenced update
            if webhook_update_col is not None:
                await session.execute(
                    update(Webhook)
                    .where(Webhook.id == webhook_id)
                    .values({webhook_update_col.key: webhook_update_col + 1})
                )

            await session.commit()
            return success

    async def process_job(
        self,
        job_id: int,
        session_factory: Any = None,
        claim_token: Optional[str] = None,
    ) -> bool:
        """Process a single claimed delivery job and record outcome transactionally."""
        factory = self._get_session_factory(session_factory)

        # 1. Read job and parent webhook in a short transaction
        async with factory() as session:
            stmt = select(
                WebhookDelivery.lease_token,
                WebhookDelivery.payload,
                WebhookDelivery.webhook_id,
            ).where(WebhookDelivery.id == job_id)
            res = await session.execute(stmt)
            job_row = res.first()
            if not job_row:
                return False

            row_token, payload_str, webhook_id = job_row
            if claim_token is not None and row_token != claim_token:
                return False
            effective_token = claim_token or row_token
            if not effective_token:
                return False

            webhook = await session.get(Webhook, webhook_id)
            if not webhook:
                return await self._persist_delivery_outcome(
                    delivery_id=job_id,
                    success=False,
                    http_status=None,
                    error_msg="Webhook no longer exists",
                    is_permanent_err=True,
                    claim_token=effective_token,
                    session_factory=factory,
                )

            if not webhook.is_active:
                return await self._persist_delivery_outcome(
                    delivery_id=job_id,
                    success=False,
                    http_status=None,
                    error_msg="Webhook is disabled",
                    is_permanent_err=True,
                    claim_token=effective_token,
                    session_factory=factory,
                )

            url = webhook.url
            secret = webhook.secret

        # 2. Outbound HTTP delivery outside database transaction
        body = payload_str.encode("utf-8")
        signature = _sign_payload(secret, body)
        ok, status_code, err = await _deliver(url, body, signature)

        # 3. Record outcome safely with atomic lease token fencing
        return await self._persist_delivery_outcome(
            delivery_id=job_id,
            success=ok,
            http_status=status_code,
            error_msg=err,
            claim_token=effective_token,
            session_factory=factory,
        )

    async def run_once(
        self,
        session_factory: Any = None,
        batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
    ) -> int:
        """Claim and execute one batch of jobs. Returns number of jobs processed."""
        factory = self._get_session_factory(session_factory)
        claimed_jobs = await self.claim_jobs(batch_size=batch_size)

        if not claimed_jobs:
            return 0

        # Execute deliveries concurrently without holding DB locks, passing claim token
        tasks = [
            self.process_job(job.id, session_factory=factory, claim_token=job.lease_token)
            for job in claimed_jobs
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        return len(claimed_jobs)

    async def process_batch(
        self,
        batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
    ) -> int:
        """Alias for run_once using worker's configured session factory."""
        return await self.run_once(batch_size=batch_size)

    async def run_loop(
        self,
        session_factory: Any,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Continuous worker execution loop with graceful shutdown support."""
        logger.info(f"Starting WebhookOutboxWorker [{self.worker_id}]")
        while stop_event is None or not stop_event.is_set():
            try:
                count = await self.run_once(session_factory)
                if count == 0:
                    await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in WebhookOutboxWorker loop: {exc}")
                await asyncio.sleep(poll_interval)
        logger.info(f"Stopped WebhookOutboxWorker [{self.worker_id}]")


class WebhookWorkerTask:
    """Manages the background outbox worker lifecycle in the main application."""

    def __init__(self, poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS):
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._worker = WebhookOutboxWorker()

    @property
    def is_running(self) -> bool:
        """Return True if background worker loop task is currently active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the background worker task if not already running."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("WebhookWorkerTask background runner started")

    async def stop(self) -> None:
        """Signal and await clean shutdown of the background worker."""
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("WebhookWorkerTask background runner stopped")

    async def _run(self) -> None:
        await self._worker.run_loop(
            session_factory=db.session_factory,
            poll_interval=self.poll_interval,
            stop_event=self._stop_event,
        )


# Singleton task instance
webhook_worker_task = WebhookWorkerTask()


async def main() -> None:
    """CLI entry point for running a standalone dedicated outbox worker container."""
    from liteads.common.config import get_settings
    from liteads.common.database import init_db, close_db

    settings = get_settings()
    logger.info("Initializing database connection for standalone webhook worker")
    await init_db()

    worker = WebhookOutboxWorker()
    stop_event = asyncio.Event()

    try:
        await worker.run_loop(
            session_factory=db.session_factory,
            poll_interval=DEFAULT_POLL_INTERVAL_SECONDS,
            stop_event=stop_event,
        )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
