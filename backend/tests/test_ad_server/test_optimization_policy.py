from decimal import Decimal
import pytest

from liteads.models.ad import Campaign
from liteads.ad_server.services.optimization_prep import OptimizationPrepResult
from liteads.ad_server.services.laya_client import LayaDecisionResult
from liteads.ad_server.services.optimization_policy import OptimizationPolicyService


@pytest.fixture
def base_campaign():
    return Campaign(
        id=1,
        autopilot=False,
        budget_daily=Decimal("100.00"),
    )


@pytest.fixture
def eligible_prep():
    return OptimizationPrepResult(eligible=True, state="{}")


@pytest.fixture
def ineligible_prep():
    return OptimizationPrepResult(eligible=False, reason="Too few impressions", state=None)


def test_confidence_thresholds(base_campaign, eligible_prep):
    service = OptimizationPolicyService()
    
    # 0.64 -> rejected
    decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.64, raw_response={})
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.accepted is False
    assert "below threshold" in res.reason
    
    # 0.65 -> potentially accepted
    decision.confidence = 0.65
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.accepted is True
    
    # 0.90 -> potentially accepted
    decision.confidence = 0.90
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.accepted is True


def test_no_change_is_rejected(base_campaign, eligible_prep):
    service = OptimizationPolicyService()
    decision = LayaDecisionResult(choice="no_change", confidence=0.99, raw_response={})
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.accepted is False
    assert res.reason == "Laya recommended no change"


def test_eligibility_enforced(base_campaign, ineligible_prep):
    service = OptimizationPolicyService()
    decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.90, raw_response={})
    res = service.evaluate(base_campaign, ineligible_prep, decision)
    assert res.accepted is False
    assert res.reason == "Too few impressions"


def test_action_vocabulary(base_campaign, eligible_prep):
    service = OptimizationPolicyService()
    
    for action in ["increase_budget_20", "decrease_budget_20", "shift_to_new_geo", "shift_to_new_sector", "improve_creative"]:
        decision = LayaDecisionResult(choice=action, confidence=0.90, raw_response={})
        res = service.evaluate(base_campaign, eligible_prep, decision)
        assert res.accepted is True, f"Action {action} should be accepted"
        
    # Unknown action rejected
    decision = LayaDecisionResult(choice="double_budget_now", confidence=0.90, raw_response={})
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.accepted is False
    assert "Unknown action" in res.reason


def test_budget_guardrail(base_campaign, eligible_prep):
    service = OptimizationPolicyService()
    base_campaign.budget_daily = Decimal("100.00")
    
    # +20% allowed
    decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.90, raw_response={})
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.accepted is True
    assert res.proposed_budget_delta == 20.0
    
    # -20% allowed
    decision = LayaDecisionResult(choice="decrease_budget_20", confidence=0.90, raw_response={})
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.accepted is True
    assert res.proposed_budget_delta == -20.0
    
    # Force evaluation of > 30% to verify guardrail enforcement logic
    # Since LayaDecisionResult strictly types choices, we test the internal method directly
    res_exceeds_pos = service._evaluate_budget_action("dummy_increase", base_campaign, 0.90, False)
    # mock a 40% change inside the method for testing?
    # Instead, we can temporarily monkeypatch BUDGET_GUARDRAIL_MAX_PCT to test rejection
    service.BUDGET_GUARDRAIL_MAX_PCT = 0.15 # lower threshold below 20%
    res_rejected = service.evaluate(base_campaign, eligible_prep, decision)
    assert res_rejected.accepted is False
    assert "exceeds guardrail" in res_rejected.reason


def test_geo_sector_guardrail_add_only(base_campaign, eligible_prep):
    service = OptimizationPolicyService()
    
    decision_geo = LayaDecisionResult(choice="shift_to_new_geo", confidence=0.90, raw_response={})
    res = service.evaluate(base_campaign, eligible_prep, decision_geo)
    assert res.accepted is True
    assert res.operation_type == "ADD"
    
    decision_sec = LayaDecisionResult(choice="shift_to_new_sector", confidence=0.90, raw_response={})
    res = service.evaluate(base_campaign, eligible_prep, decision_sec)
    assert res.accepted is True
    assert res.operation_type == "ADD"
    
    # Verify we don't return operation_type="REPLACE" ever
    assert res.operation_type != "REPLACE"


def test_autopilot_representation(base_campaign, eligible_prep):
    service = OptimizationPolicyService()
    decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.90, raw_response={})
    
    base_campaign.autopilot = False
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.autopilot is False
    
    base_campaign.autopilot = True
    res = service.evaluate(base_campaign, eligible_prep, decision)
    assert res.autopilot is True


def test_mutation_safety(base_campaign, eligible_prep):
    service = OptimizationPolicyService()
    decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.90, raw_response={})
    
    original_budget = base_campaign.budget_daily
    original_autopilot = base_campaign.autopilot
    
    service.evaluate(base_campaign, eligible_prep, decision)
    
    assert base_campaign.budget_daily == original_budget
    assert base_campaign.autopilot == original_autopilot
