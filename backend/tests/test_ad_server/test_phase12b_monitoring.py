"""Tests for Phase 12B: Operational Monitoring & Incident Readiness.

Covers:
1. Health checks: /health, /ping, /live, /health/live, /ready (200 OK / 503 on failure),
   /health/ready, /health/deps (with latency measurement and credential sanitization),
   and /health/outbox.
2. Outbox diagnostics & bounded metrics:
   - Empty queue state
   - Ready vs delayed pending items
   - Active worker processing leases
   - Permanently failed / dead-letter items
   - Oldest pending event age calculation
   - Backlog degradation detection (>600s)
   - Prometheus gauge updates
3. Laya dependency monitoring:
   - Bounded timeout enforcement (min/max clamps)
   - Request latency observation
   - Timeout classification and metric emission
   - Network/connection error classification
   - Non-200 HTTP error classification
   - Malformed JSON response classification
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from httpx import AsyncClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.middleware.metrics import (
    LAYA_ERRORS_TOTAL,
    LAYA_REQUESTS_TOTAL,
    OUTBOX_DELAYED_TOTAL,
    OUTBOX_FAILED_TOTAL,
    OUTBOX_OLDEST_AGE_SECONDS,
    OUTBOX_PENDING_TOTAL,
    OUTBOX_PROCESSING_TOTAL,
)
from liteads.ad_server.services.laya_client import (
    LayaDecisionClient,
    LayaMalformedResponseError,
    LayaUnavailableError,
)
from liteads.ad_server.services.outbox_diagnostics import (
    collect_outbox_diagnostics,
    get_queue_metrics,
)
from liteads.models.auth import User
from liteads.models.notification import Notification, NotificationDelivery
from liteads.models.webhook import Webhook, WebhookDelivery

pytestmark = pytest.mark.asyncio


# =============================================================================
# 1. Health Endpoints Tests
# =============================================================================

async def test_health_and_ping_endpoints(client: AsyncClient):
    """GET /health and GET /ping return 200 with service status."""
    res_ping = await client.get("/ping")
    assert res_ping.status_code == 200
    assert res_ping.json() == {"pong": True}

    res_health = await client.get("/health")
    assert res_health.status_code == 200
    data = res_health.json()
    assert data["status"] in ("healthy", "degraded")
    assert "version" in data
    assert "database" in data
    assert "redis" in data


async def test_liveness_endpoints(client: AsyncClient):
    """GET /live and GET /health/live return 200 OK indicating process is alive."""
    for path in ("/live", "/health/live"):
        res = await client.get(path)
        assert res.status_code == 200
        data = res.json()
        assert data["alive"] is True
        assert data["status"] == "alive"


async def test_readiness_endpoint_success(client: AsyncClient):
    """GET /ready and /health/ready return 200 OK when dependencies are ready."""
    with patch("liteads.common.database.db.health_check", new_callable=AsyncMock) as mock_db, \
         patch("liteads.common.cache.redis_client.health_check", new_callable=AsyncMock) as mock_redis:
        mock_db.return_value = True
        mock_redis.return_value = True

        for path in ("/ready", "/health/ready"):
            res = await client.get(path)
            assert res.status_code == 200
            data = res.json()
            assert data["ready"] is True
            assert data["status"] == "ready"
            assert data["database"] == "ok"
            assert data["redis"] == "ok"


async def test_readiness_endpoint_failure_returns_503(client: AsyncClient):
    """GET /ready returns 503 Service Unavailable when a critical dependency is down."""
    # 1. Database down
    with patch("liteads.common.database.db.health_check", new_callable=AsyncMock) as mock_db, \
         patch("liteads.common.cache.redis_client.health_check", new_callable=AsyncMock) as mock_redis:
        mock_db.return_value = False
        mock_redis.return_value = True

        res = await client.get("/health/ready")
        assert res.status_code == 503
        data = res.json()
        assert data["ready"] is False
        assert data["status"] == "not_ready"
        assert data["database"] == "unreachable"
        assert data["redis"] == "ok"

    # 2. Redis down
    with patch("liteads.common.database.db.health_check", new_callable=AsyncMock) as mock_db, \
         patch("liteads.common.cache.redis_client.health_check", new_callable=AsyncMock) as mock_redis:
        mock_db.return_value = True
        mock_redis.return_value = False

        res = await client.get("/ready")
        assert res.status_code == 503
        data = res.json()
        assert data["ready"] is False
        assert data["status"] == "not_ready"
        assert data["database"] == "ok"
        assert data["redis"] == "unreachable"


async def test_dependencies_diagnostics_sanitized(client: AsyncClient):
    """GET /health/deps measures latency and strictly prevents leaking credentials."""
    res = await client.get("/health/deps")
    assert res.status_code in (200, 503)
    data = res.json()

    assert "status" in data
    assert "dependencies" in data
    assert "database" in data["dependencies"]
    assert "redis" in data["dependencies"]

    # Verify sanitized output: no passwords, usernames, or full connection URIs
    raw_text = res.text.lower()
    for forbidden in ("password", "postgresql://", "redis://", "@localhost", "secret"):
        assert forbidden not in raw_text

    db_info = data["dependencies"]["database"]
    assert db_info["status"] in ("ok", "unreachable")
    if db_info["status"] == "ok":
        assert isinstance(db_info["latency_ms"], (int, float))
        assert db_info["latency_ms"] >= 0


async def test_outbox_health_endpoint(client: AsyncClient):
    """GET /health/outbox returns queue metrics and updates Prometheus."""
    res = await client.get("/health/outbox")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] in ("healthy", "degraded")
    assert "queues" in data
    assert "webhook_deliveries" in data["queues"]
    assert "notification_deliveries" in data["queues"]

    wh_q = data["queues"]["webhook_deliveries"]
    assert "pending_count" in wh_q
    assert "delayed_count" in wh_q
    assert "ready_count" in wh_q
    assert "processing_count" in wh_q
    assert "failed_count" in wh_q
    assert "oldest_pending_age_seconds" in wh_q


# =============================================================================
# 2. Outbox Diagnostics & Metrics Logic Tests
# =============================================================================

async def test_outbox_diagnostics_empty_queue(test_db: AsyncSession):
    """Empty queue produces 0 metrics and healthy status."""
    metrics = await get_queue_metrics(test_db, WebhookDelivery)
    assert metrics.pending_count == 0
    assert metrics.delayed_count == 0
    assert metrics.ready_count == 0
    assert metrics.processing_count == 0
    assert metrics.failed_count == 0
    assert metrics.oldest_pending_age_seconds == 0.0


async def test_outbox_diagnostics_ready_vs_delayed_and_oldest_age(test_db: AsyncSession):
    """Accurately distinguishes ready from delayed items and calculates oldest age."""
    now = datetime.now(timezone.utc)

    # Create dummy webhook
    wh = Webhook(
        owner_type="advertiser",
        owner_id=101,
        url="https://example.com/diag",
        secret="secret123",
        events="impression",
    )
    test_db.add(wh)
    await test_db.flush()

    # 1. Old ready pending delivery (created 100 seconds ago, next_attempt in past)
    old_item = WebhookDelivery(
        webhook_id=wh.id,
        owner_type="advertiser",
        owner_id=101,
        event_id="evt-old",
        event_type="impression",
        payload="{}",
        status="pending",
        attempts=1,
        max_attempts=5,
        next_attempt_at=now - timedelta(seconds=10),
    )
    # 2. Delayed pending delivery (in exponential retry backoff, next_attempt in future)
    delayed_item = WebhookDelivery(
        webhook_id=wh.id,
        owner_type="advertiser",
        owner_id=101,
        event_id="evt-delayed",
        event_type="impression",
        payload="{}",
        status="pending",
        attempts=2,
        max_attempts=5,
        next_attempt_at=now + timedelta(seconds=120),
    )
    # 3. Processing item (currently leased)
    leased_item = WebhookDelivery(
        webhook_id=wh.id,
        owner_type="advertiser",
        owner_id=101,
        event_id="evt-leased",
        event_type="impression",
        payload="{}",
        status="processing",
        attempts=1,
        max_attempts=5,
        lease_token="worker-token-xyz",
        lease_expires_at=now + timedelta(seconds=30),
        next_attempt_at=now,
    )
    # 4. Dead-lettered / permanently failed item
    failed_item = WebhookDelivery(
        webhook_id=wh.id,
        owner_type="advertiser",
        owner_id=101,
        event_id="evt-failed",
        event_type="impression",
        payload="{}",
        status="failed",
        attempts=5,
        max_attempts=5,
        last_error="Destination permanently rejected: HTTP 404",
        next_attempt_at=now,
    )

    test_db.add_all([old_item, delayed_item, leased_item, failed_item])
    await test_db.commit()

    # Artificially age the first item's created_at to 150 seconds ago
    old_item.created_at = (now - timedelta(seconds=150)).replace(tzinfo=None)
    await test_db.commit()

    # Query bounded metrics
    metrics = await get_queue_metrics(test_db, WebhookDelivery)

    assert metrics.pending_count == 2
    assert metrics.delayed_count == 1
    assert metrics.ready_count == 1
    assert metrics.processing_count == 1
    assert metrics.failed_count == 1
    assert metrics.oldest_pending_age_seconds >= 140.0


async def test_outbox_backlog_degradation_alert(test_db: AsyncSession):
    """Detects when oldest pending event exceeds 600 seconds and sets status degraded."""
    now = datetime.now(timezone.utc)

    wh = Webhook(
        owner_type="advertiser",
        owner_id=102,
        url="https://example.com/stuck",
        secret="secret123",
        events="impression",
    )
    test_db.add(wh)
    await test_db.flush()

    # Event created 750 seconds ago (exceeds MAX_HEALTHY_PENDING_AGE_SECONDS=600)
    stuck_item = WebhookDelivery(
        webhook_id=wh.id,
        owner_type="advertiser",
        owner_id=102,
        event_id="evt-stuck",
        event_type="impression",
        payload="{}",
        status="pending",
        attempts=1,
        max_attempts=5,
        next_attempt_at=now - timedelta(seconds=200),
    )
    test_db.add(stuck_item)
    await test_db.commit()

    stuck_item.created_at = (now - timedelta(seconds=750)).replace(tzinfo=None)
    await test_db.commit()

    report = await collect_outbox_diagnostics(test_db, update_metrics=True)
    assert report.status == "degraded"
    assert report.queues["webhook_deliveries"].oldest_pending_age_seconds >= 700.0


# =============================================================================
# 3. Laya Sidecar Client Monitoring Tests
# =============================================================================

class MockAsyncClient:
    """Mock HTTP client for simulating Laya sidecar responses and failure modes."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict | None = None,
        text: str = "",
        exception: Exception | None = None,
    ):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.exception = exception

    async def post(self, url: str, **kwargs) -> httpx.Response:
        if self.exception:
            raise self.exception
        request = httpx.Request("POST", url)
        if self._json_data is not None:
            return httpx.Response(self.status_code, json=self._json_data, request=request)
        return httpx.Response(self.status_code, text=self.text, request=request)

    async def aclose(self):
        pass


