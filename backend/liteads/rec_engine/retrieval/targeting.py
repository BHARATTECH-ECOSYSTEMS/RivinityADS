"""
Targeting-based retrieval.

Retrieves ads based on targeting rules matching user attributes.
"""

from typing import Any
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.cache import CacheKeys, redis_client
from liteads.common.logger import get_logger
from liteads.common.utils import json_dumps, json_loads
from liteads.models import Campaign, Creative, Status, TargetingRule
from liteads.models.base import ModerationStatus
from liteads.rec_engine.retrieval.base import BaseRetrieval
from liteads.schemas.internal import AdCandidate, UserContext
from liteads.ad_server.services.targeting import match_geo, match_sector

logger = get_logger(__name__)


class TargetingRetrieval(BaseRetrieval):
    """
    Retrieval based on targeting rules.

    Matches user attributes against campaign targeting rules to find
    eligible ads.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self._cache_ttl = 300  # 5 minutes

    async def retrieve(
        self,
        user_context: UserContext,
        slot_id: str,
        limit: int = 100,
        **kwargs: Any,
    ) -> list[AdCandidate]:
        """
        Retrieve candidates matching targeting rules.

        Flow:
        1. Get the ad slot to determine required dimensions
        2. Get all active campaigns with creatives
        3. For each campaign, check targeting rules
        4. Filter creatives by slot dimensions
        5. Return matching candidates
        """
        # Resolve slot dimensions and format for size matching
        slot_width, slot_height, slot_format, slot_pub_id, is_chat_surface = await self._get_slot_info(slot_id)
        logger.debug(f"Resolved slot {slot_id} to pub {slot_pub_id} (w={slot_width} h={slot_height} chat={is_chat_surface})")

        # Get active campaigns (with caching)
        campaigns = await self._get_active_campaigns()

        if not campaigns:
            logger.debug("No active campaigns found")
            return []

        assigned_candidates: list[AdCandidate] = []
        unassigned_candidates: list[AdCandidate] = []

        for campaign_data in campaigns:
            # Determine if this campaign has assignments and whether it matches this slot
            assignments = campaign_data.get("assignments", [])
            is_explicitly_assigned = False
            if assignments and slot_pub_id is not None:
                matched = False
                for assignment in assignments:
                    if assignment["publisher_id"] == slot_pub_id:
                        # If assignment has no specific slot, or matches current slot exactly
                        if assignment["slot_id"] is None or assignment["slot_id"] == slot_id:
                            matched = True
                            break
                if not matched:
                    # Campaign has assignments but NOT to this publisher/slot — skip
                    continue
                is_explicitly_assigned = True
            elif assignments and slot_pub_id is None:
                # Campaign has assignments but we can't resolve slot publisher — skip
                continue

            # Evaluate each eligible ad set
            ad_sets = campaign_data.get("ad_sets", [])
            for ad_set_data in ad_sets:
                # Match targeting rules scoped strictly to this ad set
                if not self._match_targeting(ad_set_data, user_context):
                    continue

                targeted_sectors = []
                for rule in ad_set_data.get("targeting_rules", []):
                    if rule["rule_type"] == "sector" and rule["is_include"]:
                        if rule["rule_value"] and "entries" in rule["rule_value"]:
                            targeted_sectors.extend(rule["rule_value"]["entries"])
                        elif rule["rule_value"] and "sectors" in rule["rule_value"]:
                            targeted_sectors.extend(rule["rule_value"]["sectors"])

                for creative_data in ad_set_data.get("creatives", []):
                    # Format matching
                    c_format = creative_data.get("creative_type", 1)
                    if is_chat_surface and c_format == 4:
                        continue  # Interstitial format is forbidden on chat surfaces
                    if slot_format is not None and c_format != slot_format:
                        continue

                    # Size matching: skip creatives that don't fit the slot
                    if slot_width and slot_height:
                        c_w = creative_data.get("width", 0) or 0
                        c_h = creative_data.get("height", 0) or 0
                        # Creative 0x0 means "any size" (responsive)
                        if c_w != 0 and c_h != 0:
                            if c_w != slot_width or c_h != slot_height:
                                continue

                    candidate = AdCandidate(
                        campaign_id=campaign_data["id"],
                        ad_set_id=ad_set_data.get("id"),
                        creative_id=creative_data["id"],
                        advertiser_id=campaign_data["advertiser_id"],
                        bid=ad_set_data.get("bid_amount", campaign_data["bid_amount"]),
                        bid_type=ad_set_data.get("bid_type", campaign_data["bid_type"]),
                        title=creative_data.get("title"),
                        description=creative_data.get("description"),
                        image_url=creative_data.get("image_url"),
                        video_url=creative_data.get("video_url"),
                        landing_url=creative_data.get("landing_url", ""),
                        creative_type=creative_data.get("creative_type", 1),
                        width=creative_data.get("width"),
                        height=creative_data.get("height"),
                        advertiser_name=campaign_data.get("advertiser_name") or campaign_data.get("name"),
                        review_status="APPROVED",
                        metadata={
                            "sectors": targeted_sectors,
                            "freq_cap_daily": ad_set_data.get("freq_cap_daily"),
                            "freq_cap_hourly": ad_set_data.get("freq_cap_hourly"),
                            "ad_set_daily_budget": ad_set_data.get("daily_budget"),
                        },
                    )

                    if is_explicitly_assigned:
                        assigned_candidates.append(candidate)
                    else:
                        unassigned_candidates.append(candidate)

                    if len(assigned_candidates) + len(unassigned_candidates) >= limit:
                        break

                if len(assigned_candidates) + len(unassigned_candidates) >= limit:
                    break

            if len(assigned_candidates) + len(unassigned_candidates) >= limit:
                break

        # Prefer assigned campaigns; fall back to unassigned only if none are assigned
        if assigned_candidates:
            candidates = assigned_candidates
            logger.debug(
                f"Using {len(candidates)} assigned candidates (ignoring {len(unassigned_candidates)} unassigned)",
            )
        else:
            candidates = unassigned_candidates
            logger.debug(
                f"No assigned candidates; using {len(candidates)} unassigned candidates",
            )

        logger.debug(f"Retrieved {len(candidates)} candidates from targeting")
        return candidates

    async def _get_slot_info(self, slot_id: str) -> tuple[int, int, int | None, int | None, bool]:
        """Resolve an ad slot's width, height, format, publisher_id, and chat surface flag from the database."""
        try:
            from liteads.models import AdSlot
            result = await self.session.execute(
                select(AdSlot).where(AdSlot.slot_id == slot_id)
            )
            slot = result.scalar_one_or_none()
            if slot:
                return (slot.width or 0, slot.height or 0, slot.format, slot.publisher_id, bool(slot.is_chat_surface))
        except Exception as e:
            logger.error(f"Could not resolve slot info for {slot_id}: {e}", exc_info=True)
        return 0, 0, None, None, False

    async def _get_active_campaigns(self) -> list[dict[str, Any]]:
        """Get all active campaigns with ad sets, creatives, and targeting rules."""
        # Try cache first
        cache_key = CacheKeys.active_ads()
        cached = await redis_client.get(cache_key)

        if cached:
            try:
                return json_loads(cached)
            except Exception:
                pass

        # Query from database
        stmt = (
            select(Campaign)
            .where(Campaign.status == Status.ACTIVE)
            .limit(1000)
        )

        result = await self.session.execute(stmt)
        campaigns = result.scalars().all()

        campaign_list: list[dict[str, Any]] = []

        for campaign in campaigns:
            if campaign.updated_at.date() < datetime.now(timezone.utc).date():
                campaign.spent_today = 0

            if not campaign.is_active:
                continue

            adv_name = campaign.advertiser.name if getattr(campaign, "advertiser", None) else None
            campaign_data: dict[str, Any] = {
                "id": campaign.id,
                "advertiser_id": campaign.advertiser_id,
                "advertiser_name": adv_name,
                "name": campaign.name,
                "bid_type": campaign.bid_type,
                "bid_amount": float(campaign.bid_amount),
                "budget_daily": float(campaign.budget_daily) if campaign.budget_daily else None,
                "budget_total": float(campaign.budget_total) if campaign.budget_total else None,
                "spent_today": float(campaign.spent_today),
                "spent_total": float(campaign.spent_total),
                "freq_cap_daily": campaign.freq_cap_daily,
                "freq_cap_hourly": campaign.freq_cap_hourly,
                "ad_sets": [],
                "assignments": [],
            }

            # Add assignments
            for assignment in getattr(campaign, "assignments", []):
                campaign_data["assignments"].append({
                    "publisher_id": assignment.publisher_id,
                    "slot_id": assignment.slot_id,
                })

            ad_sets_data: list[dict[str, Any]] = []
            campaign_ad_sets = getattr(campaign, "ad_sets", [])
            if campaign_ad_sets:
                for ad_set in campaign_ad_sets:
                    if not ad_set.is_active or ad_set.status != Status.ACTIVE:
                        continue
                    set_creatives = [
                        {
                            "id": c.id,
                            "title": c.title,
                            "description": c.description,
                            "image_url": c.image_url,
                            "video_url": c.video_url,
                            "landing_url": c.landing_url,
                            "creative_type": c.creative_type,
                            "width": c.width,
                            "height": c.height,
                        }
                        for c in getattr(ad_set, "creatives", [])
                        if c.status == Status.ACTIVE and c.moderation_status == ModerationStatus.APPROVED.value
                    ]
                    set_rules = [
                        {
                            "rule_type": r.rule_type,
                            "rule_value": r.rule_value,
                            "is_include": r.is_include,
                        }
                        for r in getattr(ad_set, "targeting_rules", [])
                    ]
                    if set_creatives:
                        ad_sets_data.append({
                            "id": ad_set.id,
                            "name": ad_set.name,
                            "bid_type": ad_set.bid_type,
                            "bid_amount": float(ad_set.bid_amount) if ad_set.bid_amount and float(ad_set.bid_amount) > 0 else float(campaign.bid_amount),
                            "daily_budget": float(ad_set.daily_budget) if ad_set.daily_budget is not None else None,
                            "freq_cap_daily": ad_set.freq_cap_daily if ad_set.freq_cap_daily is not None else campaign.freq_cap_daily,
                            "freq_cap_hourly": ad_set.freq_cap_hourly if ad_set.freq_cap_hourly is not None else campaign.freq_cap_hourly,
                            "targeting_rules": set_rules,
                            "creatives": set_creatives,
                        })
            else:
                # Backward compatibility fallback if campaign has no ad_sets attached
                legacy_creatives = [
                    {
                        "id": creative.id,
                        "title": creative.title,
                        "description": creative.description,
                        "image_url": creative.image_url,
                        "video_url": creative.video_url,
                        "landing_url": creative.landing_url,
                        "creative_type": creative.creative_type,
                        "width": creative.width,
                        "height": creative.height,
                    }
                    for creative in getattr(campaign, "creatives", [])
                    if creative.status == Status.ACTIVE and creative.moderation_status == ModerationStatus.APPROVED.value
                ]
                legacy_rules = [
                    {
                        "rule_type": rule.rule_type,
                        "rule_value": rule.rule_value,
                        "is_include": rule.is_include,
                    }
                    for rule in getattr(campaign, "targeting_rules", [])
                ]
                if legacy_creatives:
                    ad_sets_data.append({
                        "id": None,
                        "name": "Default Ad Set",
                        "bid_type": campaign.bid_type,
                        "bid_amount": float(campaign.bid_amount),
                        "daily_budget": float(campaign.budget_daily) if campaign.budget_daily else None,
                        "freq_cap_daily": campaign.freq_cap_daily,
                        "freq_cap_hourly": campaign.freq_cap_hourly,
                        "targeting_rules": legacy_rules,
                        "creatives": legacy_creatives,
                    })

            campaign_data["ad_sets"] = ad_sets_data
            if ad_sets_data:
                campaign_list.append(campaign_data)

        # Cache the result
        if campaign_list:
            await redis_client.set(
                cache_key,
                json_dumps(campaign_list),
                ttl=self._cache_ttl,
            )

        return campaign_list

    def _match_targeting(
        self,
        campaign_data: dict[str, Any],
        user_context: UserContext,
    ) -> bool:
        """Check if user matches campaign targeting rules."""
        targeting_rules = campaign_data.get("targeting_rules", [])

        if not targeting_rules:
            return True  # No targeting = match all

        for rule in targeting_rules:
            rule_type = rule["rule_type"]
            rule_value = rule["rule_value"]
            is_include = rule["is_include"]

            matched = self._match_rule(rule_type, rule_value, user_context)

            # Include rule: must match
            # Exclude rule: must not match
            if is_include and not matched:
                return False
            if not is_include and matched:
                return False

        return True

    def _match_rule(
        self,
        rule_type: str,
        rule_value: dict[str, Any],
        user_context: UserContext,
    ) -> bool:
        """Match a single targeting rule against user context."""
        if rule_type == "age":
            if user_context.age is None:
                return True  # Unknown age matches
            min_age = rule_value.get("min", 0)
            max_age = rule_value.get("max", 999)
            return min_age <= user_context.age <= max_age

        elif rule_type == "gender":
            if user_context.gender is None:
                return True
            values = rule_value.get("values", [])
            return user_context.gender.lower() in [v.lower() for v in values]

        elif rule_type == "geo":
            return match_geo(rule_value, user_context)

        elif rule_type == "sector":
            return match_sector(rule_value, user_context)

        elif rule_type == "device":
            device_types = rule_value.get("types", [])
            # Simplified device type detection
            if user_context.device_model:
                model_lower = user_context.device_model.lower()
                if "tablet" in model_lower or "pad" in model_lower:
                    device_type = "tablet"
                else:
                    device_type = "phone"

                if device_types and device_type not in device_types:
                    return False

            return True

        elif rule_type == "os":
            os_values = rule_value.get("values", [])
            if os_values and user_context.os:
                if user_context.os.lower() not in [v.lower() for v in os_values]:
                    return False
            return True

        elif rule_type == "interest":
            interests = rule_value.get("values", [])
            if interests and user_context.interests:
                # Match if user has any of the target interests
                user_interests_lower = [i.lower() for i in user_context.interests]
                target_interests_lower = [i.lower() for i in interests]
                if not any(i in user_interests_lower for i in target_interests_lower):
                    return False
            return True

        elif rule_type == "app_category":
            categories = rule_value.get("values", [])
            if categories and user_context.app_categories:
                user_cats_lower = [c.lower() for c in user_context.app_categories]
                target_cats_lower = [c.lower() for c in categories]
                if not any(c in user_cats_lower for c in target_cats_lower):
                    return False
            return True

        # Unknown rule type - default match
        return True

    async def refresh(self) -> None:
        """Clear cache to force refresh."""
        cache_key = CacheKeys.active_ads()
        await redis_client.delete(cache_key)
        logger.info("Targeting retrieval cache refreshed")
