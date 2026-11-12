"""Weekly Publisher Placement Digest Scheduler (Phase 6 WU5).

Provides a production-safe background scheduler task for generating and delivering
weekly placement performance digests to publishers.
"""

import asyncio
from datetime import date, datetime, timezone
import os
from typing import Any, Optional
import uuid

from liteads.ad_server.services.publisher_digest_service import (
    BATCH_STATUS_FAILURE,
    BATCH_STATUS_NO_OP,
    BATCH_STATUS_PARTIAL,
    BATCH_STATUS_SUCCESS,
    DIGEST_DELIVERED_KEY_PREFIX,
    PublisherDigestService,
)
from liteads.common.cache import redis_client
from liteads.common.database import db
from liteads.common.logger import get_logger

logger = get_logger(__name__)

# Default cadence: 7 days in seconds
DEFAULT_DIGEST_INTERVAL_SECONDS = 7 * 86400  # 604,800 seconds
DEFAULT_RETRY_INTERVAL_SECONDS = 3600  # 1 hour retry interval for incomplete cycles
DEFAULT_MAX_RETRIES = 2  # Bounded immediate retries within cycle
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0  # Seconds to wait between immediate retries
DISTRIBUTED_LOCK_KEY = "lock:weekly_placement_digest_cycle"
LAST_SUCCESSFUL_CYCLE_KEY = "digest:last_successful_cycle_timestamp"
ACTIVE_CYCLE_KEY = "digest:active_cycle_id"
ACTIVE_CYCLE_TARGET_DATE_KEY = "digest:active_cycle_target_date"
DISTRIBUTED_LOCK_TTL = 3600  # 1 hour lock lease TTL
LOCK_RENEWAL_INTERVAL = 300  # 5 minutes renewal heartbeat
COMPLETED_CYCLE_RETENTION_TTL = 14 * 86400  # 14 days post-completion retention


