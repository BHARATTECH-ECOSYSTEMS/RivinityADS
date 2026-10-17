"""Live runtime verification script for Phase 6 Work Unit 3 (WU3) - Placement Health API.
Executes end-to-end against live PostgreSQL and FastAPI ASGI application.
"""

import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from liteads.ad_server.main import app
from liteads.common.cache import redis_client
from liteads.common.database import db, init_db
from liteads.common.security import create_access_token
from liteads.models.auth import User
from liteads.models.base import CreativeType, Status
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat


async def run_live_verification():
    print("=" * 70)
    print("STARTING LIVE RUNTIME VERIFICATION FOR PHASE 6 WU3 (PLACEMENT HEALTH API)")
    print("=" * 70)

    # 1. Connect to live DB & Redis
    await init_db()
    await redis_client.connect()
    print("[OK] PostgreSQL and Redis connected successfully")

    test_suffix = uuid.uuid4().hex[:8]
    created_user_ids = []
    created_pub_ids = []
    created_slot_ids = []

    async with db.session() as session:
        try:
            print("\n--- STEP 1: CREATE LIVE TENANTS & PLACEMENTS ---")
            user_a = User(
                name=f"pub_a_{test_suffix}",
                email=f"pub_a_{test_suffix}@example.com",
                password_hash="test_hash",
                role="publisher",
                is_active=True,
                email_verified=True,
            )
            user_b = User(
                name=f"pub_b_{test_suffix}",
                email=f"pub_b_{test_suffix}@example.com",
                password_hash="test_hash",
                role="publisher",
                is_active=True,
                email_verified=True,
            )
            # In PostgreSQL there can only be one admin user (uq_single_admin_role)
            admin_res = await session.execute(select(User).where(User.role == "admin"))
            admin_user = admin_res.scalar_one_or_none()
            if admin_user is None:
                admin_user = User(
                    name=f"admin_{test_suffix}",
                    email=f"admin_{test_suffix}@example.com",
                    password_hash="test_hash",
                    role="admin",
                    is_active=True,
                    email_verified=True,
                )
                session.add(admin_user)
                await session.flush()
                created_user_ids.append(admin_user.id)

            session.add_all([user_a, user_b])
            await session.flush()
            created_user_ids.extend([user_a.id, user_b.id])

            pub_a = Publisher(
                owner_user_id=user_a.id,
                name=f"Alpha Publishing {test_suffix}",
                site_url=f"https://alpha-{test_suffix}.com",
                status=Status.ACTIVE,
            )
            pub_b = Publisher(
                owner_user_id=user_b.id,
                name=f"Beta Media {test_suffix}",
                site_url=f"https://beta-{test_suffix}.com",
                status=Status.ACTIVE,
            )
            session.add_all([pub_a, pub_b])
            await session.flush()
            created_pub_ids.extend([pub_a.id, pub_b.id])

            # Link publisher_id back to users
            user_a.publisher_id = pub_a.id
            user_b.publisher_id = pub_b.id
            await session.flush()

            # Create Slots for Publisher A
            slot_a1 = AdSlot(
                publisher_id=pub_a.id,
                slot_id=f"slot_{test_suffix}_a1",
                name=f"Header Leaderboard {test_suffix}",
                format=CreativeType.BANNER,
                width=728,
                height=90,
                status=Status.ACTIVE,
            )
            slot_a2 = AdSlot(
                publisher_id=pub_a.id,
                slot_id=f"slot_{test_suffix}_a2",
                name=f"Sidebar Square {test_suffix}",
                format=CreativeType.BANNER,
                width=300,
                height=250,
                status=Status.ACTIVE,
            )
            slot_a3 = AdSlot(
                publisher_id=pub_a.id,
                slot_id=f"slot_{test_suffix}_a3",
                name=f"Footer Banner {test_suffix}",
                format=CreativeType.BANNER,
                width=728,
                height=90,
                status=Status.ACTIVE,
            )

            # Create Slots for Publisher B (Competitor)
            slot_b1 = AdSlot(
                publisher_id=pub_b.id,
                slot_id=f"slot_{test_suffix}_b1",
                name=f"Competitor Billboard {test_suffix}",
                format=CreativeType.BANNER,
                width=970,
                height=250,
                status=Status.ACTIVE,
            )
            session.add_all([slot_a1, slot_a2, slot_a3, slot_b1])
            await session.flush()
            created_slot_ids.extend([slot_a1.id, slot_a2.id, slot_a3.id, slot_b1.id])

            # Create Stats
            today = date.today()
            day_3_ago = today - timedelta(days=3)
            day_40_ago = today - timedelta(days=40)

            stats = [
                # A1: 1000 impr, 30 clicks -> great (CTR=3.0%)
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a1.id,
                    stat_date=day_3_ago,
                    impressions=1000,
                    clicks=30,
                    conversions=3,
                    earnings=Decimal("6.000000"),
                ),
                # A1: 5000 impr on day_40_ago (must be excluded by 30d window)
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a1.id,
                    stat_date=day_40_ago,
                    impressions=5000,
                    clicks=100,
                    conversions=10,
                    earnings=Decimal("30.000000"),
                ),
                # A2: 99 impr -> insufficient_data
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a2.id,
                    stat_date=day_3_ago,
                    impressions=99,
                    clicks=5,
                    conversions=0,
                    earnings=Decimal("1.000000"),
                ),
                # A3: 500 impr, 0 clicks -> low_visibility
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=slot_a3.id,
                    stat_date=day_3_ago,
                    impressions=500,
                    clicks=0,
                    conversions=0,
                    earnings=Decimal("0.500000"),
                ),
                # B1 (Pub B): 2000 impr, 20 clicks (CTR=1.0%)
                PublisherDailyStat(
                    publisher_id=pub_b.id,
                    slot_id=slot_b1.id,
                    stat_date=day_3_ago,
                    impressions=2000,
                    clicks=20,
                    conversions=2,
                    earnings=Decimal("10.000000"),
                ),
                # NULL-slot legacy row (must not be attributed)
                PublisherDailyStat(
                    publisher_id=pub_a.id,
                    slot_id=None,
                    stat_date=day_3_ago,
                    impressions=8888,
                    clicks=888,
                    conversions=88,
                    earnings=Decimal("888.000000"),
                ),
            ]
            session.add_all(stats)
            await session.commit()
            print("[OK] Test publishers, slots, and stats created and committed")

            print("\n--- STEP 2: GENERATE REAL JWT TOKENS ---")
            token_a = create_access_token(user_a)
            token_b = create_access_token(user_b)
            token_admin = create_access_token(admin_user)
            print("[OK] Real signed JWT access tokens generated")

            print("\n--- STEP 3: EXECUTE LIVE HTTP CHECKS VIA FASTAPI ASGI ---")
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                # 3A. Authorized Publisher A requests own placement health
                print("  [Check 1] Authorized publisher requests own health...")
                res = await client.get(
                    f"/api/v1/publisher/{pub_a.id}/placements/health",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
                data = res.json()
                assert data["publisher_id"] == pub_a.id
                assert data["window_days"] == 30
                assert data["total_placements"] == 3
                assert len(data["placements"]) == 3
                print("  -> Passed: 200 OK, 3 placements returned")

                # Verify metrics and verdicts
                slots_by_id = {p["slot_id"]: p for p in data["placements"]}
                p1 = slots_by_id[f"slot_{test_suffix}_a1"]
                assert p1["impressions"] == 1000, f"Expected 1000 (day-40 excluded), got {p1['impressions']}"
                assert p1["verdict"] == "great"
                assert p1["recommendation"] == "Raise floor price — demand exceeds supply"
                print("  -> Passed: Slot A1 correctly evaluated as 'great' with floor price recommendation")

                p2 = slots_by_id[f"slot_{test_suffix}_a2"]
                assert p2["impressions"] == 99
                assert p2["verdict"] == "insufficient_data"
                assert p2["recommendation"] == "Collect more data (minimum 100 impressions required)"
                print("  -> Passed: Slot A2 correctly evaluated as 'insufficient_data' at 99 impressions")

                p3 = slots_by_id[f"slot_{test_suffix}_a3"]
                assert p3["impressions"] == 500
                assert p3["verdict"] == "low_visibility"
                assert p3["recommendation"] == "Move slot above the fold or widen it"
                print("  -> Passed: Slot A3 correctly evaluated as 'low_visibility'")

                # Verify privacy / data isolation
                raw_text = res.text
                assert f"slot_{test_suffix}_b1" not in raw_text
                assert "Competitor Billboard" not in raw_text
                assert "Beta Media" not in raw_text
                print("  -> Passed: Zero cross-publisher data leakage (competitor slots absent)")

                # Verify benchmarks included
                assert "BANNER" in data["benchmarks"]
                assert data["benchmarks"]["BANNER"]["eligible_slots_count"] >= 2
                print("  -> Passed: Platform format benchmark returned with medians")

                # 3B. Publisher A attempts to access Publisher B's health -> 403
                print("\n  [Check 2] Cross-publisher access attempt...")
                res_forbidden = await client.get(
                    f"/api/v1/publisher/{pub_b.id}/placements/health",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert res_forbidden.status_code == 403, f"Expected 403, got {res_forbidden.status_code}"
                assert "outside your account" in res_forbidden.json()["detail"]
                print("  -> Passed: 403 Forbidden correctly rejected cross-publisher access")

                # 3C. Unauthenticated request -> 401
                print("\n  [Check 3] Unauthenticated request...")
                res_unauth = await client.get(f"/api/v1/publisher/{pub_a.id}/placements/health")
                assert res_unauth.status_code == 401, f"Expected 401, got {res_unauth.status_code}"
                print("  -> Passed: 401 Unauthorized correctly rejected unauthenticated request")

                # 3D. Admin accesses publisher A health -> 200
                print("\n  [Check 4] Admin access to publisher health...")
                res_admin = await client.get(
                    f"/api/v1/publisher/{pub_a.id}/placements/health",
                    headers={"Authorization": f"Bearer {token_admin}"},
                )
                assert res_admin.status_code == 200
                print("  -> Passed: Admin authorized to view publisher health")

                # 3E. Nonexistent publisher ID -> 404
                print("\n  [Check 5] Nonexistent publisher ID...")
                res_404 = await client.get(
                    "/api/v1/publisher/999999/placements/health",
                    headers={"Authorization": f"Bearer {token_admin}"},
                )
                assert res_404.status_code == 404
                print("  -> Passed: 404 Not Found returned for nonexistent publisher")

        finally:
            print("\n--- STEP 4: CLEANUP TEST DATA ---")
            await session.rollback()
            if created_slot_ids:
                await session.execute(delete(PublisherDailyStat).where(PublisherDailyStat.slot_id.in_(created_slot_ids)))
            if created_pub_ids:
                await session.execute(delete(PublisherDailyStat).where(PublisherDailyStat.publisher_id.in_(created_pub_ids)))
                await session.execute(delete(AdSlot).where(AdSlot.publisher_id.in_(created_pub_ids)))
                await session.execute(delete(Publisher).where(Publisher.id.in_(created_pub_ids)))
            if created_user_ids:
                await session.execute(delete(User).where(User.id.in_(created_user_ids)))
            await session.commit()
            print("[OK] Test records successfully cleaned up from PostgreSQL")

    print("\n" + "=" * 70)
    print("LIVE RUNTIME VERIFICATION FOR PHASE 6 WU3 COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_live_verification())
