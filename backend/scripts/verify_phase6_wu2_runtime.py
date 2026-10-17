"""Real runtime verification script for Phase 6 Work Unit 2 (WU2) - Placement Analyzer.
Executes end-to-end against live PostgreSQL.
"""

import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select, text

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from liteads.ad_server.services.placement_analyzer import (
    PlacementAnalyzerService,
    FormatBenchmark,
    PlacementAnalysisResult,
)
from liteads.common.database import db, init_db
from liteads.models.auth import User
from liteads.models.base import CreativeType, Status
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat


async def run_live_verification():
    print("=" * 70)
    print("STARTING LIVE RUNTIME VERIFICATION FOR PHASE 6 WU2 (PLACEMENT ANALYZER)")
    print("=" * 70)

    # 1. Connect to live DB
    await init_db()
    print("[OK] PostgreSQL connection initialized")

    test_suffix = uuid.uuid4().hex[:8]
    created_user_ids = []
    created_pub_ids = []
    created_slot_ids = []

    async with db.session() as session:
        try:
            print("\n--- STEP 1: CREATE TEST TENANTS & PLACEMENTS ---")
            # Create two separate publishers for tenant isolation verification
            user_a = User(
                name=f"pub_a_{test_suffix}",
                email=f"pub_a_{test_suffix}@example.com",
                password_hash="test_hash",
                role="publisher",
                is_active=True,
            )
            user_b = User(
                name=f"pub_b_{test_suffix}",
                email=f"pub_b_{test_suffix}@example.com",
                password_hash="test_hash",
                role="publisher",
                is_active=True,
            )
            session.add_all([user_a, user_b])
            await session.flush()
            created_user_ids.extend([user_a.id, user_b.id])

            pub_a = Publisher(
                owner_user_id=user_a.id,
                name=f"Publisher Alpha {test_suffix}",
                site_url=f"https://alpha-{test_suffix}.com",
                status=Status.ACTIVE,
            )
            pub_b = Publisher(
                owner_user_id=user_b.id,
                name=f"Publisher Beta {test_suffix}",
                site_url=f"https://beta-{test_suffix}.com",
                status=Status.ACTIVE,
            )
            session.add_all([pub_a, pub_b])
            await session.flush()
            created_pub_ids.extend([pub_a.id, pub_b.id])

            # Slots for Publisher A:
            # Slot A1: Banner, 1000 impr, 25 clicks (CTR=2.5% -> great), earnings 5.0 (eCPM=$5.00)
            slot_a1 = AdSlot(
                publisher_id=pub_a.id,
                slot_id=f"slot_{test_suffix}_a1",
                name=f"Header Banner {test_suffix}",
                format=CreativeType.BANNER,
                width=728,
                height=90,
                status=Status.ACTIVE,
            )
            # Slot A2: Banner, 99 impr (insufficient_data)
            slot_a2 = AdSlot(
                publisher_id=pub_a.id,
                slot_id=f"slot_{test_suffix}_a2",
                name=f"Sidebar Banner {test_suffix}",
                format=CreativeType.BANNER,
                width=300,
                height=250,
                status=Status.ACTIVE,
            )
            # Slot A3: Banner, 500 impr, 0 clicks (CTR=0.0% -> low_visibility)
            slot_a3 = AdSlot(
                publisher_id=pub_a.id,
                slot_id=f"slot_{test_suffix}_a3",
                name=f"Footer Banner {test_suffix}",
                format=CreativeType.BANNER,
                width=728,
                height=90,
                status=Status.ACTIVE,
            )
            # Slot A4: Video, 200 impr, 1 click (CTR=0.5% -> healthy)
            slot_a4 = AdSlot(
                publisher_id=pub_a.id,
                slot_id=f"slot_{test_suffix}_a4",
                name=f"In-Stream Video {test_suffix}",
                format=CreativeType.VIDEO,
                width=640,
                height=360,
                status=Status.ACTIVE,
            )

            # Slots for Publisher B (used for benchmark calculation and privacy check):
            # Slot B1: Banner, 1000 impr, 15 clicks (CTR=1.5%), earnings 3.0 (eCPM=$3.00)
            slot_b1 = AdSlot(
                publisher_id=pub_b.id,
                slot_id=f"slot_{test_suffix}_b1",
                name=f"Beta Top Banner {test_suffix}",
                format=CreativeType.BANNER,
                width=728,
                height=90,
                status=Status.ACTIVE,
            )
            # Slot B2: Banner, 1000 impr, 5 clicks (CTR=0.5%), earnings 1.0 (eCPM=$1.00)
            slot_b2 = AdSlot(
                publisher_id=pub_b.id,
                slot_id=f"slot_{test_suffix}_b2",
                name=f"Beta Side Banner {test_suffix}",
                format=CreativeType.BANNER,
                width=300,
                height=250,
                status=Status.ACTIVE,
            )
            # Slot B3: Video, 500 impr, 10 clicks (CTR=2.0%), earnings 5.0 (eCPM=$10.00)
            slot_b3 = AdSlot(
                publisher_id=pub_b.id,
                slot_id=f"slot_{test_suffix}_b3",
                name=f"Beta PreRoll Video {test_suffix}",
                format=CreativeType.VIDEO,
                width=640,
                height=360,
                status=Status.ACTIVE,
            )

            session.add_all([slot_a1, slot_a2, slot_a3, slot_a4, slot_b1, slot_b2, slot_b3])
            await session.flush()
            created_slot_ids.extend([s.id for s in [slot_a1, slot_a2, slot_a3, slot_a4, slot_b1, slot_b2, slot_b3]])
            print(f"[OK] Created 2 publishers and 7 ad slots successfully")

            print("\n--- STEP 2: INSERT DAILY STATS (ROLLING 30-DAY WINDOW) ---")
            today = date.today()
            day_5_ago = today - timedelta(days=5)
            day_35_ago = today - timedelta(days=35)  # Outside 30-day window

            # Slot A1: 1000 impr, 25 clicks, $5.00 earnings (inside window)
            # Plus 5000 impressions on day_35_ago (must be excluded from rolling window)
            stats = [
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a1.id,
                    stat_date=day_5_ago,
                    impressions=1000,
                    clicks=25,
                    conversions=2,
                    earnings=Decimal("5.000000"),
                ),
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a1.id,
                    stat_date=day_35_ago,
                    impressions=5000,
                    clicks=200,
                    conversions=20,
                    earnings=Decimal("50.000000"),
                ),
                # Slot A2: exactly 99 impr (insufficient_data test)
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a2.id,
                    stat_date=day_5_ago,
                    impressions=99,
                    clicks=10,
                    conversions=1,
                    earnings=Decimal("1.500000"),
                ),
                # Slot A3: 500 impr, 0 clicks (CTR=0.0% < 0.002 -> low_visibility)
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a3.id,
                    stat_date=day_5_ago,
                    impressions=500,
                    clicks=0,
                    conversions=0,
                    earnings=Decimal("0.500000"),
                ),
                # Slot A4: 200 impr, 1 click (CTR=0.5% -> healthy)
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a4.id,
                    stat_date=day_5_ago,
                    impressions=200,
                    clicks=1,
                    conversions=0,
                    earnings=Decimal("1.000000"),
                ),
                # Slot B1: 1000 impr, 15 clicks (CTR=1.5%), earnings 3.0 (eCPM=$3.00)
                PublisherDailyStat(
                    publisher_id=pub_b.id,
                    slot_id=slot_b1.id,
                    stat_date=day_5_ago,
                    impressions=1000,
                    clicks=15,
                    conversions=1,
                    earnings=Decimal("3.000000"),
                ),
                # Slot B2: 1000 impr, 5 clicks (CTR=0.5%), earnings 1.0 (eCPM=$1.00)
                PublisherDailyStat(
                    publisher_id=pub_b.id,
                    slot_id=slot_b2.id,
                    stat_date=day_5_ago,
                    impressions=1000,
                    clicks=5,
                    conversions=0,
                    earnings=Decimal("1.000000"),
                ),
                # Slot B3: 500 impr, 10 clicks (CTR=2.0%), earnings 5.0 (eCPM=$10.00)
                PublisherDailyStat(
                    publisher_id=pub_b.id,
                    slot_id=slot_b3.id,
                    stat_date=day_5_ago,
                    impressions=500,
                    clicks=10,
                    conversions=2,
                    earnings=Decimal("5.000000"),
                ),
                # Legacy NULL slot row for pub_a (must not be attributed to any slot)
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=None,
                    stat_date=day_5_ago,
                    impressions=9999,
                    clicks=999,
                    conversions=99,
                    earnings=Decimal("999.000000"),
                ),
            ]
            session.add_all(stats)
            await session.commit()
            print(f"[OK] Inserted test stats including rolling-window and legacy NULL-slot data")

            print("\n--- STEP 3: EXECUTE PLACEMENT ANALYZER SERVICE ---")
            analyzer = PlacementAnalyzerService(session)

            # A. Platform benchmarks
            benchmarks = await analyzer.calculate_platform_benchmarks(window_days=30, as_of_date=today)
            print("Calculated Platform Benchmarks:")
            for fmt_name, b_val in benchmarks.items():
                print(f"  Format: {fmt_name} -> Eligible Slots: {b_val.eligible_slots_count}, Median CTR: {b_val.median_ctr:.4f}, Median eCPM: ${b_val.median_ecpm:.2f}")

            banner_bm = benchmarks.get(int(CreativeType.BANNER))
            assert banner_bm is not None, "BANNER benchmark must exist"
            assert banner_bm.eligible_slots_count >= 4, f"At least 4 banner slots must be eligible (A1, A3, B1, B2), got {banner_bm.eligible_slots_count}"
            print("[OK] Platform benchmark calculation passed")

            # B. Analyze Publisher A Placements
            pub_a_results = await analyzer.analyze_publisher_placements(pub_a.id, window_days=30, as_of_date=today)
            print(f"\nPublisher A Analysis Results ({len(pub_a_results)} placements):")
            res_by_name = {r.slot_name: r for r in pub_a_results}

            # Verify Slot A1 (Header Banner)
            r_a1 = res_by_name[f"Header Banner {test_suffix}"]
            print(f"  Slot A1: Impr={r_a1.impressions}, Clicks={r_a1.clicks}, CTR={r_a1.ctr:.4f}, eCPM=${r_a1.ecpm:.2f}, Verdict={r_a1.verdict}, Rec='{r_a1.recommendation}'")
            assert r_a1.impressions == 1000, f"Expected 1000 impr (excluding 35-day-ago 5000), got {r_a1.impressions}"
            assert r_a1.clicks == 25, f"Expected 25 clicks, got {r_a1.clicks}"
            assert abs(r_a1.ctr - 0.025) < 1e-6, f"Expected CTR 0.025, got {r_a1.ctr}"
            assert abs(r_a1.ecpm - 5.0) < 1e-6, f"Expected eCPM 5.0, got {r_a1.ecpm}"
            assert r_a1.verdict == "great", f"Expected verdict 'great', got '{r_a1.verdict}'"
            assert r_a1.recommendation == "Raise floor price — demand exceeds supply"
            print("  [OK] Slot A1: 30-day window bounded correctly, verdict='great', recommendation verified")

            # Verify Slot A2 (Sidebar Banner - 99 impressions)
            r_a2 = res_by_name[f"Sidebar Banner {test_suffix}"]
            print(f"  Slot A2: Impr={r_a2.impressions}, Verdict={r_a2.verdict}, Rec='{r_a2.recommendation}'")
            assert r_a2.impressions == 99, f"Expected 99 impr, got {r_a2.impressions}"
            assert r_a2.verdict == "insufficient_data", f"Expected 'insufficient_data' for 99 impr, got '{r_a2.verdict}'"
            print("  [OK] Slot A2: 99-impression boundary correctly gives 'insufficient_data'")

            # Verify Slot A3 (Footer Banner - 500 impressions, 0 clicks)
            r_a3 = res_by_name[f"Footer Banner {test_suffix}"]
            print(f"  Slot A3: Impr={r_a3.impressions}, CTR={r_a3.ctr:.4f}, Verdict={r_a3.verdict}, Rec='{r_a3.recommendation}'")
            assert r_a3.impressions == 500
            assert r_a3.verdict == "low_visibility", f"Expected 'low_visibility', got '{r_a3.verdict}'"
            assert r_a3.recommendation == "Move slot above the fold or widen it"
            print("  [OK] Slot A3: low CTR correctly classified as 'low_visibility'")

            # Verify Slot A4 (Video - 200 impressions, 1 click -> CTR 0.5%)
            r_a4 = res_by_name[f"In-Stream Video {test_suffix}"]
            print(f"  Slot A4: Impr={r_a4.impressions}, CTR={r_a4.ctr:.4f}, Verdict={r_a4.verdict}, Rec='{r_a4.recommendation}'")
            assert r_a4.impressions == 200
            assert r_a4.verdict == "healthy", f"Expected 'healthy', got '{r_a4.verdict}'"
            print("  [OK] Slot A4: healthy classification verified")

            # Verify Tenant Isolation & Data Privacy
            for r in pub_a_results:
                assert r.publisher_id == pub_a.id, "All results must belong to Publisher A"
                # Ensure no Publisher B data is in placement result
                assert f"Beta" not in r.slot_name
                # Ensure benchmark only contains aggregate numbers
                assert isinstance(r.benchmark, dict)
                assert "eligible_slots_count" in r.benchmark
            print("[OK] Tenant isolation and data privacy verified: zero cross-publisher leakage")

            # Verify Legacy NULL-slot rows are not attributed
            total_analyzed_impr = sum(r.impressions for r in pub_a_results)
            assert total_analyzed_impr == 1000 + 99 + 500 + 200, f"Expected 1799 total impr, got {total_analyzed_impr} (legacy 9999 was properly excluded)"
            print("[OK] Legacy NULL-slot row was not attributed to any placement")

        finally:
            print("\n--- STEP 4: CLEANUP TEST DATA ---")
            await session.rollback()
            # Clean up test stats, slots, publishers, users
            if created_slot_ids:
                await session.execute(delete(PublisherDailyStat).where(PublisherDailyStat.slot_id.in_(created_slot_ids)))
            if created_pub_ids:
                await session.execute(delete(PublisherDailyStat).where(PublisherDailyStat.publisher_id.in_(created_pub_ids)))
                await session.execute(delete(AdSlot).where(AdSlot.publisher_id.in_(created_pub_ids)))
                await session.execute(delete(Publisher).where(Publisher.id.in_(created_pub_ids)))
            if created_user_ids:
                await session.execute(delete(User).where(User.id.in_(created_user_ids)))
            await session.commit()
            print("[OK] Test data cleanly removed from PostgreSQL")

    print("\n" + "=" * 70)
    print("LIVE RUNTIME VERIFICATION FOR PHASE 6 WU2 COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_live_verification())
