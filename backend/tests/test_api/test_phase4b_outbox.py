"""Comprehensive test suite for Phase 4B: Durable Webhook Outbox.

Covers:
1. Outbox job creation and transactional atomic commit.
2. Transactional rollback ensuring no phantom jobs.
3. Enqueue idempotency via unique logical event key.
4. Worker claiming with lease tokens.
5. Lease expiry and recovery of abandoned jobs.
6. Successful delivery and state transition to 'delivered'.
7. Transient failure with exponential backoff and jitter.
8. Permanent failure (SSRF rejection and 4xx status) dead-lettering.
9. Max-attempts boundary resulting in terminal 'failed' state.
10. Stale worker concurrency protection (lease token mismatch rejection).
11. API delivery visibility endpoints with tenant isolation.
12. Manual retry endpoint resetting delivery state.
13. Strict Phase 4A SSRF enforcement through outbox worker path.
14. End-to-end event tracking integration.
"""

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from liteads.ad_server.services.webhook_dispatcher import build_event_payload, dispatch_webhooks
from liteads.ad_server.services.webhook_worker import WebhookOutboxWorker
from liteads.models.auth import User
from liteads.models.webhook import Webhook, WebhookDelivery

pytestmark = pytest.mark.asyncio


async def _create_test_advertiser_and_webhook(
    client: AsyncClient,
    test_db: AsyncSession,
    email: str = "outbox_adv@example.com",
    url: str = "https://example.com/webhook",
    events: list[str] | None = None,
) -> tuple[dict, Webhook, str]:
    """Helper to register an advertiser and create a webhook via API."""
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Outbox Adv",
            "email": email,
            "password": "Password123!",
            "role": "advertiser",
        },
    )
    assert res.status_code == 201
    auth_data = res.json()
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await client.post(
        "/api/v1/webhooks",
        json={"url": url, "events": events or ["impression", "conversion"]},
        headers=headers,
    )
    assert create_res.status_code == 201
    wh_data = create_res.json()
    wh = await test_db.get(Webhook, wh_data["id"])
    assert wh is not None
    return auth_data, wh, token


# ── 1. Outbox Job Creation & Transaction Safety ──────────────────────────────

async def test_outbox_job_creation_and_atomic_commit(
    client: AsyncClient, test_db: AsyncSession
):
    """Event tracking writes WebhookDelivery jobs in 'pending' state."""
    _, wh, _ = await _create_test_advertiser_and_webhook(client, test_db)

    payload = build_event_payload(
        event_type="impression",
        request_id="req-test-123",
        ad_id="ad-1",
        campaign_id=1,
        creative_id=1,
        user_id="user-1",
        cost=0.05,
    )

    deliveries = await dispatch_webhooks(
        session=test_db,
        event_type="impression",
        payload=payload,
        advertiser_id=wh.owner_id,
    )
    await test_db.commit()

    assert len(deliveries) == 1
    job = deliveries[0]
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.max_attempts == 5
    assert job.event_type == "impression"
    assert job.event_id == "req-test-123"
    assert job.lease_token is None

    # Verify directly from DB
    persisted = await test_db.get(WebhookDelivery, job.id)
    assert persisted is not None
    assert persisted.status == "pending"
    assert json.loads(persisted.payload)["event"] == "impression"


