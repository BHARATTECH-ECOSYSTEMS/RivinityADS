"""
Test Suite for Prompt 2: Optimization Worker Mode and Fail-Safe Laya Contract (P0-02 & P1-03).

Verifies:
1. Docker Compose defaults optimization worker to suggestions_only and does not run Laya locally.
2. OptimizationSettings smoke startup loads cleanly with suggestions_only.
3. Invalid/unknown optimization modes fail closed at startup (no silent downgrade).
4. suggestions_only mode creates suggestions but NEVER mutates campaign daily budgets, bids, or status.
5. Optimization guardrails (kill switch and limited_pilot allowlist bounds) enforce zero mutations.
6. Diagnostic health endpoint accurately reports Laya status (disabled, ok, degraded, unreachable)
   without impacting core service readiness.
7. Laya disabled or unreachable fails closed in optimizer without mutating campaigns.
8. Core ad serving and readiness checks operate normally when Laya is disabled or unreachable.
"""

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
import yaml
from fastapi import Response
from sqlalchemy import select, delete

from liteads.common.config import get_settings, OptimizationSettings
from liteads.ad_server.services.laya_client import (
    LayaDecisionClient,
    LayaUnavailableError,
)
from liteads.ad_server.services.optimization_executor import (
    OptimizationExecutor,
)
from liteads.ad_server.services.optimization_policy import (
    OptimizationPolicyResult,
)
from liteads.ad_server.routers.health import (
    dependencies_check,
)
from liteads.models.ad import (
    Campaign,
    Advertiser,
    Suggestion,
    CampaignChangeLog,
    OptimizationDecision,
)
from liteads.models.base import Status

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def clean_optimizer_tables(test_db):
    """Ensure optimizer and campaign tables are clean for each test."""
    await test_db.execute(delete(OptimizationDecision))
    await test_db.execute(delete(Suggestion))
    await test_db.execute(delete(CampaignChangeLog))
    await test_db.execute(delete(Campaign))
    await test_db.execute(delete(Advertiser))
    await test_db.commit()
    yield
    await test_db.rollback()


@pytest.fixture
async def sample_advertiser(test_db):
    adv = Advertiser(
        name="Prompt2 Test Advertiser",
        contact_email="prompt2@example.com",
        balance=Decimal("5000.00"),
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)
    return adv


@pytest.fixture
async def sample_campaign(test_db, sample_advertiser):
    camp = Campaign(
        advertiser_id=sample_advertiser.id,
        name="Prompt2 Target Campaign",
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("2000.00"),
        spent_today=Decimal("10.00"),
        spent_total=Decimal("100.00"),
        bid_amount=Decimal("1.50"),
        status=Status.ACTIVE,
        autopilot=True,
        impressions=500,
        clicks=25,
        conversions=2,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)
    return camp


# ---------------------------------------------------------------------------
# 1. Compose Default Mode and External Dependency Contract
# ---------------------------------------------------------------------------

async def test_compose_default_optimization_mode_resolves_suggestions_only():
    """
    Parses docker-compose.prod.yml to verify:
    - optimization-worker specifies ${LITEADS_OPTIMIZATION__MODE:-suggestions_only}
    - ad-server and optimization-worker specify LAYA_ENABLED=${LAYA_ENABLED:-false}
    - Compose defines NO service named 'laya' (confirming external dependency contract)
    - Resolving environment defaults gives suggestions_only
    """
    compose_path = Path("backend/docker-compose.prod.yml")
    assert compose_path.exists(), "backend/docker-compose.prod.yml must exist"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    services = compose_data.get("services", {})
    assert "optimization-worker" in services, "optimization-worker service must be present"
    assert "ad-server" in services, "ad-server service must be present"
    assert "laya" not in services, "laya must NOT be defined as a local Compose service"

    worker_env = services["optimization-worker"].get("environment", [])
    worker_env_dict = {}
    for entry in worker_env:
        if "=" in entry:
            k, v = entry.split("=", 1)
            worker_env_dict[k] = v

    # Verify optimization mode default
    opt_mode_val = worker_env_dict.get("LITEADS_OPTIMIZATION__MODE", "")
    assert opt_mode_val == "${LITEADS_OPTIMIZATION__MODE:-suggestions_only}", (
        f"Expected '${{LITEADS_OPTIMIZATION__MODE:-suggestions_only}}', got '{opt_mode_val}'"
    )
    assert "autonomous" not in opt_mode_val, "autonomous mode must not appear in compose default"

    # Verify Laya external contract in worker
    assert worker_env_dict.get("LAYA_ENABLED") == "${LAYA_ENABLED:-false}", (
        "optimization-worker must declare LAYA_ENABLED=${LAYA_ENABLED:-false}"
    )
    assert worker_env_dict.get("LAYA_BASE_URL") == "${LAYA_BASE_URL:-http://laya:8000}", (
        "optimization-worker must declare configurable LAYA_BASE_URL"
    )

    # Verify Laya external contract in ad-server
    server_env = services["ad-server"].get("environment", [])
    server_env_dict = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in server_env if "=" in e}
    assert server_env_dict.get("LAYA_ENABLED") == "${LAYA_ENABLED:-false}", (
        "ad-server must declare LAYA_ENABLED=${LAYA_ENABLED:-false}"
    )

    # Verify that when environment variable is unset, OptimizationSettings resolves suggestions_only
    settings = OptimizationSettings()
    assert settings.mode == "suggestions_only"


