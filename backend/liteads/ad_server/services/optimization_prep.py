"""
Optimization Preparation Service.

Gathers campaign performance and targeting state for Laya decision making.
"""

import json
from dataclasses import dataclass
from typing import Any

from liteads.models.ad import Campaign


@dataclass
class OptimizationPrepResult:
    """Result of optimization preparation."""
    eligible: bool
    reason: str | None = None
    state: str | None = None


class OptimizationPreparationService:
    """
    Prepares deterministic optimization context for the Laya decision client.
    
    This service is READ-ONLY and does not modify any campaign state.
    """

    def prepare_context(self, campaign: Campaign) -> OptimizationPrepResult:
        """
        Evaluate eligibility and prepare the state string for Laya.
        """
        if not campaign.is_active:
            return OptimizationPrepResult(
                eligible=False, 
                reason="Campaign is not active"
            )
            
        if campaign.impressions < 500:
            return OptimizationPrepResult(
                eligible=False, 
                reason="Campaign has fewer than 500 impressions"
            )
            
        state_dict = self._build_state_dict(campaign)
        state_str = json.dumps(state_dict, sort_keys=True)
        
        return OptimizationPrepResult(
            eligible=True,
            state=state_str
        )

    def _build_state_dict(self, campaign: Campaign) -> dict[str, Any]:
        """
        Build a deterministic dictionary representing campaign state.
        """
        ctr = campaign.clicks / campaign.impressions if campaign.impressions > 0 else 0.0
        cvr = campaign.conversions / campaign.clicks if campaign.clicks > 0 else 0.0

        perf = {
            "impressions": campaign.impressions,
            "clicks": campaign.clicks,
            "conversions": campaign.conversions,
            "spent_today": float(campaign.spent_today),
            "spent_total": float(campaign.spent_total),
            "daily_budget": float(campaign.budget_daily),
            "total_budget": float(campaign.budget_total),
            "bid_amount": float(campaign.bid_amount),
            "ctr": float(round(ctr, 6)),
            "cvr": float(round(cvr, 6)),
        }
        
        targeting = {
            "geo": {"include": [], "exclude": []},
            "sector": {"include": [], "exclude": []}
        }
        
        # Sort rules to ensure deterministic output
        sorted_rules = sorted(campaign.targeting_rules, key=lambda r: (r.rule_type, r.is_include, str(r.rule_value)))
        
        for rule in sorted_rules:
            if rule.rule_type == "geo":
                if rule.is_include:
                    targeting["geo"]["include"].append(rule.rule_value)
                else:
                    targeting["geo"]["exclude"].append(rule.rule_value)
            elif rule.rule_type == "sector":
                if rule.is_include:
                    targeting["sector"]["include"].append(rule.rule_value)
                else:
                    targeting["sector"]["exclude"].append(rule.rule_value)

        return {
            "performance": perf,
            "targeting": targeting
        }