async def test_outbox_job_rollback_on_transaction_failure(
    client: AsyncClient, test_db: AsyncSession
):
    """If the transaction rolls back, no outbox job is created."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_rb@example.com"
    )

    payload = build_event_payload(
        event_type="conversion",
        request_id="req-rollback-456",
        ad_id="ad-2",
        campaign_id=1,
        creative_id=1,
        user_id="user-2",
        cost=1.0,
    )

    await dispatch_webhooks(
        session=test_db,
        event_type="conversion",
        payload=payload,
        advertiser_id=wh.owner_id,
    )
    # Rollback session before commit
    await test_db.rollback()

    stmt = select(WebhookDelivery).where(WebhookDelivery.event_id == "req-rollback-456")
    res = await test_db.execute(stmt)
    assert res.scalar_one_or_none() is None


# ── 2. Idempotency & Deduplication ──────────────────────────────────────────

async def test_idempotency_prevents_duplicate_enqueue(
    client: AsyncClient, test_db: AsyncSession
):
    """Calling dispatch_webhooks multiple times for same event produces only 1 job."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_idem@example.com"
    )

    payload = build_event_payload(
        event_type="impression",
        request_id="req-idem-789",
        ad_id="ad-3",
        campaign_id=1,
        creative_id=1,
        user_id="user-3",
        cost=0.02,
    )

    d1 = await dispatch_webhooks(
        session=test_db,
        event_type="impression",
        payload=payload,
        advertiser_id=wh.owner_id,
    )
    await test_db.commit()
    assert len(d1) == 1

    d2 = await dispatch_webhooks(
        session=test_db,
        event_type="impression",
        payload=payload,
        advertiser_id=wh.owner_id,
    )
    await test_db.commit()
    assert len(d2) == 0

    stmt = select(WebhookDelivery).where(
        WebhookDelivery.webhook_id == wh.id,
        WebhookDelivery.event_id == "req-idem-789",
    )
    res = await test_db.execute(stmt)
    all_jobs = res.scalars().all()
    assert len(all_jobs) == 1


# ── 3. Worker Claiming & Concurrency ────────────────────────────────────────

async def test_worker_claiming_assigns_lease(
    client: AsyncClient, test_db: AsyncSession
):
    """Worker claims pending jobs and marks them 'processing' with a lease token."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_claim@example.com"
    )

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="claim-1",
        event_type="impression",
        payload="{}",
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(
        session_factory=session_factory, worker_id="worker-test-1", lease_duration_seconds=30
    )

    claimed = await worker.claim_jobs()
    assert len(claimed) == 1
    job_id = claimed[0].id

    # Verify DB state of claimed job
    job = await test_db.get(WebhookDelivery, job_id)
    await test_db.refresh(job)
    assert job.status == "processing"
    assert job.lease_token is not None
    assert job.lease_token != "worker-test-1"  # Claim-specific unpredictable token, not worker_id
    assert len(job.lease_token) >= 16
    assert job.lease_token == claimed[0].lease_token
    assert job.lease_expires_at is not None
    exp1 = job.lease_expires_at.replace(tzinfo=timezone.utc) if job.lease_expires_at.tzinfo is None else job.lease_expires_at
    assert exp1 > datetime.now(timezone.utc)


async def test_abandoned_job_recovery_on_lease_expiry(
    client: AsyncClient, test_db: AsyncSession
):
    """An abandoned job with expired lease is claimed by another worker."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_recover@example.com"
    )

    # Job previously claimed by a dead worker whose lease expired 5 minutes ago
    expired_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="abandoned-1",
        event_type="conversion",
        payload="{}",
        status="processing",
        attempts=1,
        max_attempts=5,
        lease_token="dead-worker-999",
        lease_expires_at=expired_time,
        next_attempt_at=expired_time,
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker2 = WebhookOutboxWorker(
        session_factory=session_factory, worker_id="alive-worker-2", lease_duration_seconds=60
    )

    claimed = await worker2.claim_jobs()
    assert len(claimed) >= 1
    reclaimed_ids = [c.id for c in claimed]
    assert delivery.id in reclaimed_ids

    job = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(job)
    assert job.status == "processing"
    assert job.lease_token is not None
    assert job.lease_token != "dead-worker-999"
    assert job.lease_token != "alive-worker-2"  # Claim-specific unpredictable token, not worker_id
    assert job.lease_token == claimed[0].lease_token
    exp2 = job.lease_expires_at.replace(tzinfo=timezone.utc) if job.lease_expires_at.tzinfo is None else job.lease_expires_at
    assert exp2 > datetime.now(timezone.utc)


