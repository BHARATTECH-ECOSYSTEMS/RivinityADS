"""
Optimization Policy Service.

Evaluates Laya decisions against campaign guardrails and eligibility.
"""

from dataclasses import dataclass
from typing import Any
from decimal import Decimal

from liteads.models.ad import Campaign
from liteads.ad_server.services.optimization_prep import OptimizationPrepResult
from liteads.ad_server.services.laya_client import LayaDecisionResult


@dataclass
class OptimizationPolicyResult:
    """Result of optimization policy evaluation."""
    accepted: bool
    reason: str | None
    action: str | None
    confidence: float
    autopilot: bool
    proposed_budget_delta: float | None = None
    operation_type: str | None = None


class OptimizationPolicyService:
    """
    Evaluates optimization decisions against business rules and guardrails.
    
    This service is PURE and READ-ONLY. It does not modify any state.
    """

    VALID_ACTIONS = {
        "increase_budget_20",
        "decrease_budget_20",
        "shift_to_new_geo",
        "shift_to_new_sector",
        "improve_creative",
        "no_change",
    }

    CONFIDENCE_THRESHOLD = 0.65
    BUDGET_GUARDRAIL_MAX_PCT = 0.30

    def evaluate(
        self, 
        campaign: Campaign, 
        prep_result: OptimizationPrepResult, 
        decision: LayaDecisionResult
    ) -> OptimizationPolicyResult:
        """
        Evaluate if a decision is allowed to proceed to execution.
        """
        autopilot = bool(campaign.autopilot)
        confidence = decision.confidence
        action = decision.choice
        
        # 1. Eligibility Check
        if not prep_result.eligible:
            return OptimizationPolicyResult(
                accepted=False,
                reason=prep_result.reason or "Campaign is ineligible for optimization",
                action=action,
                confidence=confidence,
                autopilot=autopilot
            )
            
        # 2. Vocabulary Check
        if action not in self.VALID_ACTIONS:
            return OptimizationPolicyResult(
                accepted=False,
                reason=f"Unknown action: {action}",
                action=action,
                confidence=confidence,
                autopilot=autopilot
            )
            
        # 3. Confidence and 'no_change' Check
        if action == "no_change":
            return OptimizationPolicyResult(
                accepted=False,
                reason="Laya recommended no change",
                action=action,
                confidence=confidence,
                autopilot=autopilot
            )
            
        if confidence < self.CONFIDENCE_THRESHOLD:
            return OptimizationPolicyResult(
                accepted=False,
                reason=f"Confidence {confidence} is below threshold {self.CONFIDENCE_THRESHOLD}",
                action=action,
                confidence=confidence,
                autopilot=autopilot
            )
            
        # 4. Action-specific Guardrails
        if action == "increase_budget_20" or action == "decrease_budget_20":
            return self._evaluate_budget_action(action, campaign, confidence, autopilot)
            
        if action in ("shift_to_new_geo", "shift_to_new_sector"):
            return self._evaluate_targeting_action(action, campaign, confidence, autopilot)
            
        if action == "improve_creative":
            return OptimizationPolicyResult(
                accepted=True,
                reason="Creative improvement suggested",
                action=action,
                confidence=confidence,
                autopilot=autopilot,
                operation_type="SUGGESTION"
            )
            
        # Fallback (should be unreachable due to VALID_ACTIONS check)
        return OptimizationPolicyResult(
            accepted=False,
            reason="Action validation failed",
            action=action,
            confidence=confidence,
            autopilot=autopilot
        )

    def _evaluate_budget_action(
        self, action: str, campaign: Campaign, confidence: float, autopilot: bool
    ) -> OptimizationPolicyResult:
        """Evaluate budget actions against the ±30% guardrail."""
        current_budget = float(campaign.budget_daily)
        pct_change = 0.20 if action == "increase_budget_20" else -0.20
        
        # Guardrail check (even though 20% is <= 30%, we strictly enforce it here)
        if abs(pct_change) > self.BUDGET_GUARDRAIL_MAX_PCT:
            return OptimizationPolicyResult(
                accepted=False,
                reason=f"Budget change {pct_change*100}% exceeds guardrail {self.BUDGET_GUARDRAIL_MAX_PCT*100}%",
                action=action,
                confidence=confidence,
                autopilot=autopilot
            )
            
        proposed_delta = current_budget * pct_change
        
        return OptimizationPolicyResult(
            accepted=True,
            reason="Budget change within guardrails",
            action=action,
            confidence=confidence,
            autopilot=autopilot,
            proposed_budget_delta=proposed_delta,
            operation_type="MODIFY"
        )
        
    def _evaluate_targeting_action(
        self, action: str, campaign: Campaign, confidence: float, autopilot: bool
    ) -> OptimizationPolicyResult:
        """Evaluate targeting actions, ensuring ADD-ONLY semantics."""
        # Targeting changes must be ADD-ONLY as per Phase 2 spec.
        # We enforce this by explicitly tagging the operation_type as ADD.
        # A hypothetical future executor that receives operation_type="REPLACE" would fail.
        # We do not invent replacement semantics here.
        return OptimizationPolicyResult(
            accepted=True,
            reason="Targeting shift allowed as ADD operation",
            action=action,
            confidence=confidence,
            autopilot=autopilot,
            operation_type="ADD"
        )