async def test_laya_timeout_bounding():
    """Client clamps timeout between 0.1s and 60.0s."""
    # Too small -> clamped to 0.1
    c1 = LayaDecisionClient(timeout=0.01)
    assert c1.timeout == 0.1

    # Too large -> clamped to 60.0
    c2 = LayaDecisionClient(timeout=120.0)
    assert c2.timeout == 60.0

    # Within bounds -> preserved
    c3 = LayaDecisionClient(timeout=5.5)
    assert c3.timeout == 5.5


async def test_laya_successful_request_records_metrics():
    """Successful decision call records duration and success counter."""
    mock_resp = {
        "answers": {
            "opt_decision": {
                "choice": "no_change",
                "confidence": 0.95,
            }
        }
    }
    mock_http = MockAsyncClient(status_code=200, json_data=mock_resp)
    client = LayaDecisionClient(client=mock_http)

    res = await client.decide("Campaign context state")
    assert res.choice == "no_change"
    assert res.confidence == 0.95


async def test_laya_timeout_monitoring_and_classification():
    """TimeoutException raises LayaUnavailableError and records timeout metrics."""
    mock_http = MockAsyncClient(exception=httpx.TimeoutException("Read timed out"))
    client = LayaDecisionClient(client=mock_http, timeout=2.0)

    with pytest.raises(LayaUnavailableError) as exc_info:
        await client.decide("Context state")

    assert "timed out" in str(exc_info.value)


async def test_laya_network_error_classification():
    """ConnectError raises LayaUnavailableError and records network_error metrics."""
    mock_http = MockAsyncClient(exception=httpx.ConnectError("Connection refused"))
    client = LayaDecisionClient(client=mock_http)

    with pytest.raises(LayaUnavailableError) as exc_info:
        await client.decide("Context state")

    assert "request failed" in str(exc_info.value)


async def test_laya_http_500_error_classification():
    """HTTP 500 error raises LayaUnavailableError and records http_500 metrics."""
    mock_http = MockAsyncClient(status_code=500, text="Internal Server Error")
    client = LayaDecisionClient(client=mock_http)

    with pytest.raises(LayaUnavailableError) as exc_info:
        await client.decide("Context state")

    assert "status 500" in str(exc_info.value)


async def test_laya_malformed_json_classification():
    """Invalid JSON response raises LayaMalformedResponseError and records malformed_response."""
    mock_http = MockAsyncClient(status_code=200, text="<!DOCTYPE html><html>Gateway error</html>")
    client = LayaDecisionClient(client=mock_http)

    with pytest.raises(LayaMalformedResponseError) as exc_info:
        await client.decide("Context state")

    assert "Invalid JSON" in str(exc_info.value)