async def test_stale_worker_cannot_overwrite_newer_worker_result(
    client: AsyncClient, test_db: AsyncSession
):
    """If a worker's lease expires and another claims it, the stale worker's write is rejected."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_stale@example.com"
    )

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="stale-test",
        event_type="impression",
        payload="{}",
        status="processing",
        attempts=1,
        max_attempts=5,
        lease_token="worker-2-new",  # currently leased to worker 2
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        next_attempt_at=datetime.now(timezone.utc),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    # Stale worker 1 tries to record outcome
    stale_worker = WebhookOutboxWorker(
        session_factory=session_factory, worker_id="worker-1-stale"
    )

    success = await stale_worker._persist_delivery_outcome(
        delivery_id=delivery.id,
        success=True,
        http_status=200,
        error_msg=None,
        is_permanent_err=False,
    )
    assert success is False

    # Verify DB status was NOT changed to delivered by the stale worker
    fresh = await test_db.get(WebhookDelivery, delivery.id)
    assert fresh.status == "processing"
    assert fresh.lease_token == "worker-2-new"


# ── 4. Delivery Outcomes, Retries & Dead Lettering ───────────────────────────

async def test_successful_delivery_persists_delivered_state(
    client: AsyncClient, test_db: AsyncSession
):
    """Successful outbound delivery transitions job to 'delivered' and updates stats."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_succ@example.com"
    )

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="succ-1",
        event_type="impression",
        payload='{"event":"impression","cost":0.01}',
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory, worker_id="worker-succ")

    with patch("liteads.ad_server.services.webhook_worker._deliver", new_callable=AsyncMock) as mock_deliv:
        mock_deliv.return_value = (True, 200, None)
        processed = await worker.process_batch()
        assert processed >= 1

    job = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(job)
    assert job.status == "delivered"
    assert job.attempts == 1
    assert job.last_http_status == 200
    assert job.last_error is None
    assert job.delivered_at is not None
    assert job.lease_token is None

    # Webhook delivery counter updated
    wh_updated = await test_db.get(Webhook, wh.id)
    await test_db.refresh(wh_updated)
    assert wh_updated.deliveries_ok == 1


async def test_retryable_failure_schedules_backoff(
    client: AsyncClient, test_db: AsyncSession
):
    """Transient HTTP 500 schedules exponential backoff and increments attempts."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_retry@example.com"
    )

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="retry-1",
        event_type="click",
        payload='{"event":"click"}',
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory, worker_id="worker-retry")

    with patch("liteads.ad_server.services.webhook_worker._deliver", new_callable=AsyncMock) as mock_deliv:
        mock_deliv.return_value = (False, 500, "HTTP 500")
        processed = await worker.process_batch()
        assert processed >= 1

    job = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 1
    assert job.last_http_status == 500
    assert "HTTP 500" in job.last_error
    next_att = job.next_attempt_at.replace(tzinfo=timezone.utc) if job.next_attempt_at.tzinfo is None else job.next_attempt_at
    assert next_att > datetime.now(timezone.utc) - timedelta(seconds=1)
    assert job.lease_token is None


async def test_permanent_ssrf_rejection_fails_immediately_without_retry(
    client: AsyncClient, test_db: AsyncSession
):
    """SSRF rejection transitions immediately to terminal 'failed' state without retry."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_ssrf@example.com"
    )

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="ssrf-1",
        event_type="conversion",
        payload='{"event":"conversion"}',
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory, worker_id="worker-ssrf")

    with patch("liteads.ad_server.services.webhook_worker._deliver", new_callable=AsyncMock) as mock_deliv:
        mock_deliv.return_value = (False, None, "Destination rejected: prohibited address")
        await worker.process_batch()

    job = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1
    assert "Destination rejected" in job.last_error

    wh_updated = await test_db.get(Webhook, wh.id)
    await test_db.refresh(wh_updated)
    assert wh_updated.deliveries_failed == 1


