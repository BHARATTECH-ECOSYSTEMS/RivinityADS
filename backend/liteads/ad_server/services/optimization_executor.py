"""
Optimization Action Executor.

Orchestrates the application of approved Laya decisions.
"""

from typing import Any
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.ad import Campaign, Suggestion, CampaignChangeLog, TargetingRule
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult


class OptimizationExecutionError(Exception):
    """Raised when an optimization action cannot be executed safely."""
    pass


class OptimizationExecutor:
    """
    Executes approved optimization policies based on Campaign autopilot mode.
    
    Autopilot OFF -> Suggestion
    Autopilot ON -> Execution + CampaignChangeLog
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def execute(
        self, 
        campaign: Campaign, 
        policy_result: OptimizationPolicyResult, 
        target_payload: dict[str, Any] | None = None
    ) -> None:
        """
        Main orchestration method.
        """
        if not policy_result.accepted:
            return
            
        action_payload = {
            "action": policy_result.action,
            "confidence": policy_result.confidence,
        }
        if policy_result.proposed_budget_delta is not None:
            action_payload["proposed_budget_delta"] = policy_result.proposed_budget_delta
        if policy_result.operation_type is not None:
            action_payload["operation_type"] = policy_result.operation_type
        if target_payload is not None:
            action_payload["target_payload"] = target_payload

        if not campaign.autopilot:
            await self._execute_autopilot_off(campaign, action_payload)
        else:
            await self._execute_autopilot_on(campaign, policy_result, action_payload, target_payload)

    async def _execute_autopilot_off(
        self, campaign: Campaign, action_payload: dict[str, Any]
    ) -> None:
        """Create a suggestion without modifying campaign state."""
        suggestion = Suggestion(
            campaign_id=campaign.id,
            action=action_payload,
        )
        self.session.add(suggestion)
        await self.session.commit()

    async def _execute_autopilot_on(
        self, 
        campaign: Campaign, 
        policy_result: OptimizationPolicyResult, 
        action_payload: dict[str, Any],
        target_payload: dict[str, Any] | None
    ) -> None:
        """Apply action transactionally and log change."""
        action = policy_result.action

        try:
            if action in ("increase_budget_20", "decrease_budget_20"):
                self._apply_budget_action(campaign, policy_result)
            elif action == "shift_to_new_geo":
                self._apply_geo_action(campaign, policy_result, target_payload)
            elif action == "shift_to_new_sector":
                self._apply_sector_action(campaign, policy_result, target_payload)
            elif action == "improve_creative":
                raise OptimizationExecutionError("No concrete creative execution mechanism exists")
            elif action == "no_change":
                # Handled by policy rejecting it, but just in case
                pass
            else:
                raise OptimizationExecutionError(f"Unsupported action: {action}")
                
            # Log successful execution
            changelog = CampaignChangeLog(
                campaign_id=campaign.id,
                action=action_payload,
            )
            self.session.add(changelog)
            await self.session.commit()
            
        except Exception as e:
            await self.session.rollback()
            raise OptimizationExecutionError(f"Action execution failed: {e}") from e

    def _apply_budget_action(
        self, campaign: Campaign, policy_result: OptimizationPolicyResult
    ) -> None:
        if policy_result.proposed_budget_delta is None:
            raise ValueError("Missing budget delta")
        
        delta = Decimal(str(policy_result.proposed_budget_delta))
        pct_change = abs(delta) / campaign.budget_daily
        
        # Hardguard against delta exceeding 30%
        if pct_change > Decimal("0.30"):
            raise ValueError("Budget change exceeds 30% guardrail")
            
        campaign.budget_daily += delta

    def _apply_geo_action(
        self, campaign: Campaign, policy_result: OptimizationPolicyResult, target_payload: dict[str, Any] | None
    ) -> None:
        if policy_result.operation_type != "ADD":
            raise ValueError("Geo operations must be ADD-only")
            
        if not target_payload or "geo" not in target_payload:
            raise ValueError("No concrete geo target provided")
            
        rule = TargetingRule(
            campaign_id=campaign.id,
            rule_type="geo",
            is_include=True,
            rule_value=target_payload["geo"]
        )
        self.session.add(rule)

    def _apply_sector_action(
        self, campaign: Campaign, policy_result: OptimizationPolicyResult, target_payload: dict[str, Any] | None
    ) -> None:
        if policy_result.operation_type != "ADD":
            raise ValueError("Sector operations must be ADD-only")
            
        if not target_payload or "sector" not in target_payload:
            raise ValueError("No concrete sector target provided")
            
        rule = TargetingRule(
            campaign_id=campaign.id,
            rule_type="sector",
            is_include=True,
            rule_value=target_payload["sector"]
        )
        self.session.add(rule)
