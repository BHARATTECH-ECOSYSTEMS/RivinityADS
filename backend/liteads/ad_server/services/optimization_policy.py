"""
Optimization Policy Service.

Evaluates Laya decisions against campaign guardrails and eligibility.
"""

from dataclasses import dataclass
from typing import Any
from decimal import Decimal

from liteads.models.ad import Campaign
from liteads.models.base import ModerationStatus
from liteads.ad_server.services.optimization_prep import OptimizationPrepResult
from liteads.ad_server.services.laya_client import LayaDecisionResult
from liteads.ad_server.services.placement_analyzer import (
    PlacementAnalysisResult,
    VERDICT_GREAT,
    VERDICT_HEALTHY,
    VERDICT_LOW_VISIBILITY,
    VERDICT_INSUFFICIENT_DATA,
)


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

    def select_preferred_shift_destination(
        self,
        campaign: Campaign,
        candidate_destinations: list[str | dict[str, Any]],
        placements: list[PlacementAnalysisResult] | list[dict[str, Any]] | None = None,
        advertiser_balance: Decimal | None = None,
        verified_domain: bool | None = None,
    ) -> str | dict[str, Any] | None:
        """
        Advisory destination selection for optimizer shift actions preferring great placements.
        
        Strict Guardrails:
        - Campaign must be active (is_active == True).
        - Campaign must have >= 500 impressions.
        - Advertiser balance must be > 0 (if provided).
        - Creative moderation must be satisfied (no rejected creatives live).
        - Verified-domain enforcement must not be violated (if checked).
        - If candidate_destinations is empty, returns None (never invents a destination).
        - Placements with verdict == 'great' are prioritized.
        - Non-great placements ('healthy', 'low_visibility', 'insufficient_data') do NOT receive 'great' preference and never displace a 'great' placement.
        - Legacy NULL-slot rows never receive preference.
        - If no analysis is available or candidates have equal verdict, preserves deterministic ordering.
        """
        # 1. Guardrail: Campaign active
        if not campaign.is_active:
            return None

        # 2. Guardrail: Minimum 500 impressions
        if campaign.impressions < 500:
            return None

        # 3. Guardrail: Advertiser balance
        if advertiser_balance is not None and advertiser_balance <= Decimal("0"):
            return None

        # 4. Guardrail: Verified domain enforcement
        if verified_domain is False:
            return None

        # 5. Guardrail: Moderation status (creative cannot be rejected)
        creatives = campaign.__dict__.get("creatives") or []
        if any(getattr(c, "moderation_status", None) == ModerationStatus.REJECTED for c in creatives):
            return None

        # 6. Guardrail: Never invent a destination if no eligible destination exists
        if not candidate_destinations:
            return None

        # If placement analyses are absent or empty, preserve existing deterministic behavior
        if not placements:
            return candidate_destinations[0]

        # Build lookup of placement results by slot_id
        # Exclude legacy NULL-slot rows
        analysis_by_slot: dict[str, dict[str, Any]] = {}
        for p in placements:
            if isinstance(p, dict):
                slot_id = p.get("slot_id")
                verdict = p.get("verdict")
                ctr = float(p.get("ctr", 0.0))
            else:
                slot_id = p.slot_id
                verdict = p.verdict
                ctr = float(p.ctr)

            if slot_id:
                analysis_by_slot[slot_id] = {
                    "verdict": verdict,
                    "ctr": ctr,
                }

        # Score candidates
        def candidate_key(candidate: str | dict[str, Any]) -> tuple[int, float, str]:
            if isinstance(candidate, str):
                slot_id = candidate
            elif isinstance(candidate, dict):
                slot_id = candidate.get("slot_id") or candidate.get("id") or str(candidate)
            else:
                slot_id = str(candidate)

            p_data = analysis_by_slot.get(slot_id)
            if not p_data:
                return (0, 0.0, slot_id)

            verdict = p_data["verdict"]
            ctr = p_data["ctr"]

            # Only VERDICT_GREAT receives the great preference
            if verdict == VERDICT_GREAT:
                return (1, ctr, slot_id)
            else:
                return (0, 0.0, slot_id)

        great_candidates: list[tuple[tuple[int, float, str], str | dict[str, Any]]] = []
        other_candidates: list[str | dict[str, Any]] = []

        for c in candidate_destinations:
            key = candidate_key(c)
            if key[0] == 1:
                great_candidates.append((key, c))
            else:
                other_candidates.append(c)

        if great_candidates:
            # Sort great candidates: highest CTR first, then deterministic tie-break on slot_id
            great_candidates.sort(key=lambda item: (-item[0][1], item[0][2]))
            return great_candidates[0][1]

        # No great candidate: preserve original deterministic order
        return other_candidates[0]