async def test_permanent_4xx_dead_lettering(
    client: AsyncClient, test_db: AsyncSession
):
    """HTTP 404 (non-retryable client error) is dead-lettered to 'failed' immediately."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_404@example.com"
    )

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="dead-404",
        event_type="impression",
        payload='{"event":"impression"}',
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory, worker_id="worker-404")

    with patch("liteads.ad_server.services.webhook_worker._deliver", new_callable=AsyncMock) as mock_deliv:
        mock_deliv.return_value = (False, 404, "HTTP 404")
        await worker.process_batch()

    job = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1
    assert job.last_http_status == 404


async def test_max_attempts_exceeded_fails_permanently(
    client: AsyncClient, test_db: AsyncSession
):
    """When attempt reaches max_attempts, job transitions permanently to 'failed'."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_max@example.com"
    )

    # Job already at 4 attempts with max 5
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="max-attempts",
        event_type="impression",
        payload='{"event":"impression"}',
        status="pending",
        attempts=4,
        max_attempts=5,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory, worker_id="worker-max")

    with patch("liteads.ad_server.services.webhook_worker._deliver", new_callable=AsyncMock) as mock_deliv:
        mock_deliv.return_value = (False, 503, "HTTP 503")
        await worker.process_batch()

    job = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 5
    assert job.last_http_status == 503


# ── 5. Delivery Visibility API & Tenant Isolation ────────────────────────────

async def test_delivery_visibility_and_tenant_isolation(
    client: AsyncClient, test_db: AsyncSession
):
    """Only the owner can view delivery jobs; cross-tenant access is forbidden."""
    _, wh1, token1 = await _create_test_advertiser_and_webhook(
        client, test_db, email="owner1@example.com"
    )
    _, wh2, token2 = await _create_test_advertiser_and_webhook(
        client, test_db, email="owner2@example.com"
    )

    # Create delivery for webhook 1
    delivery1 = WebhookDelivery(
        webhook_id=wh1.id,
        owner_type=wh1.owner_type,
        owner_id=wh1.owner_id,
        event_id="deliv-vis-1",
        event_type="impression",
        payload='{"event":"impression","secret_not_here":true}',
        status="delivered",
        attempts=1,
        max_attempts=5,
        delivered_at=datetime.now(timezone.utc),
    )
    test_db.add(delivery1)
    await test_db.commit()

    h1 = {"Authorization": f"Bearer {token1}"}
    h2 = {"Authorization": f"Bearer {token2}"}

    # Owner 1 lists deliveries -> sees 1 delivery
    res = await client.get(f"/api/v1/webhooks/{wh1.id}/deliveries", headers=h1)
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["id"] == delivery1.id
    assert items[0]["status"] == "delivered"

    # Owner 1 inspects detail -> gets payload snapshot
    detail_res = await client.get(
        f"/api/v1/webhooks/{wh1.id}/deliveries/{delivery1.id}", headers=h1
    )
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert "secret_not_here" in detail["payload"]

    # Owner 2 attempts to list Owner 1's deliveries -> 404
    res_cross = await client.get(f"/api/v1/webhooks/{wh1.id}/deliveries", headers=h2)
    assert res_cross.status_code == 404

    # Owner 2 attempts to inspect Owner 1's delivery detail -> 404
    res_cross_detail = await client.get(
        f"/api/v1/webhooks/{wh1.id}/deliveries/{delivery1.id}", headers=h2
    )
    assert res_cross_detail.status_code == 404


async def test_manual_retry_resets_delivery_job(
    client: AsyncClient, test_db: AsyncSession
):
    """POST /webhooks/{id}/deliveries/{did}/retry resets failed job to pending."""
    _, wh, token = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_retry_endpoint@example.com"
    )

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="failed-retry-test",
        event_type="conversion",
        payload="{}",
        status="failed",
        attempts=5,
        max_attempts=5,
        last_error="Destination timeout",
    )
    test_db.add(delivery)
    await test_db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    retry_res = await client.post(
        f"/api/v1/webhooks/{wh.id}/deliveries/{delivery.id}/retry",
        headers=headers,
    )
    assert retry_res.status_code == 200
    data = retry_res.json()
    assert data["status"] == "pending"
    assert data["attempts"] == 0
    assert data["last_error"] is None

    # Verify DB state
    updated = await test_db.get(WebhookDelivery, delivery.id)
    assert updated.status == "pending"
    assert updated.attempts == 0
    next_att = updated.next_attempt_at
    if next_att.tzinfo is None:
        next_att = next_att.replace(tzinfo=timezone.utc)
    assert next_att <= datetime.now(timezone.utc) + timedelta(seconds=5)