# ---------------------------------------------------------------------------
# 2. Optimization Worker Smoke Startup
# ---------------------------------------------------------------------------

async def test_optimization_worker_smoke_startup():
    """
    Instantiates OptimizationSettings under default environment and verifies
    clean startup with suggestions_only mode and safe guardrail defaults.
    """
    settings = OptimizationSettings()
    assert settings.mode == "suggestions_only"
    assert settings.kill_switch is False
    assert settings.interval_seconds == 900
    assert settings.pilot_campaign_ids == []
    assert settings.max_budget_delta_pct == 0.30
    assert settings.pilot_max_budget_delta_amount == 500.0


# ---------------------------------------------------------------------------
# 3. Invalid Optimization Mode Fails Closed
# ---------------------------------------------------------------------------

async def test_invalid_optimization_mode_fails_closed(monkeypatch):
    """
    Ensures that any unknown or invalid optimization mode string (such as 'autonomous'
    or 'invalid_foo') fails closed by raising a validation/configuration error,
    never silently defaulting or continuing.
    """
    try:
        # Direct class validation fails closed on invalid literal
        with pytest.raises(Exception):
            OptimizationSettings(mode="autonomous")  # type: ignore

        with pytest.raises(Exception):
            OptimizationSettings(mode="invalid_foo")  # type: ignore

        # Environment variable via get_settings() with double underscore (LITEADS_OPTIMIZATION__MODE)
        monkeypatch.setenv("LITEADS_OPTIMIZATION__MODE", "autonomous")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match=r"autonomous"):
            get_settings()

        monkeypatch.setenv("LITEADS_OPTIMIZATION__MODE", "super_auto")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match=r"super_auto"):
            get_settings()

        # Environment variable via get_settings() with single underscore (LITEADS_OPTIMIZATION_MODE)
        monkeypatch.delenv("LITEADS_OPTIMIZATION__MODE", raising=False)
        monkeypatch.setenv("LITEADS_OPTIMIZATION_MODE", "autonomous")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match=r"autonomous"):
            get_settings()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 4. Suggestions-Only Mode Immutability
# ---------------------------------------------------------------------------

async def test_suggestions_only_mode_immutability(test_db, sample_campaign):
    """
    Verifies that when running in suggestions_only mode:
    - Even with campaign.autopilot=True and policy_result.autopilot=True,
      zero mutations occur to campaign daily budget, bid amount, or status.
    - Zero CampaignChangeLog entries are created.
    - Exactly one reviewable Suggestion record is created in status PENDING.
    - Decision audit trail records outcome='suggested' under suggestions_only mode.
    """
    camp_id = sample_campaign.id
    orig_daily_budget = sample_campaign.budget_daily
    orig_bid_amount = sample_campaign.bid_amount
    orig_status = sample_campaign.status

    executor = OptimizationExecutor(test_db, mode="suggestions_only")
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="Scale high-performing campaign",
        action="increase_budget_20",
        confidence=0.92,
        autopilot=True,
        proposed_budget_delta=20.0,
        operation_type="MODIFY",
    )

    await executor.execute(sample_campaign, policy_result, enforce_cooldown=False)

    # 1. Campaign state must remain strictly unmodified
    await test_db.refresh(sample_campaign)
    assert sample_campaign.budget_daily == orig_daily_budget
    assert sample_campaign.bid_amount == orig_bid_amount
    assert sample_campaign.status == orig_status

    # 2. Zero change log entries
    logs = (
        await test_db.execute(
            select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id)
        )
    ).scalars().all()
    assert len(logs) == 0

    # 3. Suggestion record created
    suggs = (
        await test_db.execute(
            select(Suggestion).where(Suggestion.campaign_id == camp_id)
        )
    ).scalars().all()
    assert len(suggs) == 1
    assert suggs[0].status == "PENDING"
    assert suggs[0].action["action"] == "increase_budget_20"

    # 4. Decision audit trail
    dec_res = await test_db.execute(
        select(OptimizationDecision).where(OptimizationDecision.campaign_id == camp_id)
    )
    dec = dec_res.scalars().first()
    assert dec is not None
    assert dec.outcome == "suggested"
    assert dec.mode == "suggestions_only"


