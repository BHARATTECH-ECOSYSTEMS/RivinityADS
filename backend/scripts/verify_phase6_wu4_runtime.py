"""Live runtime verification script for Phase 6 Work Unit 4 (WU4)
Placement-Aware Laya Optimizer Integration.
Executes end-to-end against live PostgreSQL and Redis.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
import sys
import uuid

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import delete, select, text

from liteads.ad_server.services.laya_client import LayaDecisionResult
from liteads.ad_server.services.optimization_policy import OptimizationPolicyService
from liteads.ad_server.services.optimization_prep import (
    OptimizationPreparationService,
    OptimizationPrepResult,
)
from liteads.ad_server.services.placement_analyzer import (
    PlacementAnalyzerService,
    VERDICT_GREAT,
    VERDICT_HEALTHY,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_LOW_VISIBILITY,
)
from liteads.common.cache import redis_client
from liteads.common.database import db, init_db
from liteads.models.ad import Advertiser, Campaign, CampaignAssignment, Creative, TargetingRule
from liteads.models.auth import User
from liteads.models.base import CreativeType, ModerationStatus, Status
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat


async def run_live_verification():
    print("=" * 70)
    print("STARTING LIVE RUNTIME VERIFICATION FOR PHASE 6 WU4 (PLACEMENT-AWARE OPTIMIZER)")
    print("=" * 70)

    # 1. Connect to live DB & Redis
    await init_db()
    await redis_client.connect()
    print("[OK] PostgreSQL and Redis connected successfully")

    test_suffix = uuid.uuid4().hex[:8]
    created_user_ids = []
    created_pub_ids = []
    created_adv_ids = []
    created_camp_ids = []
    created_slot_ids = []

    async with db.session() as session:
        try:
            print("\n--- STEP 1: CREATE LIVE TENANTS & PLACEMENTS ---")
            pub_user = User(
                name=f"wu4_pub_user_{test_suffix}",
                email=f"wu4_pub_{test_suffix}@example.com",
                password_hash="test_hash",
                role="publisher",
                is_active=True,
                email_verified=True,
            )
            session.add(pub_user)
            await session.flush()
            created_user_ids.append(pub_user.id)

            publisher = Publisher(
                name=f"WU4 Live Publisher {test_suffix}",
                email=f"pub_{test_suffix}@example.com",
                status=Status.ACTIVE,
                owner_user_id=pub_user.id,
                balance=Decimal("500.00"),
            )
            session.add(publisher)
            await session.flush()
            created_pub_ids.append(publisher.id)

            # Slots:
            # Slot 1: High CTR (> 1.0%) -> Great
            # Slot 2: Normal CTR (0.5%) -> Healthy
            # Slot 3: Low CTR (0.1%) -> Low Visibility
            # Slot 4: Low impressions (< 100) -> Insufficient Data
            slot_great = AdSlot(
                publisher_id=publisher.id,
                slot_id=f"slot_great_{test_suffix}",
                name="Top Banner Slot",
                format=CreativeType.BANNER,
                width=728,
                height=90,
                status=Status.ACTIVE,
            )
            slot_healthy = AdSlot(
                publisher_id=publisher.id,
                slot_id=f"slot_healthy_{test_suffix}",
                name="Sidebar Banner Slot",
                format=CreativeType.BANNER,
                width=300,
                height=250,
                status=Status.ACTIVE,
            )
            slot_low_vis = AdSlot(
                publisher_id=publisher.id,
                slot_id=f"slot_low_{test_suffix}",
                name="Footer Slot",
                format=CreativeType.BANNER,
                width=728,
                height=90,
                status=Status.ACTIVE,
            )
            slot_nodata = AdSlot(
                publisher_id=publisher.id,
                slot_id=f"slot_nodata_{test_suffix}",
                name="New Slot",
                format=CreativeType.BANNER,
                width=300,
                height=250,
                status=Status.ACTIVE,
            )
            session.add_all([slot_great, slot_healthy, slot_low_vis, slot_nodata])
            await session.flush()
            created_slot_ids.extend([slot_great.id, slot_healthy.id, slot_low_vis.id, slot_nodata.id])

            today = date.today()
            # Seed statistics:
            # slot_great: 1000 imp, 25 clicks (2.5% CTR) -> Great
            session.add(
                PublisherDailyStat(
                    publisher_id=publisher.id,
                    slot_id=slot_great.id,
                    stat_date=today - timedelta(days=2),
                    impressions=1000,
                    clicks=25,
                    conversions=3,
                    earnings=Decimal("50.00"),
                )
            )
            # slot_healthy: 1000 imp, 5 clicks (0.5% CTR) -> Healthy
            session.add(
                PublisherDailyStat(
                    publisher_id=publisher.id,
                    slot_id=slot_healthy.id,
                    stat_date=today - timedelta(days=2),
                    impressions=1000,
                    clicks=5,
                    conversions=1,
                    earnings=Decimal("15.00"),
                )
            )
            # slot_low_vis: 1000 imp, 1 click (0.1% CTR) -> Low Visibility
            session.add(
                PublisherDailyStat(
                    publisher_id=publisher.id,
                    slot_id=slot_low_vis.id,
                    stat_date=today - timedelta(days=2),
                    impressions=1000,
                    clicks=1,
                    conversions=0,
                    earnings=Decimal("2.00"),
                )
            )
            # slot_nodata: 50 imp (< 100) -> Insufficient Data
            session.add(
                PublisherDailyStat(
                    publisher_id=publisher.id,
                    slot_id=slot_nodata.id,
                    stat_date=today - timedelta(days=2),
                    impressions=50,
                    clicks=2,
                    conversions=0,
                    earnings=Decimal("1.00"),
                )
            )
            await session.commit()
            print(f"[OK] Seeded live publisher {publisher.id} and 4 placements")

            print("\n--- STEP 2: CREATE LIVE ADVERTISER & CAMPAIGNS ---")
            adv = Advertiser(
                name=f"WU4 Live Advertiser {test_suffix}",
                contact_email=f"adv_{test_suffix}@example.com",
                status=Status.ACTIVE,
                balance=Decimal("250.00"),
                daily_budget=Decimal("100.00"),
            )
            session.add(adv)
            await session.flush()
            created_adv_ids.append(adv.id)

            camp = Campaign(
                advertiser_id=adv.id,
                name=f"WU4 Active Campaign {test_suffix}",
                status=Status.ACTIVE,
                start_time=datetime.now() - timedelta(days=1),
                end_time=datetime.now() + timedelta(days=5),
                impressions=1500,
                clicks=75,
                conversions=8,
                spent_today=Decimal("25.00"),
                spent_total=Decimal("125.00"),
                budget_daily=Decimal("100.00"),
                budget_total=Decimal("1000.00"),
                bid_amount=Decimal("1.50"),
                autopilot=False,
            )
            session.add(camp)
            await session.flush()
            created_camp_ids.append(camp.id)

            # Assign campaign to publisher
            assignment = CampaignAssignment(
                campaign_id=camp.id,
                publisher_id=publisher.id,
                slot_id=None, # authorized for all slots of this publisher
            )
            session.add(assignment)

            # Active banner creative
            creative = Creative(
                campaign_id=camp.id,
                title="Live Banner Creative",
                landing_url="https://example.com/live",
                creative_type=CreativeType.BANNER,
                status=Status.ACTIVE,
                moderation_status=ModerationStatus.APPROVED,
            )
            session.add(creative)
            await session.commit()
            print(f"[OK] Seeded live campaign {camp.id} assigned to publisher {publisher.id}")

            print("\n--- STEP 3: RUN LIVE PLACEMENT ANALYZER & PREPARATION SERVICE ---")
            analyzer = PlacementAnalyzerService(session)
            analyses = await analyzer.analyze_publisher_placements(publisher.id)
            print(f"[OK] Analyzed {len(analyses)} placements for publisher {publisher.id}")
            for a in analyses:
                print(f"     Slot '{a.slot_id}': verdict={a.verdict}, ctr={a.ctr:.4f}, ecpm={a.ecpm:.2f}")

            # Verify verdicts
            verdict_map = {a.slot_id: a.verdict for a in analyses}
            assert verdict_map[slot_great.slot_id] == VERDICT_GREAT
            assert verdict_map[slot_healthy.slot_id] == VERDICT_HEALTHY
            assert verdict_map[slot_low_vis.slot_id] == VERDICT_LOW_VISIBILITY
            assert verdict_map[slot_nodata.slot_id] == VERDICT_INSUFFICIENT_DATA
            print("[OK] Verified all 4 distinct placement health verdicts against live DB")

            prep_service = OptimizationPreparationService()
            eligible_placements = await prep_service.get_campaign_eligible_placements(session, camp)
            assert len(eligible_placements) == 4
            print(f"[OK] get_campaign_eligible_placements resolved {len(eligible_placements)} slots for campaign")

            # Prepare context with placements
            prep_result = prep_service.prepare_context(camp, placements=eligible_placements)
            assert prep_result.eligible is True
            state_dict = json.loads(prep_result.state)

            assert "top_performing_placements" in state_dict
            top_placements = state_dict["top_performing_placements"]
            print(f"[OK] top_performing_placements present in state dict: count={len(top_placements)}")
            # Only slot_great should be in top_performing_placements (verdict == "great")
            assert len(top_placements) == 1
            assert top_placements[0]["slot_id"] == slot_great.slot_id
            assert top_placements[0]["verdict"] == VERDICT_GREAT
            # Verify sensitive publisher / financial information is stripped
            assert "publisher_id" not in top_placements[0]
            assert "earnings" not in top_placements[0]
            assert "benchmark" not in top_placements[0]
            print("[OK] Confirmed sensitive competitor/publisher data completely stripped")

            print("\n--- STEP 4: VERIFY ADVISORY DESTINATION PREFERENCE IN OPTIMIZER POLICY ---")
            policy_service = OptimizationPolicyService()

            # Multiple candidates including great and non-great
            candidates = [slot_healthy.slot_id, slot_great.slot_id, slot_low_vis.slot_id]
            chosen_dest = policy_service.select_preferred_shift_destination(
                campaign=camp,
                candidate_destinations=candidates,
                placements=eligible_placements,
                advertiser_balance=adv.balance,
                verified_domain=True,
            )
            assert chosen_dest == slot_great.slot_id
            print(f"[OK] Successfully preferred great placement: '{chosen_dest}'")

            # Non-great placements alone do not receive great preference
            non_great_candidates = [slot_low_vis.slot_id, slot_healthy.slot_id]
            chosen_fallback = policy_service.select_preferred_shift_destination(
                campaign=camp,
                candidate_destinations=non_great_candidates,
                placements=eligible_placements,
                advertiser_balance=adv.balance,
                verified_domain=True,
            )
            # Default deterministic first candidate preserved
            assert chosen_fallback == slot_low_vis.slot_id
            print(f"[OK] Non-great candidates do not receive great preference (first candidate preserved: '{chosen_fallback}')")

            print("\n--- STEP 5: VERIFY GUARDRAILS ENFORCEMENT ---")
            # 1. Zero balance rejects shift destination
            dest_zero_bal = policy_service.select_preferred_shift_destination(
                campaign=camp,
                candidate_destinations=candidates,
                placements=eligible_placements,
                advertiser_balance=Decimal("0.00"),
                verified_domain=True,
            )
            assert dest_zero_bal is None
            print("[OK] Zero advertiser balance strictly rejects destination selection")

            # 2. Inactive campaign rejects
            camp.status = Status.PAUSED
            dest_inactive = policy_service.select_preferred_shift_destination(
                campaign=camp,
                candidate_destinations=candidates,
                placements=eligible_placements,
            )
            assert dest_inactive is None
            print("[OK] Inactive campaign strictly rejects destination selection")

            # 3. Low impressions (< 500) rejects
            camp.status = Status.ACTIVE
            camp.impressions = 350
            dest_low_imp = policy_service.select_preferred_shift_destination(
                campaign=camp,
                candidate_destinations=candidates,
                placements=eligible_placements,
            )
            assert dest_low_imp is None
            print("[OK] Under 500 impressions strictly rejects destination selection")

            # 4. Empty candidates never invents an action
            dest_empty = policy_service.select_preferred_shift_destination(
                campaign=camp,
                candidate_destinations=[],
                placements=eligible_placements,
            )
            assert dest_empty is None
            print("[OK] Empty candidate list never invents a destination")

            # 5. Existing Phase 2 behavior preserved when placement context is absent
            camp.impressions = 1500
            legacy_context = prep_service.prepare_context(camp, placements=None)
            legacy_dict = json.loads(legacy_context.state)
            assert set(legacy_dict.keys()) == {"performance", "targeting"}
            assert "top_performing_placements" not in legacy_dict
            print("[OK] Confirmed Phase 2 behavior completely unchanged when placement context is absent")

            print("\n" + "=" * 70)
            print("LIVE RUNTIME VERIFICATION FOR PHASE 6 WU4 SUCCEEDED 100%!")
            print("=" * 70)

        finally:
            print("\n--- CLEANUP SEEDED TEST ENTITIES ---")
            await session.rollback()
            for sid in created_slot_ids:
                await session.execute(delete(PublisherDailyStat).where(PublisherDailyStat.slot_id == sid))
                await session.execute(delete(AdSlot).where(AdSlot.id == sid))
            for cid in created_camp_ids:
                await session.execute(delete(CampaignAssignment).where(CampaignAssignment.campaign_id == cid))
                await session.execute(delete(Creative).where(Creative.campaign_id == cid))
                await session.execute(delete(Campaign).where(Campaign.id == cid))
            for aid in created_adv_ids:
                await session.execute(delete(Advertiser).where(Advertiser.id == aid))
            for pid in created_pub_ids:
                await session.execute(delete(Publisher).where(Publisher.id == pid))
            for uid in created_user_ids:
                await session.execute(delete(User).where(User.id == uid))
            await session.commit()
            print("[OK] Live test cleanup completed cleanly")


if __name__ == "__main__":
    asyncio.run(run_live_verification())
