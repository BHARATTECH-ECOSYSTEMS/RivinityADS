"""
Optimization Action Executor.

Orchestrates the application of approved Laya decisions with:
- Strict financial and wallet balance guardrails
- Suggestion deduplication and evidence update
- Safe operating modes: shadow, suggestions_only, limited_pilot, autopilot
- Safe improve_creative handling without breaking execution
- Comprehensive decision audit trail persistence
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
import math
from typing import Any, Optional
import uuid

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.ad import (
    Campaign,
    Advertiser,
    Suggestion,
    CampaignChangeLog,
    CampaignChangeLog as CampaignChangeLogModel,
    TargetingRule,
    OptimizationDecision,
)
from liteads.models.base import Status
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.logger import get_logger

logger = get_logger(__name__)


class OptimizationExecutionError(Exception):
    """Raised when an optimization action cannot be executed safely."""
    pass


def utc_now() -> datetime:
    """Return naive UTC datetime compatible with PostgreSQL TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(timezone.utc).replace(tzinfo=None)



def compute_suggestion_dedup_key(
    campaign_id: int, action: str, target_payload: dict[str, Any] | None
) -> str:
    """
    Computes a deterministic deduplication key for a campaign suggestion.
    Format: {action}:{sha256(campaign_id:action:normalized_target)}
    """
    normalized_target = ""
    if target_payload:
        if action == "shift_to_new_geo" and "geo" in target_payload:
            geo_val = target_payload["geo"]
            normalized_target = (
                json.dumps(geo_val, sort_keys=True)
                if isinstance(geo_val, (dict, list))
                else str(geo_val).strip().lower()
            )
        elif action == "shift_to_new_sector" and "sector" in target_payload:
            sector_val = target_payload["sector"]
            normalized_target = str(sector_val).strip().lower()
        elif action == "improve_creative" and "creative_id" in target_payload:
            normalized_target = f"creative:{target_payload['creative_id']}"
        else:
            try:
                normalized_target = json.dumps(target_payload, sort_keys=True)
            except Exception:
                normalized_target = str(target_payload)

    raw_key = f"{campaign_id}:{action}:{normalized_target}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
    return f"{action}:{key_hash}"


