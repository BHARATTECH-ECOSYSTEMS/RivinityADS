import pytest
from decimal import Decimal
from sqlalchemy import select

from liteads.models.ad import Campaign, Advertiser, Suggestion, CampaignChangeLog, TargetingRule
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult
from liteads.ad_server.services.optimization_executor import OptimizationExecutor, OptimizationExecutionError

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def advertiser(test_db):
    adv = Advertiser(
        name="Executor Test Advertiser",
        contact_email="executor@example.com",
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)
    return adv


@pytest.fixture
async def campaign(test_db, advertiser):
    camp = Campaign(
        advertiser_id=advertiser.id,
        name="Test Executor Campaign",
        budget_daily=Decimal("100.00"),
        autopilot=False,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)
    return camp


async def test_rejected_policy_no_action(test_db, campaign):
    executor = OptimizationExecutor(test_db)
    
    policy_result = OptimizationPolicyResult(
        accepted=False,
        reason="Rejected",
        action="increase_budget_20",
        confidence=0.1,
        autopilot=False
    )
    
    await executor.execute(campaign, policy_result)
    
    # Verify no suggestion, changelog, or modification
    sugg_count = await test_db.scalar(select(Suggestion).where(Suggestion.campaign_id == campaign.id))
    log_count = await test_db.scalar(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    
    assert sugg_count is None
    assert log_count is None
    assert campaign.budget_daily == Decimal("100.00")


async def test_autopilot_off_budget_action(test_db, campaign):
    campaign.autopilot = False
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="increase_budget_20",
        confidence=0.9,
        autopilot=False,
        proposed_budget_delta=20.0,
        operation_type="MODIFY"
    )
    
    await executor.execute(campaign, policy_result)
    
    # Suggestion created, no actual modification, no changelog
    sugg_res = await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))
    suggestion = sugg_res.scalar_one_or_none()
    
    assert suggestion is not None
    assert suggestion.action["action"] == "increase_budget_20"
    
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    assert log_res.scalar_one_or_none() is None
    
    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")


async def test_autopilot_off_targeting_action(test_db, campaign):
    campaign.autopilot = False
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="shift_to_new_geo",
        confidence=0.9,
        autopilot=False,
        operation_type="ADD"
    )
    target_payload = {"geo": {"countries": ["US"]}}
    
    await executor.execute(campaign, policy_result, target_payload)
    
    sugg_res = await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))
    assert sugg_res.scalar_one_or_none() is not None
    
    # Targeting unmodified
    rules_res = await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign.id))
    assert rules_res.scalar_one_or_none() is None


async def test_autopilot_on_increase_budget(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="increase_budget_20",
        confidence=0.9,
        autopilot=True,
        proposed_budget_delta=20.0,
        operation_type="MODIFY"
    )
    
    await executor.execute(campaign, policy_result)
    
    # ChangeLog created, budget modified
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    log = log_res.scalar_one_or_none()
    
    assert log is not None
    assert log.action["action"] == "increase_budget_20"
    
    sugg_res = await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))
    assert sugg_res.scalar_one_or_none() is None
    
    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("120.00")


async def test_autopilot_on_decrease_budget(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="decrease_budget_20",
        confidence=0.9,
        autopilot=True,
        proposed_budget_delta=-20.0,
        operation_type="MODIFY"
    )
    
    await executor.execute(campaign, policy_result)
    
    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("80.00")


async def test_autopilot_on_geo_add_only(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    
    # pre-existing targeting rule
    old_rule = TargetingRule(
        campaign_id=campaign.id,
        rule_type="geo",
        is_include=True,
        rule_value={"countries": ["IN"]}
    )
    test_db.add(old_rule)
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="shift_to_new_geo",
        confidence=0.9,
        autopilot=True,
        operation_type="ADD"
    )
    target_payload = {"geo": {"countries": ["US"]}}
    
    await executor.execute(campaign, policy_result, target_payload)
    
    rules_res = await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign.id))
    rules = rules_res.scalars().all()
    
    assert len(rules) == 2 # Pre-existing preserved + 1 added
    assert any("IN" in r.rule_value.get("countries", []) for r in rules)
    assert any("US" in r.rule_value.get("countries", []) for r in rules)
    
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    assert log_res.scalar_one_or_none() is not None