# ── 6. SSRF Protection Preservation in Outbox ───────────────────────────────

async def test_ssrf_protection_preserved_in_outbox_worker(
    client: AsyncClient, test_db: AsyncSession
):
    """Worker delivery goes through _deliver, strictly blocking private IPs and loopbacks."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_ssrf_strict@example.com"
    )

    # Mutate webhook URL directly to a prohibited loopback IP
    wh.url = "https://127.0.0.1:8443/evil"
    await test_db.commit()

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="ssrf-strict-test",
        event_type="impression",
        payload='{"event":"impression"}',
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory, worker_id="worker-ssrf-real")

    # Real call to _deliver (no mock) - should be blocked by validate_destination_url
    await worker.process_batch()

    job = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(job)
    assert job.status == "failed"
    assert "Destination rejected" in (job.last_error or "")


# ── 7. Phase 4B Corrective Patch Regression Tests ────────────────────────────

async def test_claim_tokens_are_unique_and_unpredictable_on_reclaim(
    client: AsyncClient, test_db: AsyncSession
):
    """Req 1: Two separate claims of the same job receive different tokens, including same worker."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_reclaim_tokens@example.com"
    )
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="claim-token-1",
        event_type="impression",
        payload="{}",
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory, worker_id="worker-same-instance")

    # Claim 1
    claimed_1 = await worker.claim_jobs()
    assert len(claimed_1) == 1
    token_1 = claimed_1[0].lease_token
    assert token_1 is not None
    assert token_1 != worker.worker_id

    # Expire the lease to make it eligible for reclaim
    job = await test_db.get(WebhookDelivery, delivery.id)
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await test_db.commit()

    # Reclaim by the exact same worker instance
    claimed_2 = await worker.claim_jobs()
    assert len(claimed_2) == 1
    token_2 = claimed_2[0].lease_token
    assert token_2 is not None
    assert token_2 != worker.worker_id

    # Tokens MUST be distinct even for the same worker instance
    assert token_1 != token_2


async def test_stale_attempt_cannot_persist_success_after_reclaim(
    client: AsyncClient, test_db: AsyncSession
):
    """Req 2: Earlier slow attempt cannot persist success after its lease has been reclaimed."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_stale_success@example.com"
    )
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="stale-succ-1",
        event_type="impression",
        payload="{}",
        status="processing",
        attempts=1,
        max_attempts=5,
        lease_token="claim-token-new",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        next_attempt_at=datetime.now(timezone.utc),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory)

    # Attempt with stale claim token tries to persist success
    persisted = await worker._persist_delivery_outcome(
        delivery_id=delivery.id,
        success=True,
        http_status=200,
        error_msg=None,
        claim_token="claim-token-old-stale",
    )
    assert persisted is False

    # Verify DB: status is still processing, lease is still claim-token-new, delivered_at is None
    fresh = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(fresh)
    assert fresh.status == "processing"
    assert fresh.lease_token == "claim-token-new"
    assert fresh.delivered_at is None


async def test_stale_attempt_cannot_persist_failure_or_reschedule_retry(
    client: AsyncClient, test_db: AsyncSession
):
    """Req 3: Earlier slow attempt cannot persist failure or schedule a retry after lease reclaimed."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_stale_fail@example.com"
    )
    original_next_attempt = datetime.now(timezone.utc) + timedelta(minutes=5)
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="stale-fail-1",
        event_type="impression",
        payload="{}",
        status="processing",
        attempts=1,
        max_attempts=5,
        lease_token="claim-token-new",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        next_attempt_at=original_next_attempt,
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory)

    # Stale attempt tries to report failure with backoff reschedule
    persisted = await worker._persist_delivery_outcome(
        delivery_id=delivery.id,
        success=False,
        http_status=500,
        error_msg="Slow failure",
        claim_token="claim-token-old-stale",
    )
    assert persisted is False

    # Verify DB: status is still processing, attempts not incremented, next_attempt_at unchanged
    fresh = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(fresh)
    assert fresh.status == "processing"
    assert fresh.lease_token == "claim-token-new"
    assert fresh.attempts == 1
    assert fresh.last_error is None


