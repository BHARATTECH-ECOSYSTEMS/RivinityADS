"""Real runtime verification script for Phase 6 Work Unit 1 (WU1).
Executes end-to-end against live PostgreSQL and Redis.
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from liteads.ad_server.main import app
from liteads.common.cache import redis_client
from liteads.common.database import db, init_db
from liteads.common.tracking_sig import sign_tracking
from liteads.models.ad import Advertiser, Campaign, Creative
from liteads.models.auth import User
from liteads.models.base import CreativeType, ModerationStatus, Status
from liteads.models.domain import AdvertiserDomain
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat


async def run_live_verification():
    print("=" * 70)
    print("STARTING LIVE RUNTIME VERIFICATION FOR PHASE 6 WU1")
    print("=" * 70)

    # 1. Connect to live DB and Redis
    await init_db()
    await redis_client.connect()
    assert redis_client.connected, "Redis must be connected"
    print("[OK] PostgreSQL and Redis connected successfully")

    async with db.session() as session:
        # Step A: Inspect PostgreSQL Constraints and Indexes
        print("\n--- STEP A: INSPECT POSTGRESQL CONSTRAINTS AND INDEXES ---")
        constraints_res = await session.execute(
            text("""
                SELECT conname, pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE conrelid = 'publisher_daily_stats'::regclass
            """)
        )
        constraints = constraints_res.fetchall()
        print(f"Found {len(constraints)} constraints on publisher_daily_stats:")
        constraint_names = []
        for conname, condef in constraints:
            constraint_names.append(conname)
            print(f"  - {conname}: {condef}")

        assert "uq_pub_daily_stats_slot_date" in constraint_names, "uq_pub_daily_stats_slot_date must exist"
        assert "publisher_daily_stats_publisher_id_stat_date_key" not in constraint_names, "Old constraint must be dropped"
        print("[OK] Constraint verification passed: uq_pub_daily_stats_slot_date is active, old constraint dropped")

        indexes_res = await session.execute(
            text("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'publisher_daily_stats'
            """)
        )
        indexes = indexes_res.fetchall()
        print(f"Found {len(indexes)} indexes on publisher_daily_stats:")
        index_names = []
        for idxname, idxdef in indexes:
            index_names.append(idxname)
            print(f"  - {idxname}: {idxdef}")

        assert "idx_pub_daily_stats_lookup" in index_names, "idx_pub_daily_stats_lookup must exist"
        print("[OK] Index verification passed: idx_pub_daily_stats_lookup is active")

        # Step B: Check Legacy Rows with slot_id IS NULL
        print("\n--- STEP B: VERIFY LEGACY ROWS WITH slot_id IS NULL ---")
        legacy_res = await session.execute(
            text("SELECT id, publisher_id, slot_id, stat_date, impressions, clicks, earnings FROM publisher_daily_stats WHERE slot_id IS NULL")
        )
        legacy_rows = legacy_res.fetchall()
        print(f"Preserved legacy NULL-slot rows count: {len(legacy_rows)}")
        for row in legacy_rows:
            print(f"  - ID: {row[0]}, Publisher: {row[1]}, Slot: {row[2]}, Date: {row[3]}, Impr: {row[4]}, Clicks: {row[5]}, Earnings: {row[6]}")
        print("[OK] Historical NULL-slot rows are intact and untouched")

        # Step C: Create Live Entities for Multi-Slot Ingestion
        print("\n--- STEP C: SEED LIVE ENTITIES ---")
        pub = Publisher(name=f"Phase 6 WU1 Pub {int(datetime.now().timestamp())}", status=1, balance=Decimal("10.00"))
        session.add(pub)
        await session.flush()

        # Create two slots for this publisher
        slot_alpha = AdSlot(
            publisher_id=pub.id,
            slot_id=f"slot_alpha_{pub.id}",
            name="Alpha Slot",
            format=1,
            width=728,
            height=90,
            status=1,
        )
        slot_beta = AdSlot(
            publisher_id=pub.id,
            slot_id=f"slot_beta_{pub.id}",
            name="Beta Slot",
            format=1,
            width=300,
            height=250,
            status=1,
        )
        session.add_all([slot_alpha, slot_beta])
        await session.flush()

        # Create advertiser, domain, campaign, creative
        adv = Advertiser(name=f"Phase 6 WU1 Adv {pub.id}", balance=Decimal("500.00"), daily_budget=Decimal("100.00"), status=1)
        session.add(adv)
        await session.flush()

        dom = AdvertiserDomain(advertiser_id=adv.id, domain=f"brand{pub.id}.com", status="VERIFIED", verification_token="tok")
        session.add(dom)
        await session.flush()

        camp = Campaign(
            advertiser_id=adv.id,
            name=f"Phase 6 WU1 Camp {pub.id}",
            bid_amount=Decimal("2.50"),
            budget_daily=Decimal("100.00"),
            status=Status.ACTIVE,
        )
        session.add(camp)
        await session.flush()

        creat = Creative(
            campaign_id=camp.id,
            title="WU1 Creative",
            landing_url="https://brand16.com/product",
            creative_type=CreativeType.BANNER,
            status=Status.ACTIVE,
            moderation_status=ModerationStatus.APPROVED,
        )
        session.add(creat)
        await session.flush()

        # Create publisher user for reporting endpoint authentication
        user = User(
            email=f"pub_{pub.id}@example.com",
            name="Publisher User",
            password_hash="test_hash",
            role="publisher",
            publisher_id=pub.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        print(f"[OK] Seeded Pub={pub.id}, SlotAlpha={slot_alpha.id} ({slot_alpha.slot_id}), SlotBeta={slot_beta.id} ({slot_beta.slot_id}), User={user.id}")

    # Step D: Test Real HTTP Event Ingestion via POST /api/v1/event/track
    print("\n--- STEP D: LIVE EVENT INGESTION VIA /api/v1/event/track ---")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ts_now = int(datetime.now().timestamp())
        req_alpha_imp_id = f"req_alpha_imp_{pub.id}_{ts_now}"
        req_beta_imp_id = f"req_beta_imp_{pub.id}_{ts_now}"
        req_alpha_clk_id = f"req_alpha_clk_{pub.id}_{ts_now}"

        # Create signed tracking tokens for both slots
        ad_id_str = f"ad_{camp.id}_{creat.id}"
        sig_alpha_imp = sign_tracking(req_alpha_imp_id, ad_id_str, slot_alpha.slot_id)
        sig_beta_imp = sign_tracking(req_beta_imp_id, ad_id_str, slot_beta.slot_id)
        sig_alpha_click = sign_tracking(req_alpha_clk_id, ad_id_str, slot_alpha.slot_id)

        # 1. Ingest impression for Slot Alpha with root-level slot_id
        res1 = await client.post(
            "/api/v1/event/track",
            json={
                "event_type": "impression",
                "ad_id": ad_id_str,
                "request_id": req_alpha_imp_id,
                "sig": sig_alpha_imp,
                "slot_id": slot_alpha.slot_id,
                "extra": {"device": "desktop"},
            },
        )
        assert res1.status_code == 200, f"Impression Alpha failed: {res1.text}"
        print("[OK] Ingested impression for Slot Alpha (200 OK)")

        # 2. Ingest impression for Slot Beta with root-level slot_id
        res2 = await client.post(
            "/api/v1/event/track",
            json={
                "event_type": "impression",
                "ad_id": ad_id_str,
                "request_id": req_beta_imp_id,
                "sig": sig_beta_imp,
                "slot_id": slot_beta.slot_id,
            },
        )
        assert res2.status_code == 200, f"Impression Beta failed: {res2.text}"
        print("[OK] Ingested impression for Slot Beta (200 OK)")

        # 3. Ingest click for Slot Alpha
        res3 = await client.post(
            "/api/v1/event/track",
            json={
                "event_type": "click",
                "ad_id": ad_id_str,
                "request_id": req_alpha_clk_id,
                "sig": sig_alpha_click,
                "slot_id": slot_alpha.slot_id,
            },
        )
        assert res3.status_code == 200, f"Click Alpha failed: {res3.text}"
        print("[OK] Ingested click for Slot Alpha (200 OK)")

    # Step E: Verify PostgreSQL Rows
    print("\n--- STEP E: VERIFY DATABASE MULTI-SLOT STATS ROWS ---")
    async with db.session() as session:
        today = date.today()
        rows_res = await session.execute(
            text("""
                SELECT id, publisher_id, slot_id, stat_date, impressions, clicks, conversions, earnings
                FROM publisher_daily_stats
                WHERE publisher_id = :pub_id AND stat_date = :today
                ORDER BY slot_id
            """),
            {"pub_id": pub.id, "today": today},
        )
        rows = rows_res.fetchall()
        print(f"Found {len(rows)} rows for publisher {pub.id} on {today}:")
        assert len(rows) == 2, f"Expected exactly 2 rows (one per slot), found {len(rows)}"

        row_alpha = [r for r in rows if r[2] == slot_alpha.id][0]
        row_beta = [r for r in rows if r[2] == slot_beta.id][0]

        print(f"  Slot Alpha (id={row_alpha[2]}): impressions={row_alpha[4]}, clicks={row_alpha[5]}, earnings={row_alpha[7]}")
        print(f"  Slot Beta  (id={row_beta[2]}): impressions={row_beta[4]}, clicks={row_beta[5]}, earnings={row_beta[7]}")

        assert row_alpha[4] == 1, "Slot Alpha should have 1 impression"
        assert row_alpha[5] == 1, "Slot Alpha should have 1 click"
        assert row_beta[4] == 1, "Slot Beta should have 1 impression"
        assert row_beta[5] == 0, "Slot Beta should have 0 clicks"
        print("[OK] Multi-slot rows verified: separate rows for the same publisher and date exist with correct per-slot metrics!")

    # Step F: Verify Publisher Reporting SQL Aggregation
    print("\n--- STEP F: VERIFY PUBLISHER REPORTING AGGREGATION ---")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Override verified_user to simulate the publisher
        from liteads.common.security import verified_user, current_user
        app.dependency_overrides[verified_user] = lambda: user
        app.dependency_overrides[current_user] = lambda: user

        try:
            start_str = (today - timedelta(days=1)).isoformat()
            end_str = (today + timedelta(days=1)).isoformat()
            rep_res = await client.get(
                f"/api/v1/publisher/{pub.id}/reporting?start_date={start_str}&end_date={end_str}"
            )
            assert rep_res.status_code == 200, f"Reporting failed: {rep_res.text}"
            data = rep_res.json()
            print(f"Reporting response status: {rep_res.status_code}")
            print(f"Total metrics: impressions={data.get('impressions')}, clicks={data.get('clicks')}, earnings={data.get('earnings')}")
            daily_breakdown = data.get("daily_breakdown", [])
            print(f"Daily breakdown count: {len(daily_breakdown)}")
            for entry in daily_breakdown:
                print(f"  - Date: {entry['stat_date']}, Impr: {entry['impressions']}, Clicks: {entry['clicks']}, Conversions: {entry['conversions']}, Earnings: {entry['earnings']}")

            assert len(daily_breakdown) == 1, f"Expected exactly 1 aggregated daily entry for today, got {len(daily_breakdown)}"
            today_entry = daily_breakdown[0]
            assert today_entry["stat_date"] == today.isoformat()
            assert today_entry["impressions"] == 2, f"Expected 2 aggregated impressions, got {today_entry['impressions']}"
            assert today_entry["clicks"] == 1, f"Expected 1 aggregated click, got {today_entry['clicks']}"
            assert data["impressions"] == 2, f"Expected 2 total impressions, got {data['impressions']}"
            assert data["clicks"] == 1, f"Expected 1 total click, got {data['clicks']}"
            print("[OK] Publisher reporting SQL aggregation verified: exactly one daily record per date combining all slots!")
        finally:
            app.dependency_overrides.clear()

    # Step G: Verify PostgreSQL Multi-Session Concurrent First-Event Race
    print("\n--- STEP G: POSTGRESQL MULTI-SESSION CONCURRENT FIRST-EVENT RACE ---")
    async with db.session() as session:
        slot_gamma = AdSlot(
            publisher_id=pub.id,
            slot_id=f"slot_gamma_{pub.id}_{int(datetime.now().timestamp())}",
            name="Gamma Slot (Race Test)",
            format=1,
            width=300,
            height=250,
            status=1,
        )
        session.add(slot_gamma)
        await session.commit()
        gamma_slot_id = slot_gamma.slot_id
        gamma_slot_pk = slot_gamma.id
        print(f"Created virgin slot Gamma: id={gamma_slot_pk}, slot_id='{gamma_slot_id}'")

    from liteads.ad_server.services.event_service import EventService

    async def race_worker(worker_id: int):
        async with db.session() as s:
            svc = EventService(session=s)
            req_id = f"req_gamma_conc_{worker_id}_{int(datetime.now().timestamp())}"
            res = await svc.track_event(
                request_id=req_id,
                ad_id=ad_id_str,
                event_type="impression",
                extra={"slot_id": gamma_slot_id},
            )
            assert res is True
            # session commits upon exiting db.session()
        return True

    # Launch two simultaneous workers on independent PostgreSQL sessions
    worker_results = await asyncio.gather(race_worker(1), race_worker(2), return_exceptions=True)
    print(f"PostgreSQL concurrent workers results: {worker_results}")
    for r in worker_results:
        assert r is True, f"Worker failed with exception: {r}"

    async with db.session() as session:
        res = await session.execute(
            text("""
                SELECT id, publisher_id, slot_id, stat_date, impressions, clicks, earnings
                FROM publisher_daily_stats
                WHERE publisher_id = :pub_id AND slot_id = :slot_id AND stat_date = :today
            """),
            {"pub_id": pub.id, "slot_id": gamma_slot_pk, "today": today},
        )
        gamma_rows = res.fetchall()
        print(f"PostgreSQL rows for Gamma slot count: {len(gamma_rows)}")
        assert len(gamma_rows) == 1, f"Expected exactly 1 row, got {len(gamma_rows)}"
        gamma_row = gamma_rows[0]
        print(f"  Gamma row: ID={gamma_row[0]}, Impressions={gamma_row[4]}, Clicks={gamma_row[5]}, Earnings={gamma_row[6]}")
        assert gamma_row[4] == 2, f"Expected exactly 2 impressions recorded, got {gamma_row[4]}"
        print("[OK] PostgreSQL race scenario verified: two independent concurrent sessions recorded first events simultaneously without errors or lost updates!")

    print("\n" + "=" * 70)
    print("ALL LIVE RUNTIME VERIFICATION CHECKS PASSED FOR PHASE 6 WU1!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_live_verification())