async def test_autopilot_on_sector_add_only(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="shift_to_new_sector",
        confidence=0.9,
        autopilot=True,
        operation_type="ADD"
    )
    target_payload = {"sector": {"sectors": ["technology"]}}
    
    await executor.execute(campaign, policy_result, target_payload)
    
    rules_res = await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign.id))
    rules = rules_res.scalars().all()
    
    assert len(rules) == 1
    assert "technology" in rules[0].rule_value.get("sectors", [])


async def test_invalid_execution_missing_payload_fails_safely(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="shift_to_new_geo",
        confidence=0.9,
        autopilot=True,
        operation_type="ADD"
    )
    # Target payload missing
    
    with pytest.raises(OptimizationExecutionError, match="No concrete geo target provided"):
        await executor.execute(campaign, policy_result, target_payload=None)
        
    # No false changelog
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    assert log_res.scalar_one_or_none() is None


async def test_creative_execution_controlled_failure(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="improve_creative",
        confidence=0.9,
        autopilot=True,
        operation_type="SUGGESTION"
    )
    
    with pytest.raises(OptimizationExecutionError, match="No concrete creative execution mechanism exists"):
        await executor.execute(campaign, policy_result)
        
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    assert log_res.scalar_one_or_none() is None


async def test_transactionality_rollback(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    
    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="increase_budget_20",
        confidence=0.9,
        autopilot=True,
        proposed_budget_delta=40.0, # Will exceed 30% guardrail
        operation_type="MODIFY"
    )
    
    with pytest.raises(OptimizationExecutionError, match="Budget change exceeds 30% guardrail"):
        await executor.execute(campaign, policy_result)
        
    # Transaction should be rolled back.
    # Campaign should be unchanged.
    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")
    
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    assert log_res.scalar_one_or_none() is None


async def test_budget_boundary_exact_30(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    executor = OptimizationExecutor(test_db)
    
    # +30%
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=30.0,
        operation_type="MODIFY"
    )
    await executor.execute(campaign, policy_result)
    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("130.00")
    
    # -30%
    policy_result.proposed_budget_delta = -30.0
    await executor.execute(campaign, policy_result)
    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")


async def test_budget_boundary_exceeds_30(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    executor = OptimizationExecutor(test_db)
    
    # +30.1%
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=30.1,
        operation_type="MODIFY"
    )
    with pytest.raises(OptimizationExecutionError, match="Budget change exceeds 30% guardrail"):
        await executor.execute(campaign, policy_result)
        
    # -30.1%
    policy_result.proposed_budget_delta = -30.1
    with pytest.raises(OptimizationExecutionError, match="Budget change exceeds 30% guardrail"):
        await executor.execute(campaign, policy_result)


from unittest.mock import patch
from sqlalchemy.exc import IntegrityError


async def test_transaction_changelog_failure(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    executor = OptimizationExecutor(test_db)
    
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0,
        operation_type="MODIFY"
    )
    
    with patch("liteads.ad_server.services.optimization_executor.CampaignChangeLog", side_effect=ValueError("Simulated ChangeLog init failure")):
        with pytest.raises(OptimizationExecutionError, match="Action execution failed: Simulated ChangeLog init failure"):
            await executor.execute(campaign, policy_result)
            
    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00") # Rolled back
    
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    assert log_res.scalar_one_or_none() is None


async def test_transaction_commit_failure(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    executor = OptimizationExecutor(test_db)
    
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0,
        operation_type="MODIFY"
    )
    
    with patch.object(test_db, 'commit', side_effect=IntegrityError("simulated", "params", "orig")):
        with pytest.raises(OptimizationExecutionError, match="Action execution failed:"):
            await executor.execute(campaign, policy_result)
            
    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")
    
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    assert log_res.scalar_one_or_none() is None


async def test_transaction_targeting_failure(test_db, campaign):
    campaign.autopilot = True
    await test_db.commit()
    executor = OptimizationExecutor(test_db)
    
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="shift_to_new_geo",
        confidence=0.9, autopilot=True, operation_type="ADD"
    )
    
    target_payload = {"geo": {"countries": ["US"]}}
    
    with patch("liteads.ad_server.services.optimization_executor.TargetingRule", side_effect=ValueError("Simulated TargetingRule init failure")):
        with pytest.raises(OptimizationExecutionError, match="Action execution failed: Simulated TargetingRule init failure"):
            await executor.execute(campaign, policy_result, target_payload)
            
    rules_res = await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign.id))
    assert rules_res.scalar_one_or_none() is None
    
    log_res = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == campaign.id))
    assert log_res.scalar_one_or_none() is None