async def test_stale_attempt_cannot_increment_counters(
    client: AsyncClient, test_db: AsyncSession
):
    """Req 4: Stale attempt cannot increment delivery success or failure counters."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_stale_counters@example.com"
    )
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="stale-counter-1",
        event_type="impression",
        payload="{}",
        status="processing",
        attempts=1,
        max_attempts=5,
        lease_token="claim-token-active",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        next_attempt_at=datetime.now(timezone.utc),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory)

    # Stale success attempt
    res_s = await worker._persist_delivery_outcome(
        delivery_id=delivery.id,
        success=True,
        http_status=200,
        error_msg=None,
        claim_token="claim-token-stale",
    )
    assert res_s is False

    # Stale permanent failure attempt
    res_f = await worker._persist_delivery_outcome(
        delivery_id=delivery.id,
        success=False,
        http_status=404,
        error_msg="Not Found",
        is_permanent_err=True,
        claim_token="claim-token-stale",
    )
    assert res_f is False

    # Verify parent Webhook stats remained 0
    fresh_wh = await test_db.get(Webhook, wh.id)
    await test_db.refresh(fresh_wh)
    assert fresh_wh.deliveries_ok == 0
    assert fresh_wh.deliveries_failed == 0


async def test_current_claim_persists_valid_outcome_and_increments_counters(
    client: AsyncClient, test_db: AsyncSession
):
    """Req 5: Current claim with matching token persists outcome and increments counters."""
    _, wh, _ = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_valid_claim@example.com"
    )
    valid_token = "valid-token-active"
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="valid-claim-1",
        event_type="impression",
        payload="{}",
        status="processing",
        attempts=0,
        max_attempts=5,
        lease_token=valid_token,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        next_attempt_at=datetime.now(timezone.utc),
    )
    test_db.add(delivery)
    await test_db.commit()

    session_factory = async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )
    worker = WebhookOutboxWorker(session_factory=session_factory)

    # Valid claim persists outcome
    res = await worker._persist_delivery_outcome(
        delivery_id=delivery.id,
        success=True,
        http_status=200,
        error_msg=None,
        claim_token=valid_token,
    )
    assert res is True

    # Verify DB: status is delivered, lease cleared, delivered_at set, parent deliveries_ok incremented
    fresh = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(fresh)
    assert fresh.status == "delivered"
    assert fresh.lease_token is None
    assert fresh.lease_expires_at is None
    assert fresh.delivered_at is not None
    assert fresh.attempts == 1

    fresh_wh = await test_db.get(Webhook, wh.id)
    await test_db.refresh(fresh_wh)
    assert fresh_wh.deliveries_ok == 1


async def test_manual_retry_rejects_in_flight_job_with_409(
    client: AsyncClient, test_db: AsyncSession
):
    """Req 6: Manual retry rejects an in-flight (processing) job with HTTP 409 Conflict."""
    _, wh, token = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_inflight_retry@example.com"
    )
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="inflight-1",
        event_type="conversion",
        payload="{}",
        status="processing",
        attempts=1,
        max_attempts=5,
        lease_token="active-lease-token",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        next_attempt_at=datetime.now(timezone.utc),
    )
    test_db.add(delivery)
    await test_db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        f"/api/v1/webhooks/{wh.id}/deliveries/{delivery.id}/retry",
        headers=headers,
    )
    assert res.status_code == 409
    err = res.json()
    assert "in flight" in err["detail"].lower()

    # Verify lease and status in DB are untouched
    fresh = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(fresh)
    assert fresh.status == "processing"
    assert fresh.lease_token == "active-lease-token"


async def test_concurrent_retry_cannot_invalidate_active_claim(
    client: AsyncClient, test_db: AsyncSession
):
    """Req 7: Atomic conditional update prevents retry race when worker claims concurrently."""
    _, wh, token = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_race_retry@example.com"
    )
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        event_id="race-retry-1",
        event_type="conversion",
        payload="{}",
        status="pending",
        attempts=1,
        max_attempts=5,
    )
    test_db.add(delivery)
    await test_db.commit()

    # Simulate worker claiming right before retry query executes
    delivery.status = "processing"
    delivery.lease_token = "worker-race-token"
    delivery.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await test_db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        f"/api/v1/webhooks/{wh.id}/deliveries/{delivery.id}/retry",
        headers=headers,
    )
    assert res.status_code == 409
    # Active claim lease is preserved
    fresh = await test_db.get(WebhookDelivery, delivery.id)
    await test_db.refresh(fresh)
    assert fresh.status == "processing"
    assert fresh.lease_token == "worker-race-token"


async def test_manual_retry_enforces_tenant_isolation(
    client: AsyncClient, test_db: AsyncSession
):
    """Req 8: Tenant isolation remains strictly enforced for manual retry."""
    _, wh1, token1 = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_tenant1@example.com"
    )
    _, wh2, token2 = await _create_test_advertiser_and_webhook(
        client, test_db, email="adv_tenant2@example.com"
    )

    delivery1 = WebhookDelivery(
        webhook_id=wh1.id,
        owner_type=wh1.owner_type,
        owner_id=wh1.owner_id,
        event_id="tenant-retry-1",
        event_type="impression",
        payload="{}",
        status="failed",
        attempts=5,
        max_attempts=5,
    )
    test_db.add(delivery1)
    await test_db.commit()

    # Tenant 2 attempts to retry Tenant 1's delivery -> 404
    h2 = {"Authorization": f"Bearer {token2}"}
    res = await client.post(
        f"/api/v1/webhooks/{wh1.id}/deliveries/{delivery1.id}/retry",
        headers=h2,
    )
    assert res.status_code == 404

    # Tenant 2 attempts to pass wh2 with delivery1.id -> 404
    res2 = await client.post(
        f"/api/v1/webhooks/{wh2.id}/deliveries/{delivery1.id}/retry",
        headers=h2,
    )
    assert res2.status_code == 404


async def test_disabled_in_process_worker_prevents_background_polling(monkeypatch):
    """Req 9: Setting LITEADS_ENABLE_WEBHOOK_WORKER=false prevents background polling from starting."""
    import os
    from liteads.ad_server.services.webhook_worker import WebhookWorkerTask
    from liteads.common.config import get_settings

    # Clear cached settings
    get_settings.cache_clear()
    monkeypatch.setenv("LITEADS_ENABLE_WEBHOOK_WORKER", "false")
    settings = get_settings()
    assert settings.enable_webhook_worker is False

    task = WebhookWorkerTask(poll_interval=0.1)
    assert task.is_running is False

    # Simulate lifespan conditional check
    if settings.enable_webhook_worker:
        task.start()

    assert task.is_running is False

    # Clean shutdown should be a safe no-op
    await task.stop()
    assert task.is_running is False
    get_settings.cache_clear()


async def test_enabled_in_process_worker_lifecycle(monkeypatch):
    """Req 10: Existing startup/shutdown behavior remains correct when worker is enabled."""
    from liteads.ad_server.services.webhook_worker import WebhookWorkerTask
    from liteads.common.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LITEADS_ENABLE_WEBHOOK_WORKER", "true")
    settings = get_settings()
    assert settings.enable_webhook_worker is True

    task = WebhookWorkerTask(poll_interval=1.0)
    assert task.is_running is False

    task.start()
    assert task.is_running is True

    # Idempotent start does not create duplicate loops
    first_task = task._task
    task.start()
    assert task._task is first_task

    # Graceful shutdown stops the background task
    await task.stop()
    assert task.is_running is False
    assert task._task is None
    get_settings.cache_clear()