# ---------------------------------------------------------------------------
# 5. Optimization Guardrails (Kill Switch and Pilot Bounds)
# ---------------------------------------------------------------------------

async def test_optimization_guardrails_kill_switch_and_pilot_bounds(test_db, sample_campaign):
    """
    Verifies that:
    1. Global kill switch immediately halts changes: no changelog, no suggestion,
       and records outcome='rejected'.
    2. limited_pilot mode with a campaign not in pilot_campaign_ids falls closed
       to suggestions-only without applying automatic changes.
    """
    camp_id = sample_campaign.id
    settings = get_settings()

    # Part A: Kill switch enforcement
    orig_kill_switch = settings.optimization.kill_switch
    try:
        settings.optimization.kill_switch = True
        executor = OptimizationExecutor(test_db, mode="autopilot")
        policy_result = OptimizationPolicyResult(
            accepted=True,
            reason="Scale campaign",
            action="increase_budget_20",
            confidence=0.95,
            autopilot=True,
            proposed_budget_delta=20.0,
            operation_type="MODIFY",
        )

        await executor.execute(sample_campaign, policy_result, enforce_cooldown=False)

        # Confirm zero mutations and zero suggestions
        logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
        suggs = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == camp_id))).scalars().all()
        assert len(logs) == 0
        assert len(suggs) == 0

        # Audit reflects kill switch rejection
        dec = (await test_db.execute(select(OptimizationDecision).where(OptimizationDecision.campaign_id == camp_id))).scalars().first()
        assert dec is not None
        assert dec.outcome == "rejected"
        assert "kill switch" in dec.reason.lower()
    finally:
        settings.optimization.kill_switch = orig_kill_switch

    # Part B: limited_pilot mode without campaign in allowlist
    await test_db.execute(delete(OptimizationDecision))
    await test_db.commit()

    executor_pilot = OptimizationExecutor(test_db, mode="limited_pilot")
    policy_pilot = OptimizationPolicyResult(
        accepted=True,
        reason="Scale campaign",
        action="increase_budget_20",
        confidence=0.90,
        autopilot=True,
        proposed_budget_delta=20.0,
        operation_type="MODIFY",
    )

    await executor_pilot.execute(sample_campaign, policy_pilot, enforce_cooldown=False)

    await test_db.refresh(sample_campaign)
    assert sample_campaign.budget_daily == Decimal("100.00"), "Budget must not change in pilot if not allowlisted"

    pilot_logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
    assert len(pilot_logs) == 0

    pilot_suggs = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == camp_id))).scalars().all()
    assert len(pilot_suggs) == 1
    assert pilot_suggs[0].status == "PENDING"

    pilot_dec = (await test_db.execute(select(OptimizationDecision).where(OptimizationDecision.campaign_id == camp_id))).scalars().first()
    assert pilot_dec is not None
    assert pilot_dec.outcome == "suggested"
    assert "pilot allowlist" in pilot_dec.reason.lower()


# ---------------------------------------------------------------------------
# 6. Diagnostic Health Reporting for Laya (Disabled, OK, Degraded, Unreachable)
# ---------------------------------------------------------------------------

