from fastapi import HTTPException
from liteads.common.cache import redis_client

_LIMITS = {
    "impression": 1,
    "imp": 1,
    "click": 3,
    "clk": 3,
    "conversion": 1,
    "conv": 1,
}


class ReplayReservation:
    """Manages the two-phase lifecycle of a replay-guard reservation."""

    def __init__(self, event_key: str, request_id: str, ad_id: str, limit: int, seq: int = 1):
        self.event_key = event_key
        self.request_id = request_id
        self.ad_id = ad_id
        self.limit = limit
        self.seq = seq
        self.committed_key = f"trk:nonce:{event_key}:{request_id}:{ad_id}"
        self.inflight_key = f"trk:inflight:{event_key}:{request_id}:{ad_id}"
        self.slot_committed_key = f"trk:slot:{event_key}:{request_id}:{ad_id}:{seq}:committed"
        self.slot_inflight_key = f"trk:slot:{event_key}:{request_id}:{ad_id}:{seq}:inflight"
        self._finalized = False
        self._released = False

    async def finalize(self) -> None:
        """Finalize the reservation upon successful database commit."""
        if self._finalized or self._released:
            return
        self._finalized = True
        try:
            # 1. Mark this specific sequence slot committed with 900s TTL
            await redis_client.incr(self.slot_committed_key, 1)
            await redis_client.expire(self.slot_committed_key, 900)  # 15 minutes TTL

            # 2. Increment aggregate committed key with 900s TTL
            count = await redis_client.incr(self.committed_key, 1)
            if count == 1:
                await redis_client.expire(self.committed_key, 900)  # 15 minutes TTL
        finally:
            await self._release_inflight()

    async def release(self) -> None:
        """Release the reservation upon database failure or abort so retries can proceed."""
        if self._finalized or self._released:
            return
        self._released = True
        await self._release_inflight()

    async def _release_inflight(self) -> None:
        """Decrement in-flight reservation counters and clean up."""
        try:
            rem_slot = await redis_client.incr(self.slot_inflight_key, -1)
            if rem_slot <= 0 and hasattr(redis_client, "delete"):
                await redis_client.delete(self.slot_inflight_key)

            rem_agg = await redis_client.incr(self.inflight_key, -1)
            if rem_agg <= 0 and hasattr(redis_client, "delete"):
                await redis_client.delete(self.inflight_key)
        except Exception:
            pass


async def acquire_replay_guard(event_type: str, request_id: str, ad_id: str) -> ReplayReservation:
    """
    Acquire an in-flight reservation before database persistence.

    Iterates through candidate slots 1..limit to find an uncommitted, unreserved slot.
    Guarantees concurrency safety: concurrent clicks take distinct sequence numbers.
    Raises HTTPException 429 if all slots are either committed or in-flight.
    """
    event_key = event_type.lower()
    limit = _LIMITS.get(event_key, 1)

    aggregate_committed_key = f"trk:nonce:{event_key}:{request_id}:{ad_id}"
    aggregate_inflight_key = f"trk:inflight:{event_key}:{request_id}:{ad_id}"

    # 1. Fast check on aggregate committed count
    committed_total = await redis_client.incr(aggregate_committed_key, 0)
    if committed_total >= limit:
        raise HTTPException(status_code=429, detail="Duplicate event rejected")

    # 2. Iterate through sequence slots 1..limit to find an uncommitted and free slot
    for s in range(1, limit + 1):
        slot_committed_key = f"trk:slot:{event_key}:{request_id}:{ad_id}:{s}:committed"
        slot_inflight_key = f"trk:slot:{event_key}:{request_id}:{ad_id}:{s}:inflight"

        # Check if this specific slot is already committed
        slot_committed = await redis_client.incr(slot_committed_key, 0)
        if slot_committed > 0:
            continue

        # Try to acquire exclusive in-flight lease on slot s
        inflight = await redis_client.incr(slot_inflight_key, 1)
        if inflight == 0:
            # Redis is disconnected / degraded mode; fall through to database idempotency
            return ReplayReservation(event_key, request_id, ad_id, limit, seq=1)

        if inflight == 1:
            # Successfully took in-flight lease
            await redis_client.expire(slot_inflight_key, 30)  # 30-second lease

            # Re-check committed status in case of concurrent finalize
            slot_committed = await redis_client.incr(slot_committed_key, 0)
            if slot_committed > 0:
                await redis_client.incr(slot_inflight_key, -1)
                continue

            # Increment aggregate in-flight counter
            agg_inf = await redis_client.incr(aggregate_inflight_key, 1)
            if agg_inf == 1:
                await redis_client.expire(aggregate_inflight_key, 30)

            # Check aggregate limit against total in-flight + committed
            committed_now = await redis_client.incr(aggregate_committed_key, 0)
            if committed_now + agg_inf > limit:
                await redis_client.incr(slot_inflight_key, -1)
                await redis_client.incr(aggregate_inflight_key, -1)
                raise HTTPException(status_code=429, detail="Duplicate event rejected")

            return ReplayReservation(event_key, request_id, ad_id, limit, seq=s)
        else:
            # Slot is already held in-flight by another concurrent request; decrement and try next
            await redis_client.incr(slot_inflight_key, -1)
            continue

    # All slots exhausted
    raise HTTPException(status_code=429, detail="Duplicate event rejected")


async def enforce_replay_guard(event_type: str, request_id: str, ad_id: str) -> None:
    """
    Legacy replay guard for direct one-step enforcement.
    Raises HTTPException 429 if the limit is exceeded.
    """
    reservation = await acquire_replay_guard(event_type, request_id, ad_id)
    await reservation.finalize()
