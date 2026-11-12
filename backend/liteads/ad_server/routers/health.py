"""
Health check and operational diagnostics endpoints (Phase 12B).
"""

import time
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.outbox_diagnostics import collect_outbox_diagnostics
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.database import db, get_session
from liteads.schemas.response import (
    DependenciesHealthResponse,
    DependencyStatus,
    HealthResponse,
    OutboxHealthResponse,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    General health check endpoint.

    Returns service status and dependency health flags.
    """
    settings = get_settings()

    # Check database
    db_healthy = await db.health_check()

    # Check Redis
    redis_healthy = await redis_client.health_check()

    status_str = "healthy" if (db_healthy and redis_healthy) else "degraded"

    return HealthResponse(
        status=status_str,
        version=settings.app_version,
        database=db_healthy,
        redis=redis_healthy,
    )


@router.get("/ping")
async def ping() -> dict:
    """Simple ping endpoint for fast connectivity checks."""
    return {"pong": True}


@router.get("/live")
@router.get("/health/live")
async def liveness_check() -> dict:
    """
    Liveness check for Kubernetes / container orchestrators.

    Returns 200 OK as long as the process is running.
    """
    return {"alive": True, "status": "alive"}


@router.get("/ready")
@router.get("/health/ready")
async def readiness_check(response: Response) -> dict:
    """
    Readiness check for container orchestrators.

    Returns HTTP 200 when ready to receive traffic, or HTTP 503 Service Unavailable
    when critical dependencies (PostgreSQL or Redis) are unavailable.
    """
    db_healthy = await db.health_check()
    redis_healthy = await redis_client.health_check()

    if not db_healthy or not redis_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "ready": False,
            "status": "not_ready",
            "reason": "Dependencies not ready",
            "database": "ok" if db_healthy else "unreachable",
            "redis": "ok" if redis_healthy else "unreachable",
        }

    return {
        "ready": True,
        "status": "ready",
        "database": "ok",
        "redis": "ok",
    }


@router.get("/health/deps", response_model=DependenciesHealthResponse)
@router.get("/health/dependencies", response_model=DependenciesHealthResponse)
async def dependencies_check(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> DependenciesHealthResponse:
    """
    Diagnostic dependency check.

    Measures round-trip check latency for PostgreSQL and Redis while strictly
    avoiding the leakage of credentials, connection strings, hosts, ports, or stack traces.
    """
    settings = get_settings()
    deps: dict[str, DependencyStatus] = {}

    # 1. Database check
    db_ok = False
    db_latency: float | None = None
    t0 = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        db_latency = round((time.perf_counter() - t0) * 1000, 2)
        db_ok = True
    except Exception:
        db_latency = round((time.perf_counter() - t0) * 1000, 2)
        db_ok = False

    deps["database"] = DependencyStatus(
        status="ok" if db_ok else "unreachable",
        latency_ms=db_latency,
    )

    # 2. Redis check
    redis_ok = False
    redis_latency: float | None = None
    if redis_client.connected:
        t0 = time.perf_counter()
        try:
            await redis_client.client.ping()
            redis_latency = round((time.perf_counter() - t0) * 1000, 2)
            redis_ok = True
        except Exception:
            redis_latency = round((time.perf_counter() - t0) * 1000, 2)
            redis_ok = False
    else:
        deps["redis"] = DependencyStatus(status="unreachable", latency_ms=None)

    if redis_client.connected:
        deps["redis"] = DependencyStatus(
            status="ok" if redis_ok else "unreachable",
            latency_ms=redis_latency,
        )

    # 3. Worker queue lag & DLQ check
    try:
        outbox_diag = await collect_outbox_diagnostics(session=session, update_metrics=False)
        oldest_age = max(
            outbox_diag.webhook_queue.oldest_pending_age_seconds or 0,
            outbox_diag.notification_queue.oldest_pending_age_seconds or 0,
        )
        total_failed = outbox_diag.webhook_queue.failed_count + outbox_diag.notification_queue.failed_count
        deps["worker_queue_lag"] = DependencyStatus(
            status="ok" if oldest_age < 300 else "degraded",
            latency_ms=float(oldest_age),
        )
        deps["worker_dlq"] = DependencyStatus(
            status="ok" if total_failed == 0 else "degraded",
            latency_ms=float(total_failed),
        )
    except Exception:
        deps["worker_queue_lag"] = DependencyStatus(status="unreachable", latency_ms=None)
        deps["worker_dlq"] = DependencyStatus(status="unreachable", latency_ms=None)

    # 4. Email provider check
    if settings.listmonk.enabled:
        t0 = time.perf_counter()
        try:
            from liteads.common.listmonk_client import get_listmonk_client
            client = get_listmonk_client()
            client_ok = await client.health_check()
            email_lat = round((time.perf_counter() - t0) * 1000, 2)
            deps["email_provider"] = DependencyStatus(
                status="ok" if client_ok else "unreachable",
                latency_ms=email_lat,
            )
        except Exception:
            deps["email_provider"] = DependencyStatus(status="unreachable", latency_ms=None)
    elif settings.email.host:
        deps["email_provider"] = DependencyStatus(status="ok", latency_ms=None)
    else:
        deps["email_provider"] = DependencyStatus(status="unconfigured", latency_ms=None)

    # 5. Payment provider check (Razorpay)
    if settings.payment.key_id and settings.payment.key_secret:
        deps["payment_provider"] = DependencyStatus(status="ok", latency_ms=None)
    else:
        deps["payment_provider"] = DependencyStatus(status="unconfigured", latency_ms=None)

    # 6. Laya sidecar check
    if settings.laya.enabled and settings.laya.base_url:
        t0 = time.perf_counter()
        try:
            import httpx
            async with httpx.AsyncClient(timeout=1.0) as http_client:
                r = await http_client.get(f"{settings.laya.base_url}/health")
                laya_lat = round((time.perf_counter() - t0) * 1000, 2)
                deps["laya_sidecar"] = DependencyStatus(
                    status="ok" if r.status_code == 200 else "unreachable",
                    latency_ms=laya_lat,
                )
        except Exception:
            deps["laya_sidecar"] = DependencyStatus(status="unreachable", latency_ms=None)
    else:
        deps["laya_sidecar"] = DependencyStatus(status="disabled", latency_ms=None)

    if db_ok and redis_ok:
        overall = "healthy"
    elif db_ok or redis_ok:
        overall = "degraded"
    else:
        overall = "unhealthy"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return DependenciesHealthResponse(status=overall, dependencies=deps)


@router.get("/health/outbox", response_model=OutboxHealthResponse)
async def outbox_health_check(
    session: AsyncSession = Depends(get_session),
) -> OutboxHealthResponse:
    """
    Operational diagnostic endpoint for background outbox queues.

    Returns pending counts, delayed counts, worker claims, failed items,
    and oldest pending item age for webhook deliveries and notification deliveries.
    Updates Prometheus gauges as a side effect for continuous scraper alignment.
    """
    return await collect_outbox_diagnostics(session=session, update_metrics=True)