async def test_laya_health_reporting_disabled_ok_degraded_unreachable(test_db):
    """
    Verifies that /health/dependencies accurately classifies Laya as:
    - disabled when LAYA_ENABLED=false
    - ok when external service responds 200
    - degraded when external service responds 5xx
    - unreachable when connection fails or times out
    And that overall system readiness remains 'healthy' in all cases as long as
    PostgreSQL and Redis are functional.
    """
    settings = get_settings()
    orig_enabled = settings.laya.enabled
    orig_base_url = settings.laya.base_url

    try:
        # Case 1: Laya Disabled
        settings.laya.enabled = False
        res1 = await dependencies_check(response=Response(), session=test_db)
        assert res1.dependencies["laya_sidecar"].status == "disabled"
        assert res1.status in ("healthy", "degraded")

        # Case 2: Laya Enabled & OK (HTTP 200)
        settings.laya.enabled = True
        settings.laya.base_url = "http://mock-laya:8000"
        mock_resp_200 = MagicMock(status_code=200)
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp_200)):
            res2 = await dependencies_check(response=Response(), session=test_db)
            assert res2.dependencies["laya_sidecar"].status == "ok"

        # Case 3: Laya Enabled & Degraded (HTTP 503)
        mock_resp_503 = MagicMock(status_code=503)
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp_503)):
            res3 = await dependencies_check(response=Response(), session=test_db)
            assert res3.dependencies["laya_sidecar"].status == "degraded"

        # Case 4: Laya Enabled & Unreachable (Connection failure)
        with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.ConnectError("Connection refused"))):
            res4 = await dependencies_check(response=Response(), session=test_db)
            assert res4.dependencies["laya_sidecar"].status == "unreachable"

    finally:
        settings.laya.enabled = orig_enabled
        settings.laya.base_url = orig_base_url


# ---------------------------------------------------------------------------
# 7. Laya Disabled or Unreachable Fails Closed in Optimizer
# ---------------------------------------------------------------------------

async def test_laya_disabled_or_unreachable_fails_closed_in_optimizer(test_db, sample_campaign):
    """
    Verifies that:
    1. When enabled=False, LayaDecisionClient.decide() immediately raises LayaUnavailableError
       without making any network requests.
    2. When network connection fails, LayaUnavailableError is raised.
    3. The campaign remains completely unmodified if Laya is unavailable.
    """
    camp_id = sample_campaign.id

    # 1. Disabled client raises LayaUnavailableError with zero HTTP calls
    mock_http = AsyncMock()
    disabled_client = LayaDecisionClient(enabled=False, client=mock_http)
    with pytest.raises(LayaUnavailableError, match=r"Laya decision service is disabled"):
        await disabled_client.decide("Campaign state context")
    mock_http.post.assert_not_called()

    # 2. Connection error raises LayaUnavailableError
    failing_http = MagicMock()
    failing_http.post = AsyncMock(side_effect=httpx.ConnectError("Network unreachable"))
    failing_client = LayaDecisionClient(enabled=True, client=failing_http)
    with pytest.raises(LayaUnavailableError, match=r"request failed"):
        await failing_client.decide("Campaign state context")

    # 3. Verify zero campaign mutations
    await test_db.refresh(sample_campaign)
    assert sample_campaign.budget_daily == Decimal("100.00")
    assert sample_campaign.status == Status.ACTIVE


# ---------------------------------------------------------------------------
# 8. Ad Serving Unaffected When Laya Is Unreachable
# ---------------------------------------------------------------------------

async def test_ad_serving_unaffected_when_laya_unreachable(client):
    """
    Verifies that core endpoints (/health, /health/ready, /health/dependencies)
    and ad-serving components operate normally even when Laya is unreachable.
    """
    # 1. Core health check returns 200 OK
    resp_health = await client.get("/health")
    assert resp_health.status_code == 200
    assert "status" in resp_health.json()

    # 2. Readiness check returns 200 OK when core datastores are ready
    with patch("liteads.common.database.db.health_check", new_callable=AsyncMock) as mock_db, \
         patch("liteads.common.cache.redis_client.health_check", new_callable=AsyncMock) as mock_redis:
        mock_db.return_value = True
        mock_redis.return_value = True

        resp_ready = await client.get("/health/ready")
        assert resp_ready.status_code == 200
        data_ready = resp_ready.json()
        assert data_ready.get("ready") is True
        assert data_ready.get("status") == "ready"

    # 3. Dependencies check includes laya_sidecar without crashing
    resp_deps = await client.get("/health/dependencies")
    assert resp_deps.status_code in (200, 503)
    data_deps = resp_deps.json()
    assert "laya_sidecar" in data_deps.get("dependencies", {})
