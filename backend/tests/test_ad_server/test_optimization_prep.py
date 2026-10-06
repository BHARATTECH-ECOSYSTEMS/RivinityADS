from decimal import Decimal
import json
import pytest
from datetime import datetime, timedelta

from liteads.models.ad import Campaign, TargetingRule
from liteads.models.base import Status
from liteads.ad_server.services.optimization_prep import OptimizationPreparationService


@pytest.fixture
def base_campaign():
    return Campaign(
        id=1,
        advertiser_id=1,
        name="Test Campaign",
        status=Status.ACTIVE,
        start_time=datetime.now() - timedelta(days=1),
        end_time=datetime.now() + timedelta(days=1),
        impressions=1000,
        clicks=50,
        conversions=5,
        spent_today=Decimal("10.50"),
        spent_total=Decimal("100.50"),
        budget_daily=Decimal("50.00"),
        budget_total=Decimal("500.00"),
        bid_amount=Decimal("1.50"),
        targeting_rules=[]
    )


def test_active_campaign_eligible(base_campaign):
    service = OptimizationPreparationService()
    result = service.prepare_context(base_campaign)
    
    assert result.eligible is True
    assert result.state is not None
    assert result.reason is None


def test_inactive_campaign_ineligible(base_campaign):
    base_campaign.status = Status.PAUSED
    
    service = OptimizationPreparationService()
    result = service.prepare_context(base_campaign)
    
    assert result.eligible is False
    assert result.reason == "Campaign is not active"
    assert result.state is None


def test_below_threshold_impressions_ineligible(base_campaign):
    base_campaign.impressions = 0
    service = OptimizationPreparationService()
    result = service.prepare_context(base_campaign)
    assert result.eligible is False
    assert result.reason == "Campaign has fewer than 500 impressions"
    
    base_campaign.impressions = 499
    result = service.prepare_context(base_campaign)
    assert result.eligible is False
    assert result.reason == "Campaign has fewer than 500 impressions"


def test_threshold_boundary_eligible(base_campaign):
    base_campaign.impressions = 500
    service = OptimizationPreparationService()
    result = service.prepare_context(base_campaign)
    assert result.eligible is True


def test_performance_fields_representation(base_campaign):
    service = OptimizationPreparationService()
    result = service.prepare_context(base_campaign)
    
    state_dict = json.loads(result.state)
    perf = state_dict["performance"]
    
    assert perf["impressions"] == 1000
    assert perf["clicks"] == 50
    assert perf["conversions"] == 5
    assert perf["spent_today"] == 10.50
    assert perf["spent_total"] == 100.50
    assert perf["daily_budget"] == 50.00
    assert perf["total_budget"] == 500.00
    assert perf["bid_amount"] == 1.50
    assert perf["ctr"] == 0.05
    assert perf["cvr"] == 0.10


def test_targeting_representation(base_campaign):
    base_campaign.targeting_rules = [
        TargetingRule(
            rule_type="geo",
            is_include=True,
            rule_value={"countries": ["US"]}
        ),
        TargetingRule(
            rule_type="geo",
            is_include=False,
            rule_value={"cities": ["New York"]}
        ),
        TargetingRule(
            rule_type="sector",
            is_include=True,
            rule_value={"sectors": ["technology"]}
        )
    ]
    
    service = OptimizationPreparationService()
    result = service.prepare_context(base_campaign)
    
    state_dict = json.loads(result.state)
    targeting = state_dict["targeting"]
    
    assert {"countries": ["US"]} in targeting["geo"]["include"]
    assert {"cities": ["New York"]} in targeting["geo"]["exclude"]
    assert {"sectors": ["technology"]} in targeting["sector"]["include"]
    assert len(targeting["sector"]["exclude"]) == 0


def test_no_mutation(base_campaign):
    original_impressions = base_campaign.impressions
    original_budget = base_campaign.budget_daily
    
    service = OptimizationPreparationService()
    service.prepare_context(base_campaign)
    
    assert base_campaign.impressions == original_impressions
    assert base_campaign.budget_daily == original_budget


def test_laya_context_deterministic(base_campaign):
    base_campaign.targeting_rules = [
        TargetingRule(
            rule_type="geo",
            is_include=True,
            rule_value={"countries": ["US"]}
        ),
        TargetingRule(
            rule_type="sector",
            is_include=True,
            rule_value={"sectors": ["technology"]}
        )
    ]
    
    service = OptimizationPreparationService()
    result1 = service.prepare_context(base_campaign)
    
    # Reverse the order of rules
    base_campaign.targeting_rules = [
        base_campaign.targeting_rules[1],
        base_campaign.targeting_rules[0]
    ]
    
    result2 = service.prepare_context(base_campaign)
    
    assert result1.state == result2.state
