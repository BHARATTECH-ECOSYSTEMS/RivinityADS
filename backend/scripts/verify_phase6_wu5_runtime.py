"""Live runtime verification script for Phase 6 Work Unit 5 (WU5)
Weekly Publisher Placement Digest and Production-Safe Scheduler.
Executes end-to-end against live PostgreSQL and Redis.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import delete, select

from liteads.ad_server.services.digest_scheduler import (
    ACTIVE_CYCLE_KEY,
    ACTIVE_CYCLE_TARGET_DATE_KEY,
    COMPLETED_CYCLE_RETENTION_TTL,
    DEFAULT_DIGEST_INTERVAL_SECONDS,
    LAST_SUCCESSFUL_CYCLE_KEY,
    WeeklyDigestTask,
)
from liteads.ad_server.services.placement_analyzer import (
    PlacementAnalysisResult,
    VERDICT_GREAT,
    VERDICT_INSUFFICIENT_DATA,
)
from liteads.ad_server.services.publisher_digest_service import (
    BATCH_STATUS_FAILURE,
    BATCH_STATUS_NO_OP,
    BATCH_STATUS_PARTIAL,
    BATCH_STATUS_SUCCESS,
    DIGEST_DELIVERED_KEY_PREFIX,
    PublisherDigestService,
)
from liteads.common.cache import redis_client
from liteads.common.database import db, init_db
from liteads.common.email import EmailDeliveryError
from liteads.models.auth import User
from liteads.models.base import CreativeType, Status
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat


async def run_live_verification():
    print("=" * 70)
    print("STARTING LIVE RUNTIME VERIFICATION FOR PHASE 6 WU5 (WEEKLY PUBLISHER DIGEST)")
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
            print("\n--- STEP 1: CREATE LIVE TEST ENTITIES ---")
            # User & Publisher Alpha
            u_alpha = User(
                email=f"alpha_owner_{test_suffix}@example.com",
                name=f"Alpha Owner {test_suffix}",
                role="publisher",
                password_hash="fakehash",
                is_active=True,
            )
            session.add(u_alpha)
            await session.flush()
            created_user_ids.append(u_alpha.id)

            pub_alpha = Publisher(
                name=f"Alpha Media {test_suffix}",
                email=f"alpha_digest_{test_suffix}@example.com",
                owner_user_id=u_alpha.id,
                status=Status.ACTIVE,
                balance=Decimal("100.00"),
            )
            session.add(pub_alpha)
            await session.flush()
            created_pub_ids.append(pub_alpha.id)

            # User & Publisher Beta
            u_beta = User(
                email=f"beta_owner_{test_suffix}@example.com",
                name=f"Beta Owner {test_suffix}",
                role="publisher",
                password_hash="fakehash",
                is_active=True,
            )
            session.add(u_beta)
            await session.flush()
            created_user_ids.append(u_beta.id)

            pub_beta = Publisher(
                name=f"Beta Network {test_suffix}",
                email=f"beta_digest_{test_suffix}@example.com",
                owner_user_id=u_beta.id,
                status=Status.ACTIVE,
                balance=Decimal("200.00"),
            )
            session.add(pub_beta)
            await session.flush()
            created_pub_ids.append(pub_beta.id)

            # User & Publisher Fallback (email on User only)
            u_fallback = User(
                email=f"fallback_owner_{test_suffix}@example.com",
                name=f"Fallback Owner {test_suffix}",
                role="publisher",
                password_hash="fakehash",
                is_active=True,
            )
            session.add(u_fallback)
            await session.flush()
            created_user_ids.append(u_fallback.id)

            pub_fallback = Publisher(
                name=f"Fallback Media {test_suffix}",
                email=None,
                owner_user_id=u_fallback.id,
                status=Status.ACTIVE,
                balance=Decimal("50.00"),
            )
            session.add(pub_fallback)
            await session.flush()
            created_pub_ids.append(pub_fallback.id)

            # Slots
            slot_alpha_1 = AdSlot(
                publisher_id=pub_alpha.id,
                slot_id=f"alpha_banner_{test_suffix}",
                name="Alpha Main Banner",
                format=CreativeType.BANNER,
                width=728,
                height=90,
                status=Status.ACTIVE,
            )
            slot_alpha_2 = AdSlot(
                publisher_id=pub_alpha.id,
                slot_id=f"alpha_video_{test_suffix}",
                name="Alpha Video Stream",
                format=CreativeType.VIDEO,
                width=640,
                height=360,
                status=Status.ACTIVE,
            )
            slot_beta_1 = AdSlot(
                publisher_id=pub_beta.id,
                slot_id=f"beta_banner_{test_suffix}",
                name="Beta Hero Banner",
                format=CreativeType.BANNER,
                width=300,
                height=250,
                status=Status.ACTIVE,
            )
            session.add_all([slot_alpha_1, slot_alpha_2, slot_beta_1])
            await session.flush()
            created_slot_ids.extend([slot_alpha_1.id, slot_alpha_2.id, slot_beta_1.id])

            # Seed daily stats
            today = date.today()
            # Alpha 1: 1500 impressions, 45 clicks (3% CTR) -> Great
            stat_a1 = PublisherDailyStat(
                publisher_id=pub_alpha.id,
                slot_id=slot_alpha_1.id,
                stat_date=today - timedelta(days=2),
                impressions=1500,
                clicks=45,
                conversions=5,
                earnings=Decimal("75.00"),
            )
            # Alpha 2: 50 impressions (under 100) -> Insufficient Data
            stat_a2 = PublisherDailyStat(
                publisher_id=pub_alpha.id,
                slot_id=slot_alpha_2.id,
                stat_date=today - timedelta(days=3),
                impressions=50,
                clicks=5,
                conversions=0,
                earnings=Decimal("2.50"),
            )
            # Beta 1: 1000 impressions, 20 clicks (2% CTR) -> Healthy
            stat_b1 = PublisherDailyStat(
                publisher_id=pub_beta.id,
                slot_id=slot_beta_1.id,
                stat_date=today - timedelta(days=2),
                impressions=1000,
                clicks=20,
                conversions=2,
                earnings=Decimal("30.00"),
            )
            session.add_all([stat_a1, stat_a2, stat_b1])
            await session.commit()
            print(f"[OK] Created live publishers: Alpha={pub_alpha.id}, Beta={pub_beta.id}, Fallback={pub_fallback.id}")
            print(f"[OK] Created live slots and seeded daily statistics")

            print("\n--- STEP 2: VERIFY AUTHORITATIVE EMAIL RESOLUTION & VALIDATION ---")
            digest_service = PublisherDigestService(session)
            assert digest_service.is_valid_email("valid@example.com") is True
            assert digest_service.is_valid_email("invalid-email") is False
            assert digest_service.is_valid_email("") is False
            assert digest_service.is_valid_email(None) is False
            print("[OK] Email syntax validation functions correctly")

            email_alpha = await digest_service.get_authoritative_publisher_email(pub_alpha)
            assert email_alpha == f"alpha_digest_{test_suffix}@example.com"
            print(f"[OK] Direct Publisher.email resolved: {email_alpha}")

            email_fallback = await digest_service.get_authoritative_publisher_email(pub_fallback)
            assert email_fallback == f"fallback_owner_{test_suffix}@example.com"
            print(f"[OK] Fallback User.email resolved for empty publisher: {email_fallback}")

            print("\n--- STEP 3: VERIFY PLACEMENT CATEGORIZATION & RANKING ---")
            analyses_alpha = await digest_service.analyzer.analyze_publisher_placements(
                publisher_id=pub_alpha.id, as_of_date=today
            )
            ranking = digest_service.categorize_and_rank_placements(analyses_alpha)

            assert len(ranking.top_placements) == 1
            assert ranking.top_placements[0].slot_id == slot_alpha_1.slot_id
            assert ranking.top_placements[0].verdict == VERDICT_GREAT
            print(f"[OK] Top placement correctly identified: {ranking.top_placements[0].slot_id} ({ranking.top_placements[0].verdict})")

            assert len(ranking.insufficient_data_placements) == 1
            assert ranking.insufficient_data_placements[0].slot_id == slot_alpha_2.slot_id
            assert ranking.insufficient_data_placements[0].impressions < 100
            print(f"[OK] Low-volume placement safely segregated into data collection: {ranking.insufficient_data_placements[0].slot_id} ({ranking.insufficient_data_placements[0].impressions} imps)")

            print("\n--- STEP 4: VERIFY TENANT ISOLATION & COMPOSER CONTENT ---")
            text_content, html_content = digest_service.build_digest_content(
                publisher=pub_alpha,
                analyses=analyses_alpha,
                start_date=(today - timedelta(days=29)).isoformat(),
                end_date=today.isoformat(),
            )
            # Must contain Alpha details
            assert slot_alpha_1.slot_id in text_content
            assert slot_alpha_2.slot_id in text_content
            assert "Alpha Main Banner" in text_content
            assert f"Alpha Media {test_suffix}" in text_content
            # Must NEVER contain Beta details
            assert slot_beta_1.slot_id not in text_content
            assert slot_beta_1.slot_id not in html_content
            assert "Beta Hero Banner" not in text_content
            assert "Beta Network" not in text_content
            print("[OK] Tenant isolation verified: Alpha digest contains 0% Beta data")

            print("\n--- STEP 5: VERIFY END-TO-END DELIVERY & ERROR ISOLATION ---")
            delivered_messages = []

            async def mock_send(to_address, subject, text_content, html_content=None):
                if "alpha_digest" in to_address:
                    delivered_messages.append({"to": to_address, "subject": subject})
                    return None
                elif "beta_digest" in to_address:
                    raise EmailDeliveryError("Simulated SMTP delivery failure for Beta")
                delivered_messages.append({"to": to_address, "subject": subject})
                return None

            with patch("liteads.ad_server.services.publisher_digest_service.send_email", side_effect=mock_send):
                alpha_res = await digest_service.send_digest_for_publisher(pub_alpha.id)
                assert alpha_res is True
                print(f"[OK] Individual digest delivery succeeded for Publisher Alpha")

                beta_res = await digest_service.send_digest_for_publisher(pub_beta.id)
                assert beta_res is False
                print(f"[OK] Simulated email failure handled cleanly for Publisher Beta without unhandled exception")

            print("\n--- STEP 6: VERIFY SCHEDULER LIFECYCLE & MULTI-WORKER COORDINATION ---")
            scheduler = WeeklyDigestTask(interval_seconds=604800)
            assert scheduler.interval_seconds == DEFAULT_DIGEST_INTERVAL_SECONDS
            assert not scheduler.is_running

            with patch.object(scheduler, "_process_cycle", new_callable=AsyncMock) as mock_cycle:
                mock_cycle.return_value = {"total": 3, "sent": 2, "failed_or_skipped": 1}
                scheduler.start()
                assert scheduler.is_running
                # Idempotency check: repeated start does not duplicate
                orig_task = scheduler._task
                scheduler.start()
                assert scheduler._task is orig_task
                print("[OK] Scheduler start() is idempotent and created exactly 1 background task")

                await asyncio.sleep(0.02)
                await scheduler.stop()
                assert not scheduler.is_running
                assert scheduler._task.done()
                print("[OK] Scheduler stop() cleanly cancelled background task without hanging coroutines")

            # Verify distributed lock coordination and ownership-safe mechanics (Finding 3)
            if redis_client.connected:
                lock_key = "lock:weekly_placement_digest_cycle"
                owner_token = str(uuid.uuid4())
                # Acquire lock manually with token
                acquired = await redis_client.set(lock_key, owner_token, ttl=60, nx=True)
                assert acquired is True
                print(f"[OK] Lock acquired with unique owner token: {owner_token}")

                # Cycle attempt while lock is held must skip due to lock
                cycle_res = await scheduler._process_cycle()
                assert cycle_res.get("skipped_due_to_lock") == 1
                print("[OK] Multi-worker distributed locking prevents duplicate cycle execution across workers")

                # Test lease renewal with owner token
                renewed = await redis_client.renew_lock(lock_key, owner_token, ttl=120)
                assert renewed is True
                print("[OK] Lease renewal succeeded for owner token")

                # Attempt release with wrong token must fail and leave lock intact
                wrong_release = await redis_client.release_lock(lock_key, "wrong_token_123")
                assert wrong_release is False
                assert await redis_client.get(lock_key) == owner_token
                print("[OK] Refusal to release lock with mismatched token verified")

                # Successful release with owner token
                correct_release = await redis_client.release_lock(lock_key, owner_token)
                assert correct_release is True
                assert await redis_client.get(lock_key) is None
                print("[OK] Ownership-verified lock release deleted key atomically")

            print("\n--- STEP 7: VERIFY FINDING 1 CATEGORIZATION & COPY ---")
            dummy_analyses = [
                PlacementAnalysisResult(
                    slot_id=f"health_slot_{i}",
                    slot_db_id=i,
                    slot_name=f"Placement {i}",
                    publisher_id=pub_alpha.id,
                    format=CreativeType.BANNER,
                    window_days=30,
                    start_date="2026-09-18",
                    end_date="2026-10-17",
                    impressions=500,
                    clicks=10,
                    conversions=1,
                    earnings=10.0,
                    ctr=0.02,
                    ecpm=20.0,
                    verdict="healthy",
                    recommendation="Maintain placement",
                    benchmark={},
                )
                for i in range(1, 6)
            ]
            ranked_healthy = digest_service.categorize_and_rank_placements(dummy_analyses)
            assert len(ranked_healthy.top_placements) == 3
            assert len(ranked_healthy.bottom_placements) == 0
            assert len(ranked_healthy.additional_placements) == 2
            txt, htm = digest_service.build_digest_content(
                pub_alpha, dummy_analyses, "2026-09-18", "2026-10-17"
            )
            assert "PLACEMENTS NEEDING ATTENTION" not in txt
            assert "Placements Needing Attention" not in htm
            assert "ADDITIONAL ACTIVE PLACEMENTS:" in txt
            assert "Additional Active Placements" in htm
            print("[OK] Finding 1: 5 healthy placements produce no attention warnings; remaining 2 appear under 'Additional Active Placements'")

            print("\n--- STEP 8: VERIFY FINDING 2 RESTART CYCLE TIMESTAMP ---")
            ts_key = "digest:last_successful_cycle_timestamp"
            now_ts = datetime.now(timezone.utc).timestamp()
            # Set timestamp to 1 day ago (6 days remaining)
            await redis_client.set(ts_key, str(now_ts - 86400))
            remaining = await scheduler.get_remaining_interval()
            assert 518000 < remaining <= 518400
            print(f"[OK] Finding 2: Remaining interval correctly calculated after restart: {remaining:.1f}s")
            await redis_client.delete(ts_key)

            print("\n--- STEP 9: VERIFY FINDING 4 FAIL CLOSED WHEN REDIS DISCONNECTED ---")
            with patch.object(redis_client, "_connected", False):
                with patch.dict(os.environ, {}, clear=True):
                    fail_closed_res = await scheduler._process_cycle()
                    assert fail_closed_res.get("skipped_due_to_uncoordinated_degraded_mode") == 1
                    print("[OK] Finding 4: Scheduler safely fails closed when Redis is disconnected without uncoordinated flag")

            print("\n--- STEP 10: VERIFY RETRY SEMANTICS & PER-PUBLISHER IDEMPOTENCY ---")
            delivered_recipients = []
            async def selective_send(to_address, subject, text_content, html_content=None):
                delivered_recipients.append(to_address)
                # Beta fails on first attempt
                if "beta_digest" in to_address and len([r for r in delivered_recipients if "beta_digest" in r]) == 1:
                    raise EmailDeliveryError("Simulated transient SMTP failure for Beta")
                return None

            test_cycle_date = today - timedelta(days=1)
            delivered_key = f"{DIGEST_DELIVERED_KEY_PREFIX}{test_cycle_date.isoformat()}"
            await redis_client.delete(delivered_key)
            await redis_client.delete(LAST_SUCCESSFUL_CYCLE_KEY)

            with patch("liteads.ad_server.services.publisher_digest_service.send_email", side_effect=selective_send):
                # 1. First run: Alpha succeeds, Beta fails -> status partial_success
                batch_1 = await digest_service.send_all_digests(
                    as_of_date=test_cycle_date,
                    publisher_ids=[pub_alpha.id, pub_beta.id],
                    skip_already_delivered=True,
                )
                assert batch_1["status"] == BATCH_STATUS_PARTIAL
                assert batch_1["sent"] == 1
                assert batch_1["failed_or_skipped"] == 1
                assert str(pub_alpha.id) in await redis_client.smembers(delivered_key)
                assert str(pub_beta.id) not in await redis_client.smembers(delivered_key)
                # Timestamp must NOT be recorded on partial success
                assert await redis_client.get(LAST_SUCCESSFUL_CYCLE_KEY) is None
                print("[OK] Partial success does not record weekly success timestamp; records delivered publisher in Redis")

                # Count emails sent to Alpha so far
                alpha_sent_before_retry = len([r for r in delivered_recipients if "alpha_digest" in r])
                assert alpha_sent_before_retry == 1

                # 2. Retry run: Alpha is already delivered so it is skipped; Beta is retried and now succeeds
                batch_2 = await digest_service.send_all_digests(
                    as_of_date=test_cycle_date,
                    publisher_ids=[pub_alpha.id, pub_beta.id],
                    skip_already_delivered=True,
                )
                assert batch_2["status"] == BATCH_STATUS_SUCCESS
                assert batch_2["sent"] == 2
                assert batch_2["failed_or_skipped"] == 0
                assert batch_2["previously_sent_count"] == 1
                assert batch_2["newly_sent_count"] == 1

                # Verify Alpha was NOT sent a duplicate email
                alpha_sent_after_retry = len([r for r in delivered_recipients if "alpha_digest" in r])
                assert alpha_sent_after_retry == 1, "Duplicate email was sent to Alpha during retry!"
                print("[OK] Retry successfully delivered failed publisher (Beta) without resending to already-delivered publisher (Alpha)")

            await redis_client.delete(delivered_key)
            await redis_client.delete(LAST_SUCCESSFUL_CYCLE_KEY)

            print("\n--- STEP 11: VERIFY LIVE STABLE CYCLE IDENTITY & RETENTION TTL ---")
            live_scheduler = WeeklyDigestTask()
            day1_date = today - timedelta(days=1)
            day2_date = today

            # Clean any leftover active cycle
            await redis_client.delete(ACTIVE_CYCLE_KEY)
            await redis_client.delete(ACTIVE_CYCLE_TARGET_DATE_KEY)

            # 1. Day 1: Initialize active cycle
            live_cycle_id, live_target_date = await live_scheduler.get_or_create_active_cycle(as_of_date=day1_date)
            assert await redis_client.get(ACTIVE_CYCLE_KEY) == live_cycle_id
            assert await redis_client.get(ACTIVE_CYCLE_TARGET_DATE_KEY) == day1_date.isoformat()
            print(f"[OK] Live active cycle initialized: {live_cycle_id} for target date: {live_target_date}")

            live_delivered_key = f"{DIGEST_DELIVERED_KEY_PREFIX}{live_cycle_id}"
            await redis_client.delete(live_delivered_key)

            # 2. Simulate partial send on Day 1
            with patch("liteads.ad_server.services.publisher_digest_service.send_email", new_callable=AsyncMock):
                batch_res = await digest_service.send_all_digests(
                    as_of_date=live_target_date,
                    publisher_ids=[pub_alpha.id],
                    skip_already_delivered=True,
                    cycle_id=live_cycle_id,
                )
                assert batch_res["sent"] == 1
                assert str(pub_alpha.id) in await redis_client.smembers(live_delivered_key)

                # Check TTL: While incomplete, delivery key MUST NOT expire
                live_ttl_before = await redis_client.ttl(live_delivered_key)
                assert live_ttl_before == -1 or live_ttl_before is None, f"Expected no expiration (-1), got {live_ttl_before}"
                print(f"[OK] Delivery ledger key has no expiration while cycle is incomplete (TTL = {live_ttl_before})")

            # 3. Simulate Day 2 rollover: Scheduler wakes up on next calendar date
            # get_or_create_active_cycle MUST return the exact same Day 1 cycle identity and target date!
            resumed_cycle_id, resumed_target_date = await live_scheduler.get_or_create_active_cycle(as_of_date=day2_date)
            assert resumed_cycle_id == live_cycle_id, f"Expected {live_cycle_id}, got {resumed_cycle_id}"
            assert resumed_target_date == day1_date, f"Expected {day1_date}, got {resumed_target_date}"
            print(f"[OK] Calendar date rollover preserved active cycle ID and target date across midnight: {resumed_cycle_id}")

            # 4. Attempt finalization with mismatched cycle ID: must be refused atomically
            mismatched_result = await live_scheduler.complete_active_cycle("wrong_cycle_id_999")
            assert mismatched_result is False
            assert await redis_client.get(ACTIVE_CYCLE_KEY) == live_cycle_id
            print("[OK] Atomic Lua finalization refused mismatched cycle ID without state modification")

            # 5. Finalize cycle on complete success with matching cycle ID
            finalized_success = await live_scheduler.complete_active_cycle(live_cycle_id)
            assert finalized_success is True
            assert await redis_client.get(ACTIVE_CYCLE_KEY) is None
            assert await redis_client.get(ACTIVE_CYCLE_TARGET_DATE_KEY) is None
            assert await redis_client.get(LAST_SUCCESSFUL_CYCLE_KEY) is not None

            # Verify 14-day retention TTL applied post-completion
            live_ttl_after = await redis_client.ttl(live_delivered_key)
            assert 0 < live_ttl_after <= COMPLETED_CYCLE_RETENTION_TTL, f"Expected 0 < TTL <= 1209600, got {live_ttl_after}"
            print(f"[OK] Post-completion retention TTL successfully applied to live ledger (TTL = {live_ttl_after}s / ~14 days)")

            await redis_client.delete(live_delivered_key)
            await redis_client.delete(LAST_SUCCESSFUL_CYCLE_KEY)




            print("\n" + "=" * 70)
            print("ALL PHASE 6 WU5 LIVE VERIFICATION CHECKS PASSED SUCCESSFULLY (100%)")
            print("=" * 70)

        finally:
            print("\n--- CLEANUP: REMOVING LIVE TEST SEEDED ENTITIES ---")
            if created_slot_ids:
                await session.execute(delete(PublisherDailyStat).where(PublisherDailyStat.slot_id.in_(created_slot_ids)))
                await session.execute(delete(AdSlot).where(AdSlot.id.in_(created_slot_ids)))
            if created_pub_ids:
                await session.execute(delete(Publisher).where(Publisher.id.in_(created_pub_ids)))
            if created_user_ids:
                await session.execute(delete(User).where(User.id.in_(created_user_ids)))
            await session.commit()
            print("[OK] All live test records cleaned up from database")
            await redis_client.close()


if __name__ == "__main__":
    asyncio.run(run_live_verification())
