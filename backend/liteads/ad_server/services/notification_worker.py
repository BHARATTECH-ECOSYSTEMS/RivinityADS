"""Durable Notification Delivery Worker (Phase 6).

Processes queued notification deliveries (e.g. email) using atomic lease/claim semantics
with FOR UPDATE SKIP LOCKED, exponential backoff with jitter for transient errors,
dead-letter classification for permanent delivery errors or exhausted retries, and
protection against stale worker lease overwrites.

Ambiguous-Outcome SMTP Edge Case:
If a remote SMTP server accepts a message and the network connection is severed before
the worker receives the final '250 OK' acknowledgment, the worker observes a transport
failure and schedules a retry. Consequently, SMTP delivery is inherently at-least-once
across catastrophic network partitions; exactly-once delivery across remote network
failures cannot be guaranteed by the SMTP protocol without distributed transactional
mail transport protocols.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import random
import re
import smtplib
from typing import Any, Optional
import uuid

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.middleware.metrics import publish_worker_heartbeat
from liteads.common.cache import redis_client
from liteads.common.database import db
from liteads.common.email import EmailDeliveryError, send_email
from liteads.models.notification import NotificationDelivery

logger = logging.getLogger(__name__)

# ── Configuration Constants ──────────────────────────────────────────────────
DEFAULT_CLAIM_BATCH_SIZE = 20
DEFAULT_LEASE_SECONDS = 60  # Worker lease timeout for crash recovery
DEFAULT_POLL_INTERVAL_SECONDS = 1.0  # Polling interval when idle
BASE_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 3600.0  # 1 hour max retry backoff

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def calculate_backoff(attempt: int, base: float = BASE_BACKOFF_SECONDS, max_backoff: float = MAX_BACKOFF_SECONDS) -> float:
    """Calculate capped exponential backoff with full random jitter."""
    factor = base ** max(1, attempt)
    jitter = random.uniform(0.5, 1.5)
    return min(max_backoff, factor * jitter)


def is_permanent_email_error(error: Exception | str) -> bool:
    """Determine whether an email delivery error is permanent (do not retry).

    Permanent errors:
    - Invalid recipient email syntax (RFC violation, no @)
    - SMTP permanent 5xx status codes (500-504, 550-554)
    - smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused with 5xx
    - Listmonk authentication, configuration, or not-found errors
    - Listmonk ambiguous read timeouts (delivery uncertain; blind retries stopped to prevent duplicates)
    """
    err_str = str(error).lower()

    if "invalid recipient" in err_str or "malformed email" in err_str:
        return True

    if isinstance(error, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return True

    if isinstance(error, smtplib.SMTPResponseException):
        code = error.smtp_code
        if 500 <= code < 600:
            return True

    # Common permanent SMTP 5xx phrases in error string
    permanent_phrases = (
        "550 user unknown",
        "550 5.1.1",
        "551 user not local",
        "552 exceeded storage",
        "553 invalid mailbox",
        "554 transaction failed",
        "syntax error",
        "recipient address rejected",
    )
    if any(phrase in err_str for phrase in permanent_phrases):
        return True

    # Listmonk provider error classification
    from liteads.common.listmonk_client import (
        ListmonkAmbiguousTimeoutError,
        ListmonkAuthError,
        ListmonkConfigError,
        ListmonkNotFoundError,
    )

    err_obj = error if isinstance(error, Exception) else None
    cause = getattr(err_obj, "__cause__", None)
    for target in (err_obj, cause):
        if target is not None and isinstance(
            target,
            (ListmonkAuthError, ListmonkConfigError, ListmonkNotFoundError, ListmonkAmbiguousTimeoutError),
        ):
            return True

    return False


def sanitize_error(error: Any, max_len: int = 500) -> str:
    """Sanitize error messages by stripping secrets, tokens, and passwords."""
    raw = str(error) if error else "Unknown error"
    # Redact tokens, passwords, authorization headers
    sanitized = re.sub(r"(token|secret|password|auth|key)=['\"]?[^'\";\s]+['\"]?", r"\1=[REDACTED]", raw, flags=re.I)
    # Strip URL query parameters that might carry one-time tokens
    sanitized = re.sub(r"(https?://\S+?\?)\S+", r"\1[PARAMS_REDACTED]", sanitized)
    return sanitized[:max_len]


class NotificationDeliveryWorker:
    """Worker engine for claiming and dispatching durable notification delivery jobs."""

    def __init__(
        self,
        session_factory: Any = None,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id or f"notif-worker-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = lease_seconds

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
    ) -> list[tuple[NotificationDelivery, str]]:
        """Claim a batch of ready or abandoned jobs using FOR UPDATE SKIP LOCKED.

        Selects:
        - Ready jobs: status == 'pending' AND next_attempt_at <= now
        - Abandoned jobs: status == 'processing' AND lease_expires_at < now (worker crash recovery)
        where attempts < max_attempts.

        Returns list of (NotificationDelivery, claim_token) tuples.
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
    ) -> list[tuple[NotificationDelivery, str]]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lease_expires = now + timedelta(seconds=self.lease_seconds)

        stmt = (
            select(NotificationDelivery)
            .where(
                or_(
                    and_(
                        NotificationDelivery.status == "pending",
                        NotificationDelivery.next_attempt_at <= now,
                    ),
                    and_(
                        NotificationDelivery.status == "processing",
                        NotificationDelivery.lease_expires_at < now,
                    ),
                ),
                NotificationDelivery.attempts < NotificationDelivery.max_attempts,
            )
            .order_by(NotificationDelivery.next_attempt_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        result = await session.execute(stmt)
        jobs = list(result.scalars().all())

        claimed = []
        for job in jobs:
            claim_token = uuid.uuid4().hex
            job.status = "processing"
            job.lease_token = claim_token
            job.lease_expires_at = lease_expires
            job.attempts += 1
            claimed.append((job, claim_token))

        await session.commit()
        return claimed

    async def process_job(
        self,
        job: NotificationDelivery,
        claim_token: str,
        session_factory: Any = None,
    ) -> bool:
        """Process a single claimed delivery job outside any database transaction."""
        # 1. Validation check
        if not job.recipient_email or not EMAIL_REGEX.match(job.recipient_email):
            logger.warning(
                "Permanent email rejection: invalid recipient address",
                extra={"delivery_id": job.id, "email": sanitize_error(job.recipient_email)},
            )
            await self._persist_delivery_outcome(
                delivery_id=job.id,
                success=False,
                error_msg=f"Invalid recipient email syntax: {job.recipient_email}",
                is_permanent_err=True,
                claim_token=claim_token,
                session_factory=session_factory,
            )
            return False

        # 2. Perform SMTP delivery OUTSIDE DB transaction
        try:
            await send_email(
                to_address=job.recipient_email,
                subject=job.subject,
                text_content=job.body_text,
                html_content=job.body_html,
            )
            await self._persist_delivery_outcome(
                delivery_id=job.id,
                success=True,
                error_msg=None,
                claim_token=claim_token,
                session_factory=session_factory,
            )
            return True
        except Exception as e:
            is_perm = is_permanent_email_error(e)
            from liteads.common.listmonk_client import ListmonkAmbiguousTimeoutError
            err_obj = e if isinstance(e, Exception) else None
            cause = getattr(err_obj, "__cause__", None)
            is_ambiguous = any(isinstance(t, ListmonkAmbiguousTimeoutError) for t in (err_obj, cause) if t is not None)

            if is_ambiguous:
                logger.warning(
                    "Ambiguous timeout encountered; delivery uncertain - blind retries halted to prevent duplicate sends",
                    extra={"delivery_id": job.id},
                )
                err_msg = f"[AMBIGUOUS_TIMEOUT] {str(e)}"
            else:
                err_msg = str(e)

            logger.error(
                "Email delivery error in worker",
                extra={
                    "delivery_id": job.id,
                    "is_permanent": is_perm,
                    "attempts": job.attempts,
                    "error": sanitize_error(err_msg),
                },
            )
            await self._persist_delivery_outcome(
                delivery_id=job.id,
                success=False,
                error_msg=err_msg,
                is_permanent_err=is_perm,
                claim_token=claim_token,
                session_factory=session_factory,
            )
            return False

    async def _persist_delivery_outcome(
        self,
        delivery_id: int,
        success: bool,
        error_msg: Optional[str] = None,
        is_permanent_err: bool = False,
        claim_token: Optional[str] = None,
        session_factory: Any = None,
    ) -> bool:
        """Persist delivery outcome with database-enforced atomic lease token fencing."""
        token_to_check = claim_token or self.worker_id
        if not token_to_check:
            logger.warning("Outcome persistence rejected: no claim token provided")
            return False

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        factory = self._get_session_factory(session_factory)

        async with factory() as session:
            stmt_select = select(
                NotificationDelivery.attempts,
                NotificationDelivery.max_attempts,
            ).where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.lease_token == token_to_check,
            )
            res_select = await session.execute(stmt_select)
            row = res_select.first()
            if not row:
                logger.warning(
                    "Fenced update aborted: job not found or lease token no longer matches",
                    extra={"delivery_id": delivery_id, "claim_token": token_to_check},
                )
                return False

            attempts, max_attempts = row

            if success:
                new_status = "delivered"
                delivered_at = now
                sanitized_err = None
                next_attempt = now
            else:
                delivered_at = None
                sanitized_err = sanitize_error(error_msg)

                if is_permanent_err or attempts >= max_attempts:
                    new_status = "failed"  # Terminal failure
                    next_attempt = now
                else:
                    new_status = "pending"
                    delay = calculate_backoff(attempts)
                    next_attempt = now + timedelta(seconds=delay)

            # Atomic conditional update requiring matching lease_token
            stmt_update = (
                update(NotificationDelivery)
                .where(
                    NotificationDelivery.id == delivery_id,
                    NotificationDelivery.lease_token == token_to_check,
                )
                .values(
                    status=new_status,
                    last_attempt_at=now,
                    next_attempt_at=next_attempt,
                    delivered_at=delivered_at,
                    last_error=sanitized_err,
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            update_res = await session.execute(stmt_update)
            await session.commit()
            return update_res.rowcount > 0

    async def run_once(
        self,
        session_factory: Any = None,
        batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
    ) -> int:
        """Claim and process one batch of delivery jobs. Returns count of processed jobs."""
        factory = self._get_session_factory(session_factory)
        claimed = await self.claim_jobs(session=None, batch_size=batch_size)
        if not claimed:
            return 0

        for job, token in claimed:
            await self.process_job(job, token, session_factory=factory)

        return len(claimed)

    async def run_loop(
        self,
        session_factory: Any = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Run the persistent polling and delivery loop until stop_event is signaled."""
        logger.info("NotificationDeliveryWorker polling loop started", extra={"worker_id": self.worker_id})
        factory = self._get_session_factory(session_factory)
        loop = asyncio.get_running_loop()
        await publish_worker_heartbeat("notification")
        last_heartbeat = loop.time()

        while stop_event is None or not stop_event.is_set():
            try:
                now = loop.time()
                if now - last_heartbeat >= 30.0:
                    await publish_worker_heartbeat("notification")
                    last_heartbeat = now
                processed = await self.run_once(session_factory=factory)
                if processed == 0:
                    if stop_event:
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                            break
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in notification worker loop", extra={"error": sanitize_error(e)})
                await asyncio.sleep(poll_interval)

        logger.info("NotificationDeliveryWorker polling loop stopped", extra={"worker_id": self.worker_id})


class NotificationWorkerTask:
    """Manages an in-process notification worker background asyncio task."""

    def __init__(self, poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS):
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._worker = NotificationDeliveryWorker()

    def start(self) -> None:
        """Start background worker task if not already running."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("NotificationWorkerTask background runner started")

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
        logger.info("NotificationWorkerTask background runner stopped")

    async def _run(self) -> None:
        await self._worker.run_loop(
            session_factory=db.session_factory,
            poll_interval=self.poll_interval,
            stop_event=self._stop_event,
        )


# Singleton task instance
notification_worker_task = NotificationWorkerTask()


async def main() -> None:
    """CLI entry point for running a standalone dedicated notification worker container."""
    from liteads.common.config import get_settings
    from liteads.common.database import close_db, init_db

    settings = get_settings()
    logger.info("Initializing database and Redis connection for standalone notification worker")
    await init_db()
    await redis_client.connect()

    worker = NotificationDeliveryWorker()
    stop_event = asyncio.Event()

    try:
        await worker.run_loop(
            session_factory=db.session_factory,
            poll_interval=DEFAULT_POLL_INTERVAL_SECONDS,
            stop_event=stop_event,
        )
    finally:
        await redis_client.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