class OptimizationExecutor:
    """
    Executes approved optimization policies based on operating mode and Campaign autopilot.
    """

    def __init__(self, session: AsyncSession, mode: Optional[str] = None):
        self.session = session
        self.mode = mode

    async def execute(
        self, 
        campaign: Campaign, 
        policy_result: OptimizationPolicyResult, 
        target_payload: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        actor: str = "system_optimizer",
        mode: Optional[str] = None,
        enforce_cooldown: bool = True,
        fencing_token: Optional[int] = None,
        owner_token: Optional[str] = None,
        auto_commit: bool = True,
        suggestion: Optional[Suggestion] = None,
    ) -> None:
        """
        Main orchestration method respecting operating modes, policy guardrails, and fencing.
        """
        settings = get_settings()
        effective_mode = mode or self.mode
        if effective_mode is None:
            if policy_result.autopilot:
                effective_mode = "autopilot"
            else:
                try:
                    camp_autopilot = bool(getattr(campaign, "autopilot", False))
                except Exception:
                    camp_autopilot = False
                if camp_autopilot:
                    effective_mode = "autopilot"
                else:
                    effective_mode = settings.optimization.mode

        if not policy_result.accepted:
            await self._record_decision_audit(
                campaign=campaign,
                policy_result=policy_result,
                mode=effective_mode,
                outcome="rejected",
                reason=policy_result.reason,
                target_payload=target_payload,
                evidence=evidence,
                actor=actor,
                fencing_token=fencing_token,
                auto_commit=auto_commit,
            )
            return

        # Check emergency kill switch
        if settings.optimization.kill_switch:
            logger.warning("Global optimization kill switch is active; skipping execution", campaign_id=campaign.id)
            await self._record_decision_audit(
                campaign=campaign,
                policy_result=policy_result,
                mode=effective_mode,
                outcome="rejected",
                reason="Global optimization kill switch is active",
                target_payload=target_payload,
                evidence=evidence,
                actor=actor,
                fencing_token=fencing_token,
                auto_commit=auto_commit,
            )
            return

        action_payload: dict[str, Any] = {
            "action": policy_result.action,
            "confidence": policy_result.confidence,
        }
        if policy_result.proposed_budget_delta is not None:
            action_payload["proposed_budget_delta"] = policy_result.proposed_budget_delta
        if policy_result.operation_type is not None:
            action_payload["operation_type"] = policy_result.operation_type
        if target_payload is not None:
            action_payload["target_payload"] = target_payload

        # 1. SHADOW MODE: Record proposed decision in audit log, but make NO suggestions and NO changes
        if effective_mode == "shadow":
            logger.info(
                "Shadow mode: logging optimization decision without writing suggestions or applying actions",
                campaign_id=campaign.id,
                action=policy_result.action,
            )
            await self._record_decision_audit(
                campaign=campaign,
                policy_result=policy_result,
                mode="shadow",
                outcome="shadow_logged",
                reason="Shadow mode: decision evaluated but no action applied or suggestion written",
                target_payload=target_payload,
                evidence=evidence,
                actor=actor,
                fencing_token=fencing_token,
                auto_commit=auto_commit,
            )
            return

        # 2. SUGGESTIONS-ONLY MODE (Default): Always write reviewable suggestion, never apply automatic changes
        if effective_mode == "suggestions_only":
            await self._execute_autopilot_off(
                campaign,
                action_payload,
                evidence=evidence,
                actor=actor,
                fencing_token=fencing_token,
                owner_token=owner_token,
                auto_commit=auto_commit,
            )
            await self._record_decision_audit(
                campaign=campaign,
                policy_result=policy_result,
                mode="suggestions_only",
                outcome="suggested",
                reason="Suggestion created or updated for review",
                target_payload=target_payload,
                evidence=evidence,
                actor=actor,
                fencing_token=fencing_token,
                auto_commit=auto_commit,
            )
            return

        # 3. LIMITED PILOT MODE: Bounded autopilot for explicitly opted-in pilot campaigns
        if effective_mode == "limited_pilot":
            pilot_ids = settings.optimization.pilot_campaign_ids
            is_pilot_campaign = (not pilot_ids) or (campaign.id in pilot_ids)
            if campaign.autopilot and is_pilot_campaign:
                # Check pilot spend delta limit
                if policy_result.proposed_budget_delta is not None:
                    delta_abs = abs(float(policy_result.proposed_budget_delta))
                    if delta_abs > settings.optimization.pilot_max_budget_delta_amount:
                        logger.info(
                            "Limited pilot: budget delta exceeds pilot cap, falling back to suggestion",
                            campaign_id=campaign.id,
                            delta=delta_abs,
                            pilot_cap=settings.optimization.pilot_max_budget_delta_amount,
                        )
                        await self._execute_autopilot_off(
                            campaign,
                            action_payload,
                            evidence=evidence,
                            actor=actor,
                            fencing_token=fencing_token,
                            owner_token=owner_token,
                            auto_commit=auto_commit,
                        )
                        await self._record_decision_audit(
                            campaign=campaign,
                            policy_result=policy_result,
                            mode="limited_pilot",
                            outcome="suggested",
                            reason=f"Pilot delta {delta_abs} exceeded cap {settings.optimization.pilot_max_budget_delta_amount}",
                            target_payload=target_payload,
                            evidence=evidence,
                            actor=actor,
                            fencing_token=fencing_token,
                            auto_commit=auto_commit,
                        )
                        return

                await self._execute_autopilot_on(
                    campaign,
                    policy_result,
                    action_payload,
                    target_payload,
                    actor=actor,
                    enforce_cooldown=enforce_cooldown,
                    fencing_token=fencing_token,
                    owner_token=owner_token,
                    auto_commit=auto_commit,
                    suggestion=suggestion,
                )
                return
            else:
                await self._execute_autopilot_off(
                    campaign,
                    action_payload,
                    evidence=evidence,
                    actor=actor,
                    fencing_token=fencing_token,
                    owner_token=owner_token,
                    auto_commit=auto_commit,
                )
                await self._record_decision_audit(
                    campaign=campaign,
                    policy_result=policy_result,
                    mode="limited_pilot",
                    outcome="suggested",
                    reason="Campaign not opted into autopilot or not in pilot allowlist",
                    target_payload=target_payload,
                    evidence=evidence,
                    actor=actor,
                    fencing_token=fencing_token,
                    auto_commit=auto_commit,
                )
                return

        # 4. FULL AUTOPILOT MODE (Explicit opt-in required)
        if effective_mode == "autopilot":
            if not campaign.autopilot:
                await self._execute_autopilot_off(
                    campaign,
                    action_payload,
                    evidence=evidence,
                    actor=actor,
                    fencing_token=fencing_token,
                    owner_token=owner_token,
                    auto_commit=auto_commit,
                )
                await self._record_decision_audit(
                    campaign=campaign,
                    policy_result=policy_result,
                    mode="autopilot",
                    outcome="suggested",
                    reason="Campaign autopilot is disabled",
                    target_payload=target_payload,
                    evidence=evidence,
                    actor=actor,
                    fencing_token=fencing_token,
                    auto_commit=auto_commit,
                )
            else:
                await self._execute_autopilot_on(
                    campaign,
                    policy_result,
                    action_payload,
                    target_payload,
                    actor=actor,
                    enforce_cooldown=enforce_cooldown,
                    fencing_token=fencing_token,
                    owner_token=owner_token,
                    auto_commit=auto_commit,
                    suggestion=suggestion,
                )

    async def _execute_autopilot_off(
        self,
        campaign: Campaign,
        action_payload: dict[str, Any],
        evidence: dict[str, Any] | None = None,
        actor: str = "system_optimizer",
        fencing_token: Optional[int] = None,
        owner_token: Optional[str] = None,
        auto_commit: bool = True,
    ) -> Suggestion:
        """Create or update a reviewable suggestion with deduplication, cooldown checks, and fencing."""
        # 1. Database-enforced fencing token check
        if fencing_token is not None:
            current_fencing = getattr(campaign, "optimization_fencing_token", 0) or 0
            if fencing_token <= current_fencing:
                raise OptimizationExecutionError(
                    f"Stale worker fenced: campaign fencing token {current_fencing} >= worker token {fencing_token}"
                )
            campaign.optimization_fencing_token = fencing_token

        # 2. Redis leader lease pre-commit check
        if owner_token is not None and redis_client.connected:
            current_owner = await redis_client.get("lock:optimization_cycle")
            if current_owner != owner_token:
                raise OptimizationExecutionError("Stale worker fenced: leader lease lost before writing suggestion")

        action = action_payload["action"]
        target_payload = action_payload.get("target_payload")
        dedup_key = compute_suggestion_dedup_key(campaign.id, action, target_payload)

        settings = get_settings()
        rejection_cooldown = timedelta(seconds=settings.optimization.rejection_cooldown_seconds)
        now = utc_now()
        campaign_id = campaign.id

        # 3. Check for existing PENDING suggestion with same dedup_key
        stmt = (
            select(Suggestion)
            .where(
                Suggestion.campaign_id == campaign_id,
                Suggestion.status == "PENDING",
                Suggestion.dedup_key == dedup_key,
            )
            .with_for_update()
        )
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing is not None:
            # Update existing pending suggestion with fresh evidence/timestamps
            existing.updated_at = now
            existing_action = dict(existing.action) if isinstance(existing.action, dict) else {}
            existing_action["evaluation_count"] = existing_action.get("evaluation_count", 1) + 1
            existing_action["last_evaluated_at"] = now.isoformat()
            if "confidence" in action_payload:
                existing_action["confidence"] = action_payload["confidence"]
            if evidence:
                existing_action["evidence"] = evidence
            existing.action = existing_action
            if auto_commit:
                if owner_token is not None and redis_client.connected:
                    current_owner = await redis_client.get("lock:optimization_cycle")
                    if current_owner != owner_token:
                        raise OptimizationExecutionError("Stale worker fenced: leader lease expired before commit")
                await self.session.commit()
            return existing

        # 4. Check if identical suggestion was recently rejected (respect advertiser intent)
        stmt_rejected = (
            select(Suggestion)
            .where(
                Suggestion.campaign_id == campaign_id,
                Suggestion.status == "REJECTED",
                Suggestion.dedup_key == dedup_key,
            )
            .order_by(Suggestion.updated_at.desc())
        )
        res_rejected = await self.session.execute(stmt_rejected)
        recently_rejected = res_rejected.scalar_one_or_none()
        if recently_rejected is not None:
            rej_time = recently_rejected.updated_at
            if rej_time and rej_time.tzinfo is not None:
                rej_time = rej_time.replace(tzinfo=None)
            if rej_time and (now - rej_time) < rejection_cooldown:
                logger.info(
                    "Skipping suggestion creation: identical suggestion was recently rejected",
                    campaign_id=campaign_id,
                    action=action,
                    rejected_at=rej_time.isoformat(),
                )
                return recently_rejected

        # 5. Create fresh pending suggestion
        action_payload_with_meta = dict(action_payload)
        action_payload_with_meta["evaluation_count"] = 1
        action_payload_with_meta["first_suggested_at"] = now.isoformat()
        if evidence:
            action_payload_with_meta["evidence"] = evidence

        suggestion = Suggestion(
            campaign_id=campaign_id,
            action=action_payload_with_meta,
            status="PENDING",
            dedup_key=dedup_key,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(suggestion)
                await self.session.flush()
            if auto_commit:
                if owner_token is not None and redis_client.connected:
                    current_owner = await redis_client.get("lock:optimization_cycle")
                    if current_owner != owner_token:
                        raise OptimizationExecutionError("Stale worker fenced: leader lease expired before commit")
                await self.session.commit()
                await self.session.refresh(suggestion)
            return suggestion
        except IntegrityError as exc:
            # Distinguish intended uniqueness violation from unrelated IntegrityError failures.
            # Do not swallow arbitrary database errors (e.g. FK violations, NOT NULL violations).
            err_msg = str(exc).lower()
            is_dedup_conflict = (
                "uq_suggestions_pending_dedup" in err_msg
                or "unique" in err_msg
                or "dedup_key" in err_msg
            )
            if not is_dedup_conflict:
                if auto_commit:
                    await self.session.rollback()
                raise

            # Gracefully recover if concurrent worker inserted equivalent pending suggestion
            logger.info(
                "Suggestion dedup unique constraint race encountered; recovering with in-place update",
                campaign_id=campaign_id,
                dedup_key=dedup_key,
            )
            stmt_retry = (
                select(Suggestion)
                .where(
                    Suggestion.campaign_id == campaign_id,
                    Suggestion.status == "PENDING",
                    Suggestion.dedup_key == dedup_key,
                )
                .with_for_update()
            )
            res_retry = await self.session.execute(stmt_retry)
            existing_retry = res_retry.scalar_one_or_none()
            if existing_retry is not None:
                existing_retry.updated_at = now
                existing_action = dict(existing_retry.action) if isinstance(existing_retry.action, dict) else {}
                existing_action["evaluation_count"] = existing_action.get("evaluation_count", 1) + 1
                existing_action["last_evaluated_at"] = now.isoformat()
                if "confidence" in action_payload:
                    existing_action["confidence"] = action_payload["confidence"]
                if evidence:
                    existing_action["evidence"] = evidence
                existing_retry.action = existing_action
                if auto_commit:
                    if owner_token is not None and redis_client.connected:
                        current_owner = await redis_client.get("lock:optimization_cycle")
                        if current_owner != owner_token:
                            raise OptimizationExecutionError("Stale worker fenced: leader lease expired before commit")
                    await self.session.commit()
                    await self.session.refresh(existing_retry)
                return existing_retry
            if auto_commit:
                await self.session.rollback()
            raise

    async def _execute_autopilot_on(
        self, 
        campaign: Campaign, 
        policy_result: OptimizationPolicyResult, 
        action_payload: dict[str, Any],
        target_payload: dict[str, Any] | None = None,
        actor: str = "system_optimizer",
        enforce_cooldown: bool = True,
        fencing_token: Optional[int] = None,
        owner_token: Optional[str] = None,
        auto_commit: bool = True,
        suggestion: Optional[Suggestion] = None,
    ) -> None:
        """Apply action transactionally and log change with aggregate financial safety and fencing."""
        action = policy_result.action
        settings = get_settings()

        # Check campaign status before execution (automated background optimizer only)
        if actor == "system_optimizer" and campaign.status != 1:  # Status.ACTIVE == 1
            raise OptimizationExecutionError("Campaign is no longer active")

        # Check global and campaign kill switch
        if settings.optimization.kill_switch:
            raise OptimizationExecutionError("Optimization kill switch is active")

        # Database-enforced fencing token check
        if fencing_token is not None:
            current_fencing = getattr(campaign, "optimization_fencing_token", 0) or 0
            if fencing_token <= current_fencing:
                raise OptimizationExecutionError(
                    f"Stale worker fenced: campaign fencing token {current_fencing} >= worker token {fencing_token}"
                )
            campaign.optimization_fencing_token = fencing_token

        # Redis leader lease pre-commit check
        if owner_token is not None and redis_client.connected:
            current_owner = await redis_client.get("lock:optimization_cycle")
            if current_owner != owner_token:
                raise OptimizationExecutionError("Stale worker fenced: leader lease lost before write")

        # Enforce cooldown on automated actions (only when actor is optimizer and enforce_cooldown is True)
        if actor == "system_optimizer" and enforce_cooldown and settings.optimization.cooldown_seconds > 0:
            cooldown = timedelta(seconds=settings.optimization.cooldown_seconds)
            now = utc_now()
            recent_log_stmt = (
                select(CampaignChangeLogModel)
                .where(
                    CampaignChangeLogModel.campaign_id == campaign.id,
                    CampaignChangeLogModel.created_at >= now - cooldown,
                )
            )
            res_log = await self.session.execute(recent_log_stmt)
            if res_log.scalars().first() is not None:
                raise OptimizationExecutionError("Campaign in cooldown period after recent optimization action")

        # Enforce max daily automatic changes cap
        if actor == "system_optimizer":
            now = utc_now()
            day_ago = now - timedelta(hours=24)
            count_stmt = (
                select(func.count(CampaignChangeLogModel.id))
                .where(
                    CampaignChangeLogModel.campaign_id == campaign.id,
                    CampaignChangeLogModel.created_at >= day_ago,
                )
            )
            count_res = await self.session.execute(count_stmt)
            if (count_res.scalar() or 0) >= settings.optimization.max_daily_changes_per_campaign:
                raise OptimizationExecutionError("Campaign reached maximum daily automatic changes cap")

        campaign_id = campaign.id
        try:
            change_dict: dict[str, Any] | None = None
            if action in ("increase_budget_20", "decrease_budget_20"):
                change_dict = await self._apply_budget_action(campaign, policy_result)
            elif action == "shift_to_new_geo":
                change_dict = self._apply_geo_action(campaign, policy_result, target_payload)
            elif action == "shift_to_new_sector":
                change_dict = self._apply_sector_action(campaign, policy_result, target_payload)
            elif action == "improve_creative":
                # Autopilot cannot autonomously alter creative assets without human review.
                # Route safely to human-review suggestion without crashing or aborting the cycle!
                logger.info(
                    "Autopilot received improve_creative; routing to human review suggestion",
                    campaign_id=campaign_id,
                )
                await self._execute_autopilot_off(
                    campaign,
                    action_payload,
                    actor=actor,
                    fencing_token=fencing_token,
                    owner_token=owner_token,
                    auto_commit=auto_commit,
                )
                return
            elif action == "no_change":
                return
            else:
                raise OptimizationExecutionError(f"Unsupported action: {action}")
                
            # If a suggestion was provided for manual approval, mark it APPLIED atomically
            if suggestion is not None:
                suggestion.status = "APPLIED"
                suggestion.updated_at = utc_now()

            # Log successful execution
            changelog_payload = dict(action_payload)
            if change_dict:
                changelog_payload["applied_change"] = change_dict
            changelog = CampaignChangeLog(
                campaign_id=campaign_id,
                action=changelog_payload,
            )
            self.session.add(changelog)

            # Record audit decision
            await self._record_decision_audit(
                campaign=campaign,
                policy_result=policy_result,
                mode="autopilot",
                outcome="applied",
                reason="Applied via autopilot" if actor == "system_optimizer" else f"Manually approved by {actor}",
                target_payload=target_payload,
                change_applied=change_dict,
                actor=actor,
                execution_status="SUCCESS",
                campaign_id=campaign_id,
                fencing_token=fencing_token,
                auto_commit=auto_commit,
            )

            if auto_commit:
                # Pre-commit leader lease revalidation: ensures lease did not expire during execution
                if owner_token is not None and redis_client.connected:
                    current_owner = await redis_client.get("lock:optimization_cycle")
                    if current_owner != owner_token:
                        raise OptimizationExecutionError("Stale worker fenced: leader lease expired before commit")
                await self.session.commit()
            
        except Exception as e:
            if auto_commit:
                await self.session.rollback()
            await self._record_decision_audit(
                campaign=campaign,
                policy_result=policy_result,
                mode="autopilot",
                outcome="failed",
                reason=str(e),
                target_payload=target_payload,
                actor=actor,
                execution_status="FAILED",
                campaign_id=campaign_id,
                fencing_token=fencing_token,
                auto_commit=auto_commit,
            )
            if isinstance(e, OptimizationExecutionError):
                raise
            raise OptimizationExecutionError(f"Action execution failed: {e}") from e

    async def _apply_budget_action(
        self, campaign: Campaign, policy_result: OptimizationPolicyResult
    ) -> dict[str, Any]:
        """Apply budget changes with Decimal math and aggregate advertiser balance checks."""
        if policy_result.proposed_budget_delta is None:
            raise ValueError("Missing budget delta")

        if campaign.budget_daily is None or campaign.budget_daily <= Decimal("0"):
            raise ValueError("Campaign daily budget must be greater than zero")

        delta = Decimal(str(policy_result.proposed_budget_delta))
        if delta == Decimal("0"):
            raise ValueError("Budget delta cannot be zero")

        pct_change = abs(delta) / campaign.budget_daily

        # Hardguard against delta exceeding 30%
        if pct_change > Decimal("0.30"):
            raise ValueError("Budget change exceeds 30% guardrail")

        old_budget = campaign.budget_daily
        new_budget = old_budget + delta
        if new_budget <= Decimal("0"):
            raise ValueError("Proposed budget change results in non-positive daily budget")

        # Row-lock advertiser to check available wallet balance and aggregate active campaign limits
        advertiser = await self.session.get(Advertiser, campaign.advertiser_id, with_for_update=True)
        if advertiser is None:
            raise ValueError(f"Advertiser {campaign.advertiser_id} not found")

        # Financial balance constraint: optimizer cannot authorize spend exceeding wallet funds!
        if delta > Decimal("0"):
            if advertiser.balance is None or advertiser.balance <= Decimal("0"):
                raise ValueError("Advertiser wallet balance is zero or depleted")

            # Aggregate financial check: query sum of daily budgets for all other active campaigns
            other_budgets_stmt = (
                select(func.coalesce(func.sum(Campaign.budget_daily), Decimal("0")))
                .where(
                    Campaign.advertiser_id == advertiser.id,
                    Campaign.status == Status.ACTIVE,
                    Campaign.id != campaign.id,
                )
            )
            other_active_budgets = (await self.session.execute(other_budgets_stmt)).scalar() or Decimal("0")
            total_committed_daily_budget = other_active_budgets + new_budget

            # 1. Enforce aggregate Advertiser.daily_budget ceiling if configured
            if advertiser.daily_budget and advertiser.daily_budget > Decimal("0"):
                if total_committed_daily_budget > advertiser.daily_budget:
                    raise ValueError(
                        f"Total committed daily budget ({total_committed_daily_budget}) exceeds advertiser daily budget cap ({advertiser.daily_budget})"
                    )

            # 2. Enforce total committed daily budget does not exceed wallet funds
            if total_committed_daily_budget > advertiser.balance:
                raise ValueError(
                    f"Advertiser wallet balance ({advertiser.balance}) insufficient for total committed daily budget ({total_committed_daily_budget})"
                )

        # Lifetime budget ceiling check
        if campaign.budget_total and campaign.budget_total > Decimal("0"):
            spent_total = Decimal(str(campaign.spent_total or "0"))
            if spent_total >= campaign.budget_total:
                raise ValueError("Campaign has reached lifetime budget ceiling")
            if delta > Decimal("0") and new_budget > campaign.budget_total:
                raise ValueError("Proposed daily budget exceeds lifetime budget ceiling")

        campaign.budget_daily = new_budget
        return {
            "budget_before": float(old_budget),
            "budget_after": float(new_budget),
            "delta": float(delta),
        }

    def _apply_geo_action(
        self, campaign: Campaign, policy_result: OptimizationPolicyResult, target_payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Apply ADD-only geo targeting rule."""
        if policy_result.operation_type != "ADD":
            raise ValueError("Geo operations must be ADD-only")
            
        if not target_payload or "geo" not in target_payload:
            raise ValueError("No concrete geo target provided")

        geo_val = target_payload["geo"]
        if not geo_val or (isinstance(geo_val, str) and not geo_val.strip()):
            raise ValueError("No concrete geo target provided")

        rule = TargetingRule(
            campaign_id=campaign.id,
            rule_type="geo",
            is_include=True,
            rule_value=geo_val,
        )
        self.session.add(rule)
        return {"added_geo": geo_val}

    def _apply_sector_action(
        self, campaign: Campaign, policy_result: OptimizationPolicyResult, target_payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Apply ADD-only sector targeting rule."""
        if policy_result.operation_type != "ADD":
            raise ValueError("Sector operations must be ADD-only")
            
        if not target_payload or "sector" not in target_payload:
            raise ValueError("No concrete sector target provided")

        sector_val = target_payload["sector"]
        if not sector_val or (isinstance(sector_val, str) and not sector_val.strip()):
            raise ValueError("No concrete sector target provided")

        rule = TargetingRule(
            campaign_id=campaign.id,
            rule_type="sector",
            is_include=True,
            rule_value=sector_val,
        )
        self.session.add(rule)
        return {"added_sector": sector_val}

    async def _record_decision_audit(
        self,
        campaign: Optional[Campaign],
        policy_result: OptimizationPolicyResult,
        mode: str,
        outcome: str,
        reason: str | None = None,
        target_payload: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        change_applied: dict[str, Any] | None = None,
        actor: str = "system_optimizer",
        execution_status: str = "SUCCESS",
        campaign_id: Optional[int] = None,
        fencing_token: Optional[int] = None,
        auto_commit: bool = True,
    ) -> None:
        """Persist immutable audit record for the optimization decision."""
        try:
            cid = campaign_id
            if cid is None and campaign is not None:
                try:
                    cid = campaign.id
                except Exception:
                    cid = None
            if cid is None:
                return

            confidence_dec = (
                Decimal(str(round(policy_result.confidence, 4)))
                if policy_result.confidence is not None and math.isfinite(policy_result.confidence)
                else None
            )
            sanitized_evidence = None
            if evidence:
                sanitized_evidence = {
                    k: v for k, v in evidence.items()
                    if k not in ("secret", "token", "password", "key")
                }
            elif campaign:
                try:
                    sanitized_evidence = {
                        "impressions": getattr(campaign, "impressions", 0),
                        "clicks": getattr(campaign, "clicks", 0),
                        "spent_today": float(getattr(campaign, "spent_today", 0) or 0),
                        "spent_total": float(getattr(campaign, "spent_total", 0) or 0),
                        "daily_budget": float(getattr(campaign, "budget_daily", 0) or 0),
                    }
                except Exception:
                    sanitized_evidence = {}

            audit = OptimizationDecision(
                decision_id=str(uuid.uuid4()),
                campaign_id=cid,
                mode=mode,
                action=policy_result.action,
                confidence=confidence_dec,
                policy_version="v1.0",
                outcome=outcome,
                reason=reason or policy_result.reason,
                target_payload=target_payload,
                evidence=sanitized_evidence,
                change_applied=change_applied,
                actor=actor,
                execution_status=execution_status,
                fencing_token=fencing_token,
            )
            self.session.add(audit)
            if auto_commit:
                await self.session.commit()
        except Exception as e:
            logger.warning("Failed to persist optimization decision audit record", error=str(e))