class WeeklyDigestTask:
    """
    Background scheduler task for weekly publisher placement performance digests.
    
    Adheres to the project's background-task lifecycle conventions:
    - Idempotent start() preventing duplicate tasks.
    - Clean async stop() with cancellation handling.
    - Ownership-safe Redis distributed locking with unique owner tokens and Lua release.
    - Ownership-checked lease renewal for long-running batches.
    - Timestamp-based restart safety preventing premature sends after server reboots.
    - Fail-closed degraded mode preventing multi-worker duplicate fan-out when Redis is down.
    - Safe partial-failure retry semantics with idempotent per-publisher delivery tracking.
    - Per-publisher exception isolation.
    - Safe, sanitized operational logging.
    """

    def __init__(
        self,
        interval_seconds: Optional[int] = None,
        retry_interval_seconds: Optional[int] = None,
        max_retries: Optional[int] = None,
        retry_backoff_seconds: Optional[float] = None,
    ):
        env_interval = os.getenv("LITEADS_WEEKLY_DIGEST_INTERVAL_SECONDS")
        if interval_seconds is not None:
            self.interval_seconds = interval_seconds
        elif env_interval and env_interval.isdigit():
            self.interval_seconds = int(env_interval)
        else:
            self.interval_seconds = DEFAULT_DIGEST_INTERVAL_SECONDS

        env_retry_interval = os.getenv("LITEADS_DIGEST_RETRY_INTERVAL_SECONDS")
        if retry_interval_seconds is not None:
            self.retry_interval_seconds = retry_interval_seconds
        elif env_retry_interval and env_retry_interval.isdigit():
            self.retry_interval_seconds = int(env_retry_interval)
        else:
            self.retry_interval_seconds = DEFAULT_RETRY_INTERVAL_SECONDS

        env_max_retries = os.getenv("LITEADS_DIGEST_MAX_RETRIES")
        if max_retries is not None:
            self.max_retries = max_retries
        elif env_max_retries and env_max_retries.isdigit():
            self.max_retries = int(env_max_retries)
        else:
            self.max_retries = DEFAULT_MAX_RETRIES

        env_backoff = os.getenv("LITEADS_DIGEST_RETRY_BACKOFF_SECONDS")
        if retry_backoff_seconds is not None:
            self.retry_backoff_seconds = retry_backoff_seconds
        elif env_backoff:
            try:
                self.retry_backoff_seconds = float(env_backoff)
            except ValueError:
                self.retry_backoff_seconds = DEFAULT_RETRY_BACKOFF_SECONDS
        elif "PYTEST_CURRENT_TEST" in os.environ:
            self.retry_backoff_seconds = 0.0
        else:
            self.retry_backoff_seconds = DEFAULT_RETRY_BACKOFF_SECONDS

        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        """Returns True if the scheduler task is currently active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the weekly digest background loop. Prevents duplicate tasks."""
        if self.is_running:
            logger.warning("Weekly digest loop already running, ignoring start request.")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Weekly placement digest loop started",
            interval_seconds=self.interval_seconds,
        )

    async def stop(self) -> None:
        """Cleanly stop the background task."""
        if not self.is_running:
            return

        logger.info("Stopping weekly placement digest loop")
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Weekly placement digest loop stopped")

    async def get_remaining_interval(self) -> float:
        """
        Check the persisted last successful cycle timestamp in Redis.
        Returns the remaining seconds to wait if a cycle occurred recently,
        or 0.0 if a cycle is due now or if timestamp is absent/unreachable.
        """
        if not redis_client.connected:
            return 0.0

        try:
            val = await redis_client.get(LAST_SUCCESSFUL_CYCLE_KEY)
            if not val:
                return 0.0

            last_ts = float(val)
            now_ts = datetime.now(timezone.utc).timestamp()
            elapsed = now_ts - last_ts

            if 0 <= elapsed < self.interval_seconds:
                return float(self.interval_seconds - elapsed)
            return 0.0
        except Exception as e:
            logger.warning(
                "Failed to read last successful digest cycle timestamp from Redis",
                error=str(e),
            )
            return 0.0

    async def record_successful_cycle(self) -> bool:
        """Persist the current UTC timestamp as the last successful cycle."""
        if not redis_client.connected:
            return False

        try:
            now_ts = str(datetime.now(timezone.utc).timestamp())
            return await redis_client.set(
                LAST_SUCCESSFUL_CYCLE_KEY,
                now_ts,
                ttl=self.interval_seconds * 2,  # Retain for at least 2 cycles
            )
        except Exception as e:
            logger.warning("Failed to record successful digest cycle timestamp", error=str(e))
            return False

    async def get_or_create_active_cycle(
        self, as_of_date: Optional[date] = None
    ) -> tuple[str, date]:
        """
        Retrieve existing active cycle identity and target date, or initialize a new one.

        Preserves cycle identity across calendar date changes (e.g., retries crossing midnight),
        hourly retries, and scheduler process restarts until the batch completes successfully.
        """
        if not redis_client.connected:
            target_date = as_of_date or datetime.now(timezone.utc).date()
            cycle_id = f"cycle_{target_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
            return cycle_id, target_date

        try:
            existing_id = await redis_client.get(ACTIVE_CYCLE_KEY)
            existing_date_str = await redis_client.get(ACTIVE_CYCLE_TARGET_DATE_KEY)

            if existing_id and existing_date_str:
                try:
                    active_date = date.fromisoformat(existing_date_str)
                    return str(existing_id), active_date
                except ValueError:
                    pass

            target_date = as_of_date or datetime.now(timezone.utc).date()
            cycle_id = f"cycle_{target_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

            # Persist without TTL so records do not expire while incomplete
            await redis_client.set(ACTIVE_CYCLE_KEY, cycle_id)
            await redis_client.set(ACTIVE_CYCLE_TARGET_DATE_KEY, target_date.isoformat())
            return cycle_id, target_date

        except Exception as e:
            logger.warning("Error accessing active cycle in Redis, falling back to ephemeral cycle", error=str(e))
            target_date = as_of_date or datetime.now(timezone.utc).date()
            cycle_id = f"cycle_{target_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
            return cycle_id, target_date

    async def complete_active_cycle(self, cycle_id: str) -> bool:
        """
        Atomically finalize a successfully completed digest cycle via Redis Lua script:
        1. Verifies that active_cycle_key currently equals cycle_id.
        2. Applies retention TTL to the delivered ledger key (14 days).
        3. Records the successful cycle timestamp (with interval * 2 TTL).
        4. Removes the active cycle identity keys (ACTIVE_CYCLE_KEY and ACTIVE_CYCLE_TARGET_DATE_KEY).

        Returns True on successful atomic finalization, False if cycle ID mismatched or Redis unavailable.
        """
        if not redis_client.connected:
            return False

        try:
            delivered_key = f"{DIGEST_DELIVERED_KEY_PREFIX}{cycle_id}"
            now_ts = str(datetime.now(timezone.utc).timestamp())
            timestamp_ttl = self.interval_seconds * 2

            success = await redis_client.finalize_digest_cycle(
                active_cycle_key=ACTIVE_CYCLE_KEY,
                active_target_date_key=ACTIVE_CYCLE_TARGET_DATE_KEY,
                last_successful_cycle_key=LAST_SUCCESSFUL_CYCLE_KEY,
                delivered_key=delivered_key,
                expected_cycle_id=cycle_id,
                timestamp_str=now_ts,
                timestamp_ttl=timestamp_ttl,
                retention_ttl=COMPLETED_CYCLE_RETENTION_TTL,
            )
            if success:
                logger.info(
                    "Active digest cycle atomically finalized; recorded success timestamp and applied retention TTL",
                    cycle_id=cycle_id,
                )
            else:
                logger.warning(
                    "Digest cycle finalization refused by Redis: expected cycle ID did not match active cycle",
                    cycle_id=cycle_id,
                )
            return success
        except Exception as e:
            logger.warning("Error atomically completing active digest cycle in Redis", cycle_id=cycle_id, error=str(e))
            return False

    async def _run_loop(self) -> None:
        """Main loop executing digest cycles periodically with restart-delay awareness."""
        # Check if a cycle ran recently before this process started
        try:
            initial_delay = await self.get_remaining_interval()
            if initial_delay > 0:
                logger.info(
                    "Recent digest cycle detected; waiting for remaining interval before first run",
                    remaining_seconds=round(initial_delay, 1),
                )
                try:
                    await asyncio.sleep(initial_delay)
                except asyncio.CancelledError:
                    return
        except Exception as e:
            logger.warning("Error computing initial digest delay; proceeding with loop", error=str(e))

        while not self._stop_event.is_set():
            try:
                summary = await self._process_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in weekly digest cycle", error=str(e))
                summary = {"status": BATCH_STATUS_FAILURE, "error": str(e)}

            # Determine sleep duration until next attempt
            if isinstance(summary, dict) and summary.get("status") in (BATCH_STATUS_PARTIAL, BATCH_STATUS_FAILURE):
                sleep_duration = self.retry_interval_seconds
                logger.info(
                    "Digest cycle had incomplete delivery; scheduling retry",
                    retry_interval_seconds=sleep_duration,
                    status=summary.get("status"),
                )
            elif isinstance(summary, dict) and summary.get("skipped_due_to_uncoordinated_degraded_mode"):
                sleep_duration = self.retry_interval_seconds
            else:
                sleep_duration = self.interval_seconds

            try:
                await asyncio.sleep(sleep_duration)
            except asyncio.CancelledError:
                break

    async def _process_cycle(self, as_of_date: Optional[datetime] = None) -> dict[str, Any]:
        """
        Execute a single digest cycle across all eligible publishers.
        
        Safety rules:
        - Fails closed in multi-worker environments when Redis is unavailable to prevent duplicate sends.
        - Uses unique owner tokens and Lua-based atomic lock release.
        - Employs lease renewal heartbeat for long batches.
        - Idempotently skips publishers already delivered during the current cycle.
        - Retries failed publishers with bounded in-cycle backoff.
        - Updates the weekly successful run timestamp ONLY on verified complete delivery success.
        """
        logger.info("Starting weekly placement digest cycle execution")

        # 1. Multi-worker Redis coordination guard
        if not redis_client.connected:
            allow_degraded = os.getenv("LITEADS_ENABLE_DIGEST_SCHEDULER_WITHOUT_REDIS", "false").lower() in ("true", "1")
            if not allow_degraded:
                logger.warning(
                    "Weekly placement digest cycle skipped: Redis is unavailable and distributed locking "
                    "cannot be guaranteed across workers. To permit execution in single-worker degraded mode, "
                    "set LITEADS_ENABLE_DIGEST_SCHEDULER_WITHOUT_REDIS=true."
                )
                return {
                    "total": 0,
                    "sent": 0,
                    "failed_or_skipped": 0,
                    "status": BATCH_STATUS_FAILURE,
                    "skipped_due_to_uncoordinated_degraded_mode": 1,
                }
            logger.info("Proceeding with digest cycle in explicitly permitted single-worker degraded mode")

        # 2. Acquire ownership-safe distributed lock
        owner_token = str(uuid.uuid4())
        lock_acquired = True

        if redis_client.connected:
            lock_acquired = await redis_client.set(
                DISTRIBUTED_LOCK_KEY,
                owner_token,
                ttl=min(self.interval_seconds, DISTRIBUTED_LOCK_TTL),
                nx=True,
            )
            if not lock_acquired:
                logger.info(
                    "Weekly placement digest cycle skipped: lock already held by another worker",
                    lock_key=DISTRIBUTED_LOCK_KEY,
                )
                return {
                    "total": 0,
                    "sent": 0,
                    "failed_or_skipped": 0,
                    "status": BATCH_STATUS_NO_OP,
                    "skipped_due_to_lock": 1,
                }

        # 3. Start renewal heartbeat for long batches
        stop_heartbeat = asyncio.Event()

        async def _heartbeat_loop():
            while not stop_heartbeat.is_set():
                try:
                    await asyncio.sleep(LOCK_RENEWAL_INTERVAL)
                except asyncio.CancelledError:
                    break
                if stop_heartbeat.is_set():
                    break
                renewed = await redis_client.renew_lock(
                    DISTRIBUTED_LOCK_KEY, owner_token, ttl=DISTRIBUTED_LOCK_TTL
                )
                if not renewed:
                    logger.warning("Failed to renew digest lock lease; lock may have expired or been reassigned")
                    break

        heartbeat_task = None
        if redis_client.connected:
            heartbeat_task = asyncio.create_task(_heartbeat_loop())

        try:
            target_date_input = as_of_date.date() if isinstance(as_of_date, datetime) else as_of_date
            cycle_id, target_date = await self.get_or_create_active_cycle(as_of_date=target_date_input)

            async with db.session() as session:
                service = PublisherDigestService(session)
                summary = await service.send_all_digests(
                    as_of_date=target_date,
                    skip_already_delivered=True,
                    cycle_id=cycle_id,
                )

            # In-cycle bounded immediate retries for failed publishers
            failed_ids = summary.get("failed_publisher_ids", [])
            retries_remaining = self.max_retries

            while failed_ids and retries_remaining > 0:
                retries_remaining -= 1
                logger.info(
                    "Retrying failed publisher digests within cycle",
                    remaining_retries=retries_remaining,
                    failed_count=len(failed_ids),
                    cycle_id=cycle_id,
                )
                if self.retry_backoff_seconds > 0:
                    try:
                        await asyncio.sleep(self.retry_backoff_seconds)
                    except asyncio.CancelledError:
                        break

                async with db.session() as retry_session:
                    retry_service = PublisherDigestService(retry_session)
                    summary = await retry_service.send_all_digests(
                        as_of_date=target_date,
                        skip_already_delivered=True,
                        cycle_id=cycle_id,
                    )
                failed_ids = summary.get("failed_publisher_ids", [])

            # Define cycle success semantics:
            # The weekly cycle timestamp is advanced ONLY on full delivery success (or no-op).
            # If any publishers failed (partial_success or failure), timestamp is NOT updated,
            # allowing prompt retry without delaying failed publishers for a full week.
            status = summary.get("status")
            total = summary.get("total", 0)
            sent = summary.get("sent", 0)
            failed_or_skipped = summary.get("failed_or_skipped", 0)

            is_complete_success = (
                status == BATCH_STATUS_SUCCESS
                or (status == BATCH_STATUS_NO_OP or total == 0)
                or (total > 0 and sent == total and failed_or_skipped == 0)
            )

            if is_complete_success:
                finalized = await self.complete_active_cycle(cycle_id)
                logger.info(
                    "Weekly placement digest cycle completed with full success; recorded cycle timestamp and finalized active cycle atomically",
                    total=total,
                    sent=sent,
                    status=status,
                    cycle_id=cycle_id,
                    finalized=finalized,
                )
            else:
                logger.warning(
                    "Weekly placement digest cycle finished with incomplete delivery; "
                    "retaining active cycle and not advancing weekly cycle timestamp so retry will occur",
                    total=total,
                    sent=sent,
                    failed_or_skipped=failed_or_skipped,
                    status=status,
                    cycle_id=cycle_id,
                )

            return summary

        except Exception as e:
            logger.error("Database or service failure during weekly digest cycle", error=str(e))
            return {
                "total": 0,
                "sent": 0,
                "failed_or_skipped": 0,
                "status": BATCH_STATUS_FAILURE,
                "error": str(e),
            }

        finally:
            # Stop lease renewal heartbeat
            if heartbeat_task is not None:
                stop_heartbeat.set()
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            # Ownership-safe release: deletes lock only if owner_token still matches
            if redis_client.connected:
                await redis_client.release_lock(DISTRIBUTED_LOCK_KEY, owner_token)


# Singleton instance
weekly_digest_loop = WeeklyDigestTask()


async def main() -> None:
    """CLI entry point for running a standalone dedicated weekly digest worker container."""
    from liteads.common.config import get_settings
    from liteads.common.database import init_db, close_db

    settings = get_settings()
    logger.info("Initializing database and redis connection for standalone weekly digest worker")
    await init_db()
    await redis_client.connect()

    worker = WeeklyDigestTask()
    try:
        await worker._run_loop()
    finally:
        await redis_client.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

