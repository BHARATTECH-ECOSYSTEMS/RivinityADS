"""
Optimization Preparation Service.

Gathers campaign performance and targeting state for Laya decision making,
and integrates Placement Auto-Analysis results (Phase 6 WU4).
"""

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.placement_analyzer import (
    PlacementAnalysisResult,
    PlacementAnalyzerService,
    VERDICT_GREAT,
)
from liteads.models.ad import Campaign, CampaignAssignment, Creative
from liteads.models.base import Status
from liteads.models.publisher import AdSlot


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

    def prepare_context(
        self,
        campaign: Campaign,
        placements: list[PlacementAnalysisResult] | list[dict[str, Any]] | None = None,
    ) -> OptimizationPrepResult:
        """
        Evaluate eligibility and prepare the state string for Laya.
        Optionally includes top_performing_placements from Phase 6 Placement Auto-Analysis.
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
            
        state_dict = self._build_state_dict(campaign, placements=placements)
        state_str = json.dumps(state_dict, sort_keys=True)
        
        return OptimizationPrepResult(
            eligible=True,
            state=state_str
        )

    async def get_campaign_eligible_placements(
        self,
        session: AsyncSession,
        campaign: Campaign,
    ) -> list[PlacementAnalysisResult]:
        """
        Retrieve placement analyses for placements authorized and eligible for this campaign.
        Enforces tenant and publisher boundaries to prevent cross-tenant data leakage.
        Reuses PlacementAnalyzerService bounded SQL aggregation.
        """
        analyzer = PlacementAnalyzerService(session)

        # 1. Query assignments explicitly via session to avoid lazyload greenlet errors
        stmt_assignments = select(CampaignAssignment).where(CampaignAssignment.campaign_id == campaign.id)
        res_assignments = await session.execute(stmt_assignments)
        assignments = res_assignments.scalars().all()

        if assignments:
            assigned_pub_ids = {a.publisher_id for a in assignments}
            assigned_slots = {a.slot_id for a in assignments if a.slot_id}
            
            scoped_results: list[PlacementAnalysisResult] = []
            for pub_id in sorted(assigned_pub_ids):
                pub_analyses = await analyzer.analyze_publisher_placements(pub_id)
                for analysis in pub_analyses:
                    if not assigned_slots or analysis.slot_id in assigned_slots:
                        scoped_results.append(analysis)
            results = scoped_results
        else:
            # If unassigned, analyze all platform placements
            results = await analyzer.analyze_all_placements()

        # 2. Filter by format compatibility with active creatives if creatives are present
        stmt_creatives = select(Creative).where(
            Creative.campaign_id == campaign.id,
            Creative.status != Status.DELETED,
        )
        res_creatives = await session.execute(stmt_creatives)
        creatives = res_creatives.scalars().all()

        active_formats = {
            c.creative_type for c in creatives 
            if getattr(c, "status", None) == Status.ACTIVE or getattr(c, "status", None) is None
        }
        if active_formats:
            results = [r for r in results if r.format in active_formats]

        return results

    def _filter_and_format_top_placements(
        self,
        campaign: Campaign,
        placements: list[PlacementAnalysisResult] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Filter placements for great performance, creative format compatibility,
        and campaign assignment scoping.
        Strips private publisher earnings, competitor IDs, and internal benchmark records.
        Returns a deterministically sorted list of placement summaries.
        """
        # Safely inspect creatives without triggering async lazyload on expired ORM attributes
        creatives = campaign.__dict__.get("creatives") or []
        active_formats = {
            c.creative_type for c in creatives 
            if getattr(c, "status", None) == Status.ACTIVE or getattr(c, "status", None) is None
        }

        # Safely inspect assignments without triggering async lazyload
        assignments = campaign.__dict__.get("assignments") or []
        assigned_pub_ids = {a.publisher_id for a in assignments} if assignments else None
        assigned_slots = {a.slot_id for a in assignments if a.slot_id} if assignments else None

        filtered: list[dict[str, Any]] = []

        for p in placements:
            if isinstance(p, dict):
                slot_id = p.get("slot_id")
                fmt = p.get("format")
                ctr = float(p.get("ctr", 0.0))
                verdict = p.get("verdict")
                pub_id = p.get("publisher_id")
            else:
                slot_id = p.slot_id
                fmt = p.format
                ctr = float(p.ctr)
                verdict = p.verdict
                pub_id = p.publisher_id

            # Legacy NULL-slot rows are never eligible placements
            if not slot_id:
                continue

            # Only prefer high-performing placements (verdict == "great")
            if verdict != VERDICT_GREAT:
                continue

            # Check format compatibility
            if active_formats and fmt not in active_formats:
                continue

            # Check tenant / assignment scoping
            if assigned_slots and slot_id not in assigned_slots:
                continue
            if assigned_pub_ids is not None and pub_id is not None and pub_id not in assigned_pub_ids:
                continue

            # Safe representation: NO competitor identities, NO private earnings, NO internal benchmarks
            filtered.append({
                "slot_id": slot_id,
                "format": fmt,
                "ctr": float(round(ctr, 4)),
                "verdict": verdict,
            })

        # Deterministic sorting: CTR descending, then slot_id ascending
        filtered.sort(key=lambda x: (-x["ctr"], str(x["slot_id"])))
        return filtered

    def _build_state_dict(
        self,
        campaign: Campaign,
        placements: list[PlacementAnalysisResult] | list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Build a deterministic dictionary representing campaign state.
        Includes top_performing_placements when placements context is provided.
        """
        ctr = campaign.clicks / campaign.impressions if campaign.impressions > 0 else 0.0
        cvr = campaign.conversions / campaign.clicks if campaign.clicks > 0 else 0.0

        bid_type = getattr(campaign, "bid_type", 1)
        bid_type_name = "CPA" if bid_type == 3 else ("CPC" if bid_type == 2 else "CPM")
        objective = "conversions" if bid_type == 3 else ("clicks" if bid_type == 2 else "impressions")

        cpa = float(round(float(campaign.spent_total) / campaign.conversions, 4)) if getattr(campaign, "conversions", 0) > 0 else None
        cpc = float(round(float(campaign.spent_total) / campaign.clicks, 4)) if getattr(campaign, "clicks", 0) > 0 else None

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
            "bid_type": bid_type_name,
            "objective": objective,
            "effective_cpa": cpa,
            "effective_cpc": cpc,
        }
        
        targeting = {
            "geo": {"include": [], "exclude": []},
            "sector": {"include": [], "exclude": []}
        }
        
        # Safely inspect targeting rules from __dict__ to avoid descriptor triggering lazyload
        targeting_rules = campaign.__dict__.get("targeting_rules") or []

        # Sort rules to ensure deterministic output
        sorted_rules = sorted(targeting_rules, key=lambda r: (r.rule_type, r.is_include, str(r.rule_value)))
        
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

        state: dict[str, Any] = {
            "performance": perf,
            "targeting": targeting
        }

        # Extend with top_performing_placements if placements context is provided
        if placements is not None:
            state["top_performing_placements"] = self._filter_and_format_top_placements(
                campaign, placements
            )

        return state
