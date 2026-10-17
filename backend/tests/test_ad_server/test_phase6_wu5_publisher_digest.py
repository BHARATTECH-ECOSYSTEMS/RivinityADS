"""Tests for Phase 6 Work Unit 5 (WU5):
Weekly Publisher Placement Digest and Production-Safe Scheduler.

Covers all 14 mandatory test cases and all 4 audit remediation areas:
1. Correct digest content for a publisher with eligible placements.
2. Correct best/worst ordering and deterministic tie-breaking.
3. Exclusion of slot_id IS NULL legacy rows.
4. Insufficient-data placements are not falsely presented as confirmed winners or losers.
5. No-data publisher receives an appropriate empty-state digest.
6. Digest includes only the target publisher's placements.
7. Aggregate benchmark information does not reveal competitor identities or individual data.
8. Missing/invalid publisher email is handled safely.
9. Email delivery failure is handled without stopping processing of other publishers.
10. Scheduler invokes the digest service on the intended cadence.
11. Scheduler cancellation and shutdown are handled correctly.
12. Repeated startup does not create duplicate background tasks in one process.
13. No publisher's data is included in another publisher's message.
14. Existing email functionality and fallback behavior remain unchanged.
15. (Finding 1) Five healthy placements: no healthy placement is labelled as needing attention.
16. (Finding 1) Mixture of healthy and low-visibility placements: only low-visibility placements appear in attention section.
17. (Finding 1) No low-visibility placements: attention section is omitted entirely.
18. (Finding 2) Scheduler restart delay safety: persists timestamp, delays on restart when interval has not elapsed.
19. (Finding 2) Scheduler failed cycle: does not update successful timestamp on delivery failure.
20. (Finding 3) Ownership-safe Redis lock acquisition and token-verified release.
21. (Finding 3) Ownership-checked Redis lock lease renewal.
22. (Finding 4) Redis unavailable: fails closed in uncoordinated multi-worker mode; permits single-worker mode when explicitly configured.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.digest_scheduler import (
    ACTIVE_CYCLE_KEY,
    ACTIVE_CYCLE_TARGET_DATE_KEY,
    COMPLETED_CYCLE_RETENTION_TTL,
    DEFAULT_DIGEST_INTERVAL_SECONDS,
    DISTRIBUTED_LOCK_KEY,
    LAST_SUCCESSFUL_CYCLE_KEY,
    WeeklyDigestTask,
)
from liteads.ad_server.services.placement_analyzer import (
    PlacementAnalysisResult,
    VERDICT_GREAT,
    VERDICT_HEALTHY,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_LOW_VISIBILITY,
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
from liteads.common.email import EmailDeliveryError
from liteads.models.auth import User
from liteads.models.base import CreativeType, Status
from liteads.models.publisher import AdSlot, Publisher, PublisherDailyStat


def _make_stat(
    publisher_id: int,
    slot_id: int | None,
    stat_date: date,
    impressions: int = 500,
    clicks: int = 15,
    conversions: int = 1,
    earnings: Decimal = Decimal("25.00"),
) -> PublisherDailyStat:
    return PublisherDailyStat(
        publisher_id=publisher_id,
        slot_id=slot_id,
        stat_date=stat_date,
        impressions=impressions,
        clicks=clicks,
        conversions=conversions,
        earnings=earnings,
    )


@pytest.fixture
async def setup_digest_publishers(test_db: AsyncSession):
    """Seed base publishers, user accounts, and slots."""
    user1 = User(
        id=501,
        email="pub_alpha_owner@example.com",
        name="pub_alpha",
        role="publisher",
        password_hash="fakehashpw1234567890",
        is_active=True,
    )
    user2 = User(
        id=502,
        email="pub_beta_owner@example.com",
        name="pub_beta",
        role="publisher",
        password_hash="fakehashpw1234567890",
        is_active=True,
    )
    test_db.add_all([user1, user2])
    await test_db.flush()

    pub1 = Publisher(
        id=601,
        name="Alpha Media",
        email="reports@alphamedia.com",
        owner_user_id=501,
        status=Status.ACTIVE,
        balance=Decimal("500.00"),
    )
    pub2 = Publisher(
        id=602,
        name="Beta Network",
        email="digest@betanetwork.org",
        owner_user_id=502,
        status=Status.ACTIVE,
        balance=Decimal("300.00"),
    )
    test_db.add_all([pub1, pub2])

    slot_alpha_top = AdSlot(
        id=701,
        publisher_id=601,
        slot_id="alpha_slot_top",
        name="Alpha Top Header Banner",
        format=CreativeType.BANNER,
        width=728,
        height=90,
        status=Status.ACTIVE,
    )
    slot_alpha_side = AdSlot(
        id=702,
        publisher_id=601,
        slot_id="alpha_slot_side",
        name="Alpha Sidebar Banner",
        format=CreativeType.BANNER,
        width=300,
        height=250,
        status=Status.ACTIVE,
    )
    slot_alpha_new = AdSlot(
        id=703,
        publisher_id=601,
        slot_id="alpha_slot_new",
        name="Alpha In-Stream Video (New)",
        format=CreativeType.VIDEO,
        width=640,
        height=360,
        status=Status.ACTIVE,
    )

    slot_beta_main = AdSlot(
        id=704,
        publisher_id=602,
        slot_id="beta_slot_main",
        name="Beta Main Hero",
        format=CreativeType.BANNER,
        width=728,
        height=90,
        status=Status.ACTIVE,
    )

    test_db.add_all([slot_alpha_top, slot_alpha_side, slot_alpha_new, slot_beta_main])
    await test_db.commit()


@pytest.mark.asyncio
async def test_1_correct_digest_content_for_eligible_placements(
    test_db: AsyncSession, setup_digest_publishers
):
    """1. Correct digest content for a publisher with eligible placements."""
    today = date(2026, 10, 17)
    s1 = _make_stat(601, 701, today - timedelta(days=2), impressions=1000, clicks=30, earnings=Decimal("60.00"))
    s2 = _make_stat(601, 702, today - timedelta(days=5), impressions=500, clicks=5, earnings=Decimal("15.00"))
    test_db.add_all([s1, s2])
    await test_db.commit()

    service = PublisherDigestService(test_db)
    pub = await test_db.get(Publisher, 601)

    analyses = await service.analyzer.analyze_publisher_placements(601, as_of_date=today)
    text_content, html_content = service.build_digest_content(
        publisher=pub,
        analyses=analyses,
        start_date="2026-09-18",
        end_date="2026-10-17",
    )

    assert "Weekly Placement Digest for Alpha Media" in text_content
    assert "Alpha Top Header Banner" in text_content
    assert "Alpha Sidebar Banner" in text_content
    assert "Total Impressions: 1,500" in text_content
    assert "Total Clicks: 35" in text_content
    assert "₹75.00" in text_content
    assert "TOP PERFORMING PLACEMENTS" in text_content

    assert "Alpha Media" in html_content
    assert "alpha_slot_top" in html_content
    assert "1,500" in html_content
    assert "badge-great" in html_content or "badge-healthy" in html_content


@pytest.mark.asyncio
async def test_2_correct_best_worst_ordering_and_deterministic_tie_breaking():
    """2. Correct best/worst ordering and deterministic tie-breaking."""
    analyses = [
        PlacementAnalysisResult(
            slot_id="slot_b",
            slot_db_id=2,
            slot_name="Slot B",
            publisher_id=1,
            format=CreativeType.BANNER,
            window_days=30,
            start_date="2026-09-18",
            end_date="2026-10-17",
            impressions=500,
            clicks=25,
            conversions=1,
            earnings=25.0,
            ctr=0.05,
            ecpm=50.0,
            verdict=VERDICT_GREAT,
            recommendation="Raise floor",
            benchmark={},
        ),
        PlacementAnalysisResult(
            slot_id="slot_a",
            slot_db_id=1,
            slot_name="Slot A",
            publisher_id=1,
            format=CreativeType.BANNER,
            window_days=30,
            start_date="2026-09-18",
            end_date="2026-10-17",
            impressions=500,
            clicks=25,
            conversions=1,
            earnings=25.0,
            ctr=0.05,
            ecpm=50.0,
            verdict=VERDICT_GREAT,
            recommendation="Raise floor",
            benchmark={},
        ),
        PlacementAnalysisResult(
            slot_id="slot_low",
            slot_db_id=3,
            slot_name="Slot Low",
            publisher_id=1,
            format=CreativeType.BANNER,
            window_days=30,
            start_date="2026-09-18",
            end_date="2026-10-17",
            impressions=200,
            clicks=0,
            conversions=0,
            earnings=1.0,
            ctr=0.0,
            ecpm=5.0,
            verdict=VERDICT_LOW_VISIBILITY,
            recommendation="Move above fold",
            benchmark={},
        ),
    ]

    ranking = PublisherDigestService.categorize_and_rank_placements(analyses)

    assert len(ranking.top_placements) == 2
    assert ranking.top_placements[0].slot_id == "slot_a"
    assert ranking.top_placements[1].slot_id == "slot_b"

    assert len(ranking.bottom_placements) == 1
    assert ranking.bottom_placements[0].slot_id == "slot_low"


@pytest.mark.asyncio
async def test_3_exclusion_of_slot_id_is_null_legacy_rows(
    test_db: AsyncSession, setup_digest_publishers
):
    """3. Exclusion of slot_id IS NULL legacy rows."""
    today = date(2026, 10, 17)
    s1 = _make_stat(601, 701, today - timedelta(days=1), impressions=200, clicks=6)
    s_legacy = _make_stat(601, None, today - timedelta(days=1), impressions=50000, clicks=1000)
    test_db.add_all([s1, s_legacy])
    await test_db.commit()

    service = PublisherDigestService(test_db)
    pub = await test_db.get(Publisher, 601)

    analyses = await service.analyzer.analyze_publisher_placements(601, as_of_date=today)
    ranking = service.categorize_and_rank_placements(analyses)

    top_slot = next(p for p in ranking.top_placements if p.slot_id == "alpha_slot_top")
    assert top_slot.impressions == 200
    assert top_slot.impressions < 50000


@pytest.mark.asyncio
async def test_4_insufficient_data_placements_not_falsely_presented(
    test_db: AsyncSession, setup_digest_publishers
):
    """4. Insufficient-data placements are not falsely presented as confirmed winners or losers."""
    today = date(2026, 10, 17)
    s1 = _make_stat(601, 703, today - timedelta(days=1), impressions=50, clicks=10)
    test_db.add(s1)
    await test_db.commit()

    service = PublisherDigestService(test_db)
    pub = await test_db.get(Publisher, 601)

    analyses = await service.analyzer.analyze_publisher_placements(601, as_of_date=today)
    ranking = service.categorize_and_rank_placements(analyses)

    assert not any(p.slot_id == "alpha_slot_new" for p in ranking.top_placements)
    assert not any(p.slot_id == "alpha_slot_new" for p in ranking.bottom_placements)
    assert any(p.slot_id == "alpha_slot_new" for p in ranking.insufficient_data_placements)

    text_content, html_content = service.build_digest_content(
        publisher=pub, analyses=analyses, start_date="2026-09-18", end_date="2026-10-17"
    )
    assert "PLACEMENTS IN DATA COLLECTION" in text_content
    assert "Minimum 100 impressions required before performance health classification" in text_content


@pytest.mark.asyncio
async def test_5_no_data_publisher_empty_state_digest(
    test_db: AsyncSession, setup_digest_publishers
):
    """5. No-data publisher receives an appropriate empty-state digest."""
    today = date(2026, 10, 17)
    service = PublisherDigestService(test_db)
    pub = await test_db.get(Publisher, 602)

    analyses = await service.analyzer.analyze_publisher_placements(602, as_of_date=today)
    ranking = service.categorize_and_rank_placements(analyses)
    assert len(ranking.top_placements) == 0
    assert len(ranking.bottom_placements) == 0
    assert len(ranking.additional_placements) == 0
    assert ranking.total_impressions == 0

    text_content, html_content = service.build_digest_content(
        publisher=pub, analyses=analyses, start_date="2026-09-18", end_date="2026-10-17"
    )
    assert "Total Impressions: 0" in text_content


@pytest.mark.asyncio
async def test_6_digest_includes_only_target_publisher_placements(
    test_db: AsyncSession, setup_digest_publishers
):
    """6. Digest includes only the target publisher's placements."""
    today = date(2026, 10, 17)
    s1 = _make_stat(601, 701, today - timedelta(days=2), impressions=1500, clicks=45)
    s2 = _make_stat(602, 704, today - timedelta(days=2), impressions=2000, clicks=60)
    test_db.add_all([s1, s2])
    await test_db.commit()

    service = PublisherDigestService(test_db)
    pub_alpha = await test_db.get(Publisher, 601)

    analyses = await service.analyzer.analyze_publisher_placements(601, as_of_date=today)
    text_content, html_content = service.build_digest_content(
        publisher=pub_alpha, analyses=analyses, start_date="2026-09-18", end_date="2026-10-17"
    )

    assert "alpha_slot_top" in text_content
    assert "beta_slot_main" not in text_content
    assert "Beta Network" not in text_content
    assert "beta_slot_main" not in html_content


@pytest.mark.asyncio
async def test_7_aggregate_benchmark_does_not_reveal_competitor_identities(
    test_db: AsyncSession, setup_digest_publishers
):
    """7. Aggregate benchmark information does not reveal competitor identities or data."""
    today = date(2026, 10, 17)
    s_beta = _make_stat(602, 704, today - timedelta(days=1), impressions=1000, clicks=20)
    s_alpha = _make_stat(601, 701, today - timedelta(days=1), impressions=1000, clicks=30)
    test_db.add_all([s_beta, s_alpha])
    await test_db.commit()

    service = PublisherDigestService(test_db)
    pub_alpha = await test_db.get(Publisher, 601)

    analyses = await service.analyzer.analyze_publisher_placements(601, as_of_date=today)
    text_content, html_content = service.build_digest_content(
        publisher=pub_alpha, analyses=analyses, start_date="2026-09-18", end_date="2026-10-17"
    )

    assert "Beta Network" not in text_content
    assert "beta_slot_main" not in text_content
    assert "Beta Network" not in html_content


@pytest.mark.asyncio
async def test_8_missing_or_invalid_publisher_email_handled_safely(
    test_db: AsyncSession, setup_digest_publishers
):
    """8. Missing/invalid publisher email is handled safely."""
    bad_pub = Publisher(
        id=699,
        name="No Email Pub",
        email="not-an-email",
        owner_user_id=None,
        status=Status.ACTIVE,
    )
    test_db.add(bad_pub)
    await test_db.commit()

    service = PublisherDigestService(test_db)
    with patch("liteads.ad_server.services.publisher_digest_service.send_email", new_callable=AsyncMock) as mock_send:
        result = await service.send_digest_for_publisher(699)
        assert result is False
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_9_email_delivery_failure_isolated_in_batch(
    test_db: AsyncSession, setup_digest_publishers
):
    """9. Email delivery failure is handled without stopping processing of other publishers."""
    service = PublisherDigestService(test_db)

    async def mock_send_side_effect(to_address, subject, text_content, html_content=None):
        if "alphamedia.com" in to_address:
            raise EmailDeliveryError("SMTP connection timed out")
        return None

    with patch("liteads.ad_server.services.publisher_digest_service.send_email", side_effect=mock_send_side_effect):
        summary = await service.send_all_digests()

        assert summary["total"] >= 2
        assert summary["sent"] >= 1
        assert summary["failed_or_skipped"] >= 1


@pytest.mark.asyncio
async def test_10_scheduler_cadence_and_execution():
    """10. Scheduler invokes the digest service on the intended cadence."""
    scheduler = WeeklyDigestTask(interval_seconds=604800)
    assert scheduler.interval_seconds == DEFAULT_DIGEST_INTERVAL_SECONDS

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with patch.object(scheduler, "_process_cycle", new_callable=AsyncMock) as mock_cycle:
            with patch.object(scheduler, "get_remaining_interval", new_callable=AsyncMock) as mock_rem:
                mock_rem.return_value = 0.0
                mock_sleep.side_effect = asyncio.CancelledError
                await scheduler._run_loop()

                mock_cycle.assert_awaited_once()
                mock_sleep.assert_called_once_with(604800)


@pytest.mark.asyncio
async def test_11_scheduler_cancellation_and_clean_shutdown():
    """11. Scheduler cancellation and shutdown are handled correctly."""
    scheduler = WeeklyDigestTask(interval_seconds=604800)
    assert not scheduler.is_running

    with patch.object(scheduler, "_process_cycle", new_callable=AsyncMock):
        scheduler.start()
        assert scheduler.is_running

        await asyncio.sleep(0.01)

        await scheduler.stop()
        assert not scheduler.is_running
        assert scheduler._task.done()


@pytest.mark.asyncio
async def test_12_repeated_startup_idempotency():
    """12. Repeated startup does not create duplicate background tasks in one process."""
    scheduler = WeeklyDigestTask(interval_seconds=604800)

    with patch.object(scheduler, "_process_cycle", new_callable=AsyncMock):
        scheduler.start()
        first_task = scheduler._task
        assert scheduler.is_running

        scheduler.start()
        assert scheduler._task is first_task

        await scheduler.stop()


@pytest.mark.asyncio
async def test_13_no_publisher_data_in_another_publisher_message(
    test_db: AsyncSession, setup_digest_publishers
):
    """13. No publisher's data is included in another publisher's message."""
    today = date(2026, 10, 17)
    s1 = _make_stat(601, 701, today - timedelta(days=2), impressions=1200, clicks=36)
    s2 = _make_stat(602, 704, today - timedelta(days=2), impressions=800, clicks=16)
    test_db.add_all([s1, s2])
    await test_db.commit()

    service = PublisherDigestService(test_db)
    captured_emails = []

    async def mock_capture_email(to_address, subject, text_content, html_content=None):
        captured_emails.append({
            "to": to_address,
            "subject": subject,
            "text": text_content,
            "html": html_content,
        })

    with patch("liteads.ad_server.services.publisher_digest_service.send_email", side_effect=mock_capture_email):
        summary = await service.send_all_digests(as_of_date=today)

        assert len(captured_emails) == 2

        alpha_email = next(e for e in captured_emails if "alphamedia.com" in e["to"])
        beta_email = next(e for e in captured_emails if "betanetwork.org" in e["to"])

        assert "Alpha Media" in alpha_email["text"]
        assert "alpha_slot_top" in alpha_email["text"]
        assert "Beta Network" not in alpha_email["text"]
        assert "beta_slot_main" not in alpha_email["text"]

        assert "Beta Network" in beta_email["text"]
        assert "beta_slot_main" in beta_email["text"]
        assert "Alpha Media" not in beta_email["text"]
        assert "alpha_slot_top" not in beta_email["text"]


@pytest.mark.asyncio
async def test_14_existing_email_and_fallback_behavior(test_db: AsyncSession):
    """14. Existing email functionality and fallback behavior remain unchanged."""
    user = User(
        id=599,
        email="fallback_owner@domain.com",
        name="fallback_user",
        role="publisher",
        password_hash="pw",
        is_active=True,
    )
    test_db.add(user)
    await test_db.flush()

    pub = Publisher(
        id=698,
        name="Fallback Publisher",
        email=None,
        owner_user_id=599,
        status=Status.ACTIVE,
    )
    test_db.add(pub)
    await test_db.commit()

    service = PublisherDigestService(test_db)
    authoritative_email = await service.get_authoritative_publisher_email(pub)
    assert authoritative_email == "fallback_owner@domain.com"


# ==============================================================================
# REMEDIATION TESTS: FINDINGS 1, 2, 3, 4
# ==============================================================================

def _make_dummy_placement(
    slot_id: str,
    verdict: str,
    ctr: float = 0.02,
    ecpm: float = 20.0,
    impressions: int = 500,
) -> PlacementAnalysisResult:
    return PlacementAnalysisResult(
        slot_id=slot_id,
        slot_db_id=1,
        slot_name=f"Placement {slot_id}",
        publisher_id=101,
        format=CreativeType.BANNER,
        window_days=30,
        start_date="2026-09-18",
        end_date="2026-10-17",
        impressions=impressions,
        clicks=int(impressions * ctr),
        conversions=1,
        earnings=impressions * ecpm / 1000.0,
        ctr=ctr,
        ecpm=ecpm,
        verdict=verdict,
        recommendation="Maintain placement",
        benchmark={},
    )


@pytest.mark.asyncio
async def test_15_five_healthy_placements_no_attention_label():
    """Finding 1.1: Five healthy placements: no healthy placement is labelled as needing attention."""
    analyses = [
        _make_dummy_placement(f"slot_{i}", VERDICT_HEALTHY, ctr=0.01 + (i * 0.002))
        for i in range(1, 6)
    ]

    ranking = PublisherDigestService.categorize_and_rank_placements(analyses)
    assert len(ranking.top_placements) == 3
    assert len(ranking.bottom_placements) == 0  # Zero low_visibility
    assert len(ranking.additional_placements) == 2  # Remaining 2 healthy placements

    pub = Publisher(id=1, name="Clean Publisher", email="clean@example.com", status=Status.ACTIVE)
    dummy_service = PublisherDigestService(None)
    text_content, html_content = dummy_service.build_digest_content(pub, analyses, "2026-09-18", "2026-10-17")

    # Verify no misleading attention section is rendered
    assert "PLACEMENTS NEEDING ATTENTION" not in text_content
    assert "Placements Needing Attention" not in html_content
    assert "ADDITIONAL ACTIVE PLACEMENTS:" in text_content
    assert "Additional Active Placements" in html_content


@pytest.mark.asyncio
async def test_16_mixture_healthy_and_low_visibility():
    """Finding 1.2: Mixture of healthy and low-visibility: only low-visibility appears in attention section."""
    analyses = [
        _make_dummy_placement("slot_h1", VERDICT_HEALTHY, ctr=0.02),
        _make_dummy_placement("slot_h2", VERDICT_HEALTHY, ctr=0.018),
        _make_dummy_placement("slot_h3", VERDICT_HEALTHY, ctr=0.016),
        _make_dummy_placement("slot_h4", VERDICT_HEALTHY, ctr=0.014),
        _make_dummy_placement("slot_bad1", VERDICT_LOW_VISIBILITY, ctr=0.0005),
    ]

    ranking = PublisherDigestService.categorize_and_rank_placements(analyses)
    assert len(ranking.top_placements) == 3
    assert len(ranking.bottom_placements) == 1
    assert ranking.bottom_placements[0].slot_id == "slot_bad1"
    assert len(ranking.additional_placements) == 1
    assert ranking.additional_placements[0].slot_id == "slot_h4"

    pub = Publisher(id=1, name="Mixed Publisher", email="mixed@example.com", status=Status.ACTIVE)
    dummy_service = PublisherDigestService(None)
    text_content, html_content = dummy_service.build_digest_content(pub, analyses, "2026-09-18", "2026-10-17")

    assert "PLACEMENTS NEEDING ATTENTION:" in text_content
    assert "slot_bad1" in text_content
    assert "slot_h4" not in text_content.split("PLACEMENTS NEEDING ATTENTION:")[1].split("ADDITIONAL ACTIVE PLACEMENTS:")[0]
    assert "ADDITIONAL ACTIVE PLACEMENTS:" in text_content


@pytest.mark.asyncio
async def test_17_no_low_visibility_placements_omits_attention_section():
    """Finding 1.3: No low-visibility placements: attention section is completely omitted."""
    analyses = [
        _make_dummy_placement("slot_g1", VERDICT_GREAT, ctr=0.04),
        _make_dummy_placement("slot_h1", VERDICT_HEALTHY, ctr=0.015),
    ]

    ranking = PublisherDigestService.categorize_and_rank_placements(analyses)
    assert len(ranking.bottom_placements) == 0

    pub = Publisher(id=1, name="Good Pub", email="good@example.com", status=Status.ACTIVE)
    dummy_service = PublisherDigestService(None)
    text_content, html_content = dummy_service.build_digest_content(pub, analyses, "2026-09-18", "2026-10-17")

    assert "PLACEMENTS NEEDING ATTENTION" not in text_content
    assert "Placements Needing Attention" not in html_content


@pytest.mark.asyncio
async def test_18_scheduler_restart_delay_safety():
    """Finding 2: Scheduler restart delays execution if interval has not elapsed since last run."""
    scheduler = WeeklyDigestTask(interval_seconds=604800)

    # Simulate that a cycle was successfully executed 2 days ago (5 days = 432,000s remaining)
    two_days_ago = datetime.now(timezone.utc).timestamp() - (2 * 86400)
    with patch.object(redis_client, "_connected", True):
        with patch.object(redis_client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = str(two_days_ago)

            remaining = await scheduler.get_remaining_interval()
            assert 431000 < remaining <= 432000

    # Simulate restart loop waiting for the remaining duration
    with patch.object(scheduler, "get_remaining_interval", new_callable=AsyncMock) as mock_rem:
        mock_rem.return_value = 100.0
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with patch.object(scheduler, "_process_cycle", new_callable=AsyncMock) as mock_cycle:
                # Cancel on initial sleep
                mock_sleep.side_effect = asyncio.CancelledError
                await scheduler._run_loop()

                mock_sleep.assert_called_once_with(100.0)
                mock_cycle.assert_not_called()


@pytest.mark.asyncio
async def test_19_scheduler_failed_cycle_does_not_update_timestamp():
    """Finding 2: A cycle where all emails fail does not mark the timestamp as successful."""
    scheduler = WeeklyDigestTask(interval_seconds=604800)

    with patch.object(redis_client, "_connected", True):
        with patch.object(redis_client, "set", new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True
            with patch("liteads.ad_server.services.publisher_digest_service.PublisherDigestService.send_all_digests", new_callable=AsyncMock) as mock_send:
                # 2 active publishers, but 0 sent
                mock_send.return_value = {"total": 2, "sent": 0, "failed_or_skipped": 2}
                with patch.object(scheduler, "complete_active_cycle", new_callable=AsyncMock) as mock_complete:
                    summary = await scheduler._process_cycle()
                    mock_complete.assert_not_called()


@pytest.mark.asyncio
async def test_20_ownership_safe_redis_lock_and_release():
    """Finding 3: Redis lock uses unique token and ownership-checked release."""
    mock_underlying = AsyncMock()
    with patch.object(redis_client, "_connected", True), patch.object(redis_client, "_client", mock_underlying):
        mock_underlying.eval.return_value = 1  # 1 deleted
        res = await redis_client.release_lock("lock_key", "token_abc")
        assert res is True
        mock_underlying.eval.assert_called_once()

        mock_underlying.eval.return_value = 0  # 0 deleted (mismatched token)
        res_mismatch = await redis_client.release_lock("lock_key", "wrong_token")
        assert res_mismatch is False


@pytest.mark.asyncio
async def test_21_redis_lock_lease_renewal():
    """Finding 3: Ownership-checked Redis lock lease renewal."""
    mock_underlying = AsyncMock()
    with patch.object(redis_client, "_connected", True), patch.object(redis_client, "_client", mock_underlying):
        mock_underlying.eval.return_value = 1
        renewed = await redis_client.renew_lock("lock_key", "token_123", ttl=3600)
        assert renewed is True

        mock_underlying.eval.return_value = 0
        renewed_fail = await redis_client.renew_lock("lock_key", "expired_token", ttl=3600)
        assert renewed_fail is False


@pytest.mark.asyncio
async def test_22_redis_unavailable_fail_closed_and_degraded_mode():
    """Finding 4: Fail closed when Redis is unavailable in uncoordinated multi-worker mode."""
    scheduler = WeeklyDigestTask(interval_seconds=604800)

    # 1. Default: Redis disconnected and no degraded flag -> fails closed
    with patch.object(redis_client, "_connected", False):
        with patch.dict(os.environ, {}, clear=True):
            res = await scheduler._process_cycle()
            assert res.get("skipped_due_to_uncoordinated_degraded_mode") == 1

    # 2. Explicitly allowed single-worker degraded mode -> proceeds
    with patch.object(redis_client, "_connected", False):
        with patch.dict(os.environ, {"LITEADS_ENABLE_DIGEST_SCHEDULER_WITHOUT_REDIS": "true"}):
            with patch("liteads.ad_server.services.publisher_digest_service.PublisherDigestService.send_all_digests", new_callable=AsyncMock) as mock_send:
                mock_send.return_value = {"total": 1, "sent": 1, "failed_or_skipped": 0, "status": BATCH_STATUS_SUCCESS}
                @asynccontextmanager
                async def mock_session_ctx():
                    yield AsyncMock()

                with patch("liteads.ad_server.services.digest_scheduler.db.session", mock_session_ctx):
                    res = await scheduler._process_cycle()
                    assert res.get("sent") == 1


# ==============================================================================
# REMEDIATION TESTS: RETRY AND BATCH CONTRACT (FINDING 2 FOLLOW-UP)
# ==============================================================================

@pytest.mark.asyncio
async def test_23_batch_all_publisher_emails_succeed():
    """All publisher emails succeed: status is success, timestamp advances, loop sleeps full interval."""
    scheduler = WeeklyDigestTask(interval_seconds=604800, retry_interval_seconds=3600)

    @asynccontextmanager
    async def mock_session():
        yield AsyncMock()

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "set", new_callable=AsyncMock) as mock_set, \
         patch.object(redis_client, "release_lock", new_callable=AsyncMock), \
         patch("liteads.ad_server.services.digest_scheduler.db.session", mock_session):
        mock_set.return_value = True
        with patch.object(scheduler, "complete_active_cycle", new_callable=AsyncMock) as mock_complete:
            with patch("liteads.ad_server.services.publisher_digest_service.PublisherDigestService.send_all_digests", new_callable=AsyncMock) as mock_send:
                mock_send.return_value = {
                    "total": 3,
                    "sent": 3,
                    "failed_or_skipped": 0,
                    "status": BATCH_STATUS_SUCCESS,
                    "successful_publisher_ids": [101, 102, 103],
                    "failed_publisher_ids": [],
                }
                summary = await scheduler._process_cycle()
                assert summary["status"] == BATCH_STATUS_SUCCESS
                assert summary["sent"] == 3
                mock_complete.assert_awaited_once()

    # In loop: verify it schedules the full 7-day interval
    with patch.object(scheduler, "_process_cycle", new_callable=AsyncMock) as mock_cycle:
        with patch.object(scheduler, "get_remaining_interval", new_callable=AsyncMock) as mock_rem:
            mock_rem.return_value = 0.0
            mock_cycle.return_value = {"status": BATCH_STATUS_SUCCESS}
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = asyncio.CancelledError
                await scheduler._run_loop()
                mock_sleep.assert_called_once_with(604800)


@pytest.mark.asyncio
async def test_24_batch_all_deliveries_fail():
    """All deliveries fail: status is failure, timestamp does NOT advance, loop sleeps retry interval."""
    scheduler = WeeklyDigestTask(interval_seconds=604800, retry_interval_seconds=3600, max_retries=0)

    @asynccontextmanager
    async def mock_session():
        yield AsyncMock()

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "set", new_callable=AsyncMock) as mock_set, \
         patch.object(redis_client, "release_lock", new_callable=AsyncMock), \
         patch("liteads.ad_server.services.digest_scheduler.db.session", mock_session):
        mock_set.return_value = True
        with patch.object(scheduler, "complete_active_cycle", new_callable=AsyncMock) as mock_complete:
            with patch("liteads.ad_server.services.publisher_digest_service.PublisherDigestService.send_all_digests", new_callable=AsyncMock) as mock_send:
                mock_send.return_value = {
                    "total": 2,
                    "sent": 0,
                    "failed_or_skipped": 2,
                    "status": BATCH_STATUS_FAILURE,
                    "successful_publisher_ids": [],
                    "failed_publisher_ids": [101, 102],
                }
                summary = await scheduler._process_cycle()
                assert summary["status"] == BATCH_STATUS_FAILURE
                assert summary["sent"] == 0
                mock_complete.assert_not_called()

    # In loop: verify it schedules the 1-hour retry interval instead of 7 days
    with patch.object(scheduler, "_process_cycle", new_callable=AsyncMock) as mock_cycle:
        with patch.object(scheduler, "get_remaining_interval", new_callable=AsyncMock) as mock_rem:
            mock_rem.return_value = 0.0
            mock_cycle.return_value = {"status": BATCH_STATUS_FAILURE}
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = asyncio.CancelledError
                await scheduler._run_loop()
                mock_sleep.assert_called_once_with(3600)


@pytest.mark.asyncio
async def test_25_batch_partial_delivery_success_does_not_advance_weekly_timestamp():
    """Partial delivery success: timestamp does NOT advance, loop sleeps retry interval."""
    scheduler = WeeklyDigestTask(interval_seconds=604800, retry_interval_seconds=3600, max_retries=0)

    @asynccontextmanager
    async def mock_session():
        yield AsyncMock()

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "set", new_callable=AsyncMock) as mock_set, \
         patch.object(redis_client, "release_lock", new_callable=AsyncMock), \
         patch("liteads.ad_server.services.digest_scheduler.db.session", mock_session):
        mock_set.return_value = True
        with patch.object(scheduler, "complete_active_cycle", new_callable=AsyncMock) as mock_complete:
            with patch("liteads.ad_server.services.publisher_digest_service.PublisherDigestService.send_all_digests", new_callable=AsyncMock) as mock_send:
                mock_send.return_value = {
                    "total": 2,
                    "sent": 1,
                    "failed_or_skipped": 1,
                    "status": BATCH_STATUS_PARTIAL,
                    "successful_publisher_ids": [101],
                    "failed_publisher_ids": [102],
                }
                summary = await scheduler._process_cycle()
                assert summary["status"] == BATCH_STATUS_PARTIAL
                assert summary["sent"] == 1
                assert summary["failed_or_skipped"] == 1
                # Must NOT advance weekly timestamp!
                mock_complete.assert_not_called()

    # In loop: verify it schedules the 1-hour retry interval instead of 7 days
    with patch.object(scheduler, "_process_cycle", new_callable=AsyncMock) as mock_cycle:
        with patch.object(scheduler, "get_remaining_interval", new_callable=AsyncMock) as mock_rem:
            mock_rem.return_value = 0.0
            mock_cycle.return_value = {"status": BATCH_STATUS_PARTIAL}
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = asyncio.CancelledError
                await scheduler._run_loop()
                mock_sleep.assert_called_once_with(3600)


@pytest.mark.asyncio
async def test_26_retry_skips_already_delivered_publishers(test_db: AsyncSession, setup_digest_publishers):
    """A retry does not unnecessarily resend successful publishers."""
    today = date(2026, 10, 17)
    service = PublisherDigestService(test_db)

    delivered_emails = []

    async def mock_send(to_address, subject, text_content, html_content=None):
        delivered_emails.append(to_address)
        if "alphamedia.com" in to_address:
            # Alpha fails on first attempt
            if len([e for e in delivered_emails if "alphamedia.com" in e]) == 1:
                raise EmailDeliveryError("Transient SMTP error for Alpha")
        return None

    # Use in-memory set to simulate Redis tracking
    redis_mock_set = set()
    async def mock_smembers(key):
        return set(redis_mock_set)
    async def mock_sadd(key, val):
        redis_mock_set.add(val)
        return 1

    with patch.object(redis_client, "_connected", True):
        with patch.object(redis_client, "smembers", side_effect=mock_smembers), \
             patch.object(redis_client, "sadd", side_effect=mock_sadd), \
             patch.object(redis_client, "expire", new_callable=AsyncMock):
            with patch("liteads.ad_server.services.publisher_digest_service.send_email", side_effect=mock_send):
                # Attempt 1: Beta succeeds, Alpha fails
                summary_1 = await service.send_all_digests(as_of_date=today)
                assert summary_1["status"] == BATCH_STATUS_PARTIAL
                assert summary_1["sent"] == 1
                assert summary_1["failed_or_skipped"] == 1
                assert "602" in redis_mock_set  # Beta Network is pub 602
                assert "601" not in redis_mock_set  # Alpha Media is pub 601

                beta_count_attempt_1 = len([e for e in delivered_emails if "betanetwork.org" in e])
                assert beta_count_attempt_1 == 1

                # Attempt 2 (Retry): Both active publishers exist, but Beta is already delivered
                summary_2 = await service.send_all_digests(as_of_date=today)
                assert summary_2["status"] == BATCH_STATUS_SUCCESS
                assert summary_2["sent"] == 2
                assert summary_2["failed_or_skipped"] == 0
                assert summary_2["previously_sent_count"] == 1
                assert summary_2["newly_sent_count"] == 1

                # Crucial assertion: Beta was NOT sent a second email!
                beta_count_attempt_2 = len([e for e in delivered_emails if "betanetwork.org" in e])
                assert beta_count_attempt_2 == 1, "Beta received duplicate email on retry!"


@pytest.mark.asyncio
async def test_27_restart_timing_after_complete_vs_partial_success():
    """Restart timing remains correct after complete success; partial success allows immediate retry."""
    scheduler = WeeklyDigestTask(interval_seconds=604800)

    # 1. Complete success recorded 1 day ago -> delays 6 days
    one_day_ago = datetime.now(timezone.utc).timestamp() - 86400
    with patch.object(redis_client, "_connected", True):
        with patch.object(redis_client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = str(one_day_ago)
            remaining = await scheduler.get_remaining_interval()
            assert 518000 < remaining <= 518400

    # 2. Partial success occurred -> timestamp was never recorded (key is None or expired from 7+ days ago)
    with patch.object(redis_client, "_connected", True):
        with patch.object(redis_client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            remaining = await scheduler.get_remaining_interval()
            assert remaining == 0.0


@pytest.mark.asyncio
async def test_28_exceptions_and_cancellation_do_not_mark_successful():
    """Exceptions and cancellations do not incorrectly mark a batch successful."""
    scheduler = WeeklyDigestTask(interval_seconds=604800, retry_interval_seconds=3600)

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "set", new_callable=AsyncMock) as mock_set, \
         patch.object(redis_client, "release_lock", new_callable=AsyncMock):
        mock_set.return_value = True
        with patch.object(scheduler, "complete_active_cycle", new_callable=AsyncMock) as mock_complete:
            # Exception in DB session
            @asynccontextmanager
            async def failing_session():
                raise RuntimeError("Database connection pool exhausted")
                yield

            with patch("liteads.ad_server.services.digest_scheduler.db.session", failing_session):
                summary = await scheduler._process_cycle()
                assert summary["status"] == BATCH_STATUS_FAILURE
                assert "Database connection pool exhausted" in summary["error"]
                mock_complete.assert_not_called()

        # Cancellation during cycle
        with patch.object(scheduler, "complete_active_cycle", new_callable=AsyncMock) as mock_complete:
            @asynccontextmanager
            async def cancelled_session():
                raise asyncio.CancelledError()
                yield

            with patch("liteads.ad_server.services.digest_scheduler.db.session", cancelled_session):
                with pytest.raises(asyncio.CancelledError):
                    await scheduler._process_cycle()
                mock_complete.assert_not_called()


@pytest.mark.asyncio
async def test_29_partial_failure_retry_different_calendar_date(test_db: AsyncSession, setup_digest_publishers):
    """
    Batch starts on Day 1 where Pub A succeeds and Pub B fails.
    When scheduler retries on Day 2 (different calendar date):
    - Reuses the stable active cycle identity and target date.
    - Pub A is NOT resent on Day 2.
    - Pub B succeeds, completing the batch and applying post-completion retention.
    """
    day_1 = date(2026, 10, 17)
    day_2 = date(2026, 10, 18)

    scheduler = WeeklyDigestTask(max_retries=0)

    sent_emails_by_pub = {601: 0, 602: 0}

    async def mock_send(to_address, subject, text_content, html_content=None):
        if "alphamedia.com" in to_address:
            sent_emails_by_pub[601] += 1
            return None
        elif "betanetwork.org" in to_address:
            if sent_emails_by_pub[602] == 0:
                sent_emails_by_pub[602] += 1
                raise EmailDeliveryError("Temporary SMTP timeout on Day 1")
            sent_emails_by_pub[602] += 1
            return None
        return None

    redis_storage: dict[str, Any] = {}
    redis_sets: dict[str, set[str]] = {}
    redis_expirations: dict[str, int] = {}

    async def mock_get(key):
        return redis_storage.get(key)

    async def mock_set(key, val, ttl=None, nx=False):
        if nx and key in redis_storage:
            return False
        redis_storage[key] = val
        if ttl is not None:
            redis_expirations[key] = ttl
        return True

    async def mock_delete(key):
        redis_storage.pop(key, None)
        redis_sets.pop(key, None)
        return 1

    async def mock_sadd(key, val):
        if key not in redis_sets:
            redis_sets[key] = set()
        redis_sets[key].add(str(val))
        return 1

    async def mock_smembers(key):
        return set(redis_sets.get(key, set()))

    async def mock_expire(key, ttl):
        redis_expirations[key] = ttl
        return True

    async def mock_release_lock(key, token):
        if redis_storage.get(key) == token:
            redis_storage.pop(key, None)
            return True
        return False

    async def mock_finalize(active_cycle_key, active_target_date_key, last_successful_cycle_key, delivered_key, expected_cycle_id, timestamp_str, timestamp_ttl, retention_ttl, **kwargs):
        if redis_storage.get(active_cycle_key) != expected_cycle_id:
            return False
        redis_expirations[delivered_key] = retention_ttl
        redis_storage[last_successful_cycle_key] = timestamp_str
        redis_storage.pop(active_cycle_key, None)
        redis_storage.pop(active_target_date_key, None)
        return True

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "get", side_effect=mock_get), \
         patch.object(redis_client, "set", side_effect=mock_set), \
         patch.object(redis_client, "delete", side_effect=mock_delete), \
         patch.object(redis_client, "sadd", side_effect=mock_sadd), \
         patch.object(redis_client, "smembers", side_effect=mock_smembers), \
         patch.object(redis_client, "expire", side_effect=mock_expire), \
         patch.object(redis_client, "release_lock", side_effect=mock_release_lock), \
         patch.object(redis_client, "renew_lock", new_callable=AsyncMock, return_value=True), \
         patch.object(redis_client, "finalize_digest_cycle", side_effect=mock_finalize), \
         patch("liteads.ad_server.services.publisher_digest_service.send_email", side_effect=mock_send), \
         patch("liteads.ad_server.services.digest_scheduler.db.session") as mock_db_sess:

        @asynccontextmanager
        async def db_session_gen():
            yield test_db

        mock_db_sess.side_effect = db_session_gen

        # --- DAY 1 ---
        summary_day1 = await scheduler._process_cycle(as_of_date=day_1)
        assert summary_day1["status"] == BATCH_STATUS_PARTIAL
        assert summary_day1["sent"] == 1
        assert summary_day1["failed_or_skipped"] == 1
        day1_cycle_id = summary_day1["cycle_id"]
        assert day1_cycle_id is not None
        assert day1_cycle_id.startswith("cycle_20261017_")

        assert redis_storage.get(ACTIVE_CYCLE_KEY) == day1_cycle_id
        assert redis_storage.get(ACTIVE_CYCLE_TARGET_DATE_KEY) == "2026-10-17"
        assert LAST_SUCCESSFUL_CYCLE_KEY not in redis_storage

        assert sent_emails_by_pub[601] == 1
        assert sent_emails_by_pub[602] == 1

        # --- DAY 2 ---
        summary_day2 = await scheduler._process_cycle(as_of_date=day_2)
        assert summary_day2["status"] == BATCH_STATUS_SUCCESS
        assert summary_day2["sent"] == 2
        assert summary_day2["previously_sent_count"] == 1
        assert summary_day2["newly_sent_count"] == 1
        assert summary_day2["cycle_id"] == day1_cycle_id

        assert sent_emails_by_pub[601] == 1, "Pub 601 was resent on Day 2 despite succeeding on Day 1!"
        assert sent_emails_by_pub[602] == 2, "Pub 602 was not resent and succeeded on Day 2!"

        assert LAST_SUCCESSFUL_CYCLE_KEY in redis_storage
        assert ACTIVE_CYCLE_KEY not in redis_storage
        assert ACTIVE_CYCLE_TARGET_DATE_KEY not in redis_storage
        delivered_key = f"{DIGEST_DELIVERED_KEY_PREFIX}{day1_cycle_id}"
        assert redis_expirations.get(delivered_key) == COMPLETED_CYCLE_RETENTION_TTL


@pytest.mark.asyncio
async def test_30_scheduler_restart_during_incomplete_cycle(test_db: AsyncSession, setup_digest_publishers):
    """
    If the scheduler / worker restarts while a cycle is incomplete:
    - New scheduler instance discovers existing active cycle identity.
    - Successfully delivered publishers are preserved in the ledger.
    - Delivered publishers are not resent upon restart.
    """
    today = date(2026, 10, 17)
    existing_cycle_id = "cycle_20261017_restart99"

    redis_storage = {
        ACTIVE_CYCLE_KEY: existing_cycle_id,
        ACTIVE_CYCLE_TARGET_DATE_KEY: today.isoformat(),
    }
    delivered_key = f"{DIGEST_DELIVERED_KEY_PREFIX}{existing_cycle_id}"
    redis_sets = {
        delivered_key: {"601"}
    }
    redis_expirations = {}

    async def mock_get(key):
        return redis_storage.get(key)
    async def mock_set(key, val, ttl=None, nx=False):
        if nx and key in redis_storage:
            return False
        redis_storage[key] = val
        return True
    async def mock_delete(key):
        redis_storage.pop(key, None)
        return 1
    async def mock_smembers(key):
        return set(redis_sets.get(key, set()))
    async def mock_sadd(key, val):
        redis_sets.setdefault(key, set()).add(str(val))
        return 1
    async def mock_expire(key, ttl):
        redis_expirations[key] = ttl
        return True
    async def mock_release_lock(key, token):
        return True

    async def mock_finalize(active_cycle_key, active_target_date_key, last_successful_cycle_key, delivered_key, expected_cycle_id, timestamp_str, timestamp_ttl, retention_ttl, **kwargs):
        if redis_storage.get(active_cycle_key) != expected_cycle_id:
            return False
        redis_expirations[delivered_key] = retention_ttl
        redis_storage[last_successful_cycle_key] = timestamp_str
        redis_storage.pop(active_cycle_key, None)
        redis_storage.pop(active_target_date_key, None)
        return True

    new_scheduler = WeeklyDigestTask(max_retries=0)

    delivered_emails = []
    async def mock_send(to_address, subject, text_content, html_content=None):
        delivered_emails.append(to_address)
        return None

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "get", side_effect=mock_get), \
         patch.object(redis_client, "set", side_effect=mock_set), \
         patch.object(redis_client, "delete", side_effect=mock_delete), \
         patch.object(redis_client, "smembers", side_effect=mock_smembers), \
         patch.object(redis_client, "sadd", side_effect=mock_sadd), \
         patch.object(redis_client, "expire", side_effect=mock_expire), \
         patch.object(redis_client, "release_lock", side_effect=mock_release_lock), \
         patch.object(redis_client, "renew_lock", new_callable=AsyncMock, return_value=True), \
         patch.object(redis_client, "finalize_digest_cycle", side_effect=mock_finalize), \
         patch("liteads.ad_server.services.publisher_digest_service.send_email", side_effect=mock_send), \
         patch("liteads.ad_server.services.digest_scheduler.db.session") as mock_db_sess:

        @asynccontextmanager
        async def db_session_gen():
            yield test_db

        mock_db_sess.side_effect = db_session_gen

        cycle_id, target_date = await new_scheduler.get_or_create_active_cycle()
        assert cycle_id == existing_cycle_id
        assert target_date == today

        summary = await new_scheduler._process_cycle()
        assert summary["status"] == BATCH_STATUS_SUCCESS
        assert summary["previously_sent_count"] == 1
        assert summary["newly_sent_count"] == 1
        assert not any("alphamedia.com" in e for e in delivered_emails)
        assert any("betanetwork.org" in e for e in delivered_emails)


@pytest.mark.asyncio
async def test_31_cycle_incomplete_beyond_14_days_does_not_expire(test_db: AsyncSession, setup_digest_publishers):
    """
    Ensure delivery records for an incomplete cycle DO NOT have a 14-day TTL applied while incomplete.
    Only when the cycle completes successfully is the 14-day post-completion retention TTL set.
    """
    cycle_id = "cycle_long_running_1234"
    delivered_key = f"{DIGEST_DELIVERED_KEY_PREFIX}{cycle_id}"

    service = PublisherDigestService(test_db)

    expire_calls = []
    async def mock_expire(key, ttl):
        expire_calls.append((key, ttl))
        return True

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "sadd", new_callable=AsyncMock, return_value=1), \
         patch.object(redis_client, "smembers", new_callable=AsyncMock, return_value=set()), \
         patch.object(redis_client, "expire", side_effect=mock_expire):

        with patch("liteads.ad_server.services.publisher_digest_service.send_email", new_callable=AsyncMock):
            await service.send_all_digests(cycle_id=cycle_id)

            delivered_expires = [call for call in expire_calls if call[0] == delivered_key]
            assert len(delivered_expires) == 0, "TTL was incorrectly set on incomplete cycle ledger!"

    scheduler = WeeklyDigestTask()
    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "finalize_digest_cycle", new_callable=AsyncMock, return_value=True) as mock_final:

        success = await scheduler.complete_active_cycle(cycle_id)
        assert success is True
        mock_final.assert_awaited_once_with(
            active_cycle_key=ACTIVE_CYCLE_KEY,
            active_target_date_key=ACTIVE_CYCLE_TARGET_DATE_KEY,
            last_successful_cycle_key=LAST_SUCCESSFUL_CYCLE_KEY,
            delivered_key=delivered_key,
            expected_cycle_id=cycle_id,
            timestamp_str=mock_final.call_args.kwargs["timestamp_str"],
            timestamp_ttl=scheduler.interval_seconds * 2,
            retention_ttl=COMPLETED_CYCLE_RETENTION_TTL,
        )


@pytest.mark.asyncio
async def test_32_multi_worker_lock_contention_and_retry_safety(test_db: AsyncSession, setup_digest_publishers):
    """
    Multiple workers attempt to run the digest cycle simultaneously:
    - Worker 1 gets the distributed lock and processes the batch.
    - Worker 2 fails to acquire the lock and exits with skipped_due_to_lock=1.
    - Worker 2 does not corrupt state or advance the success timestamp.
    """
    worker1 = WeeklyDigestTask()
    worker2 = WeeklyDigestTask()

    redis_storage: dict[str, str] = {}

    async def mock_set(key, val, ttl=None, nx=False):
        if nx and key in redis_storage:
            return False
        redis_storage[key] = val
        return True

    async def mock_get(key):
        return redis_storage.get(key)

    async def mock_release_lock(key, token):
        if redis_storage.get(key) == token:
            redis_storage.pop(key, None)
            return True
        return False

    async def mock_finalize(active_cycle_key, active_target_date_key, last_successful_cycle_key, delivered_key, expected_cycle_id, timestamp_str, timestamp_ttl, retention_ttl, **kwargs):
        if redis_storage.get(active_cycle_key) != expected_cycle_id:
            return False
        redis_storage[last_successful_cycle_key] = timestamp_str
        redis_storage.pop(active_cycle_key, None)
        redis_storage.pop(active_target_date_key, None)
        return True

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "set", side_effect=mock_set), \
         patch.object(redis_client, "get", side_effect=mock_get), \
         patch.object(redis_client, "release_lock", side_effect=mock_release_lock), \
         patch.object(redis_client, "renew_lock", new_callable=AsyncMock, return_value=True), \
         patch.object(redis_client, "smembers", new_callable=AsyncMock, return_value=set()), \
         patch.object(redis_client, "sadd", new_callable=AsyncMock, return_value=1), \
         patch.object(redis_client, "expire", new_callable=AsyncMock, return_value=True), \
         patch.object(redis_client, "delete", new_callable=AsyncMock, return_value=True), \
         patch.object(redis_client, "finalize_digest_cycle", side_effect=mock_finalize), \
         patch("liteads.ad_server.services.publisher_digest_service.send_email", new_callable=AsyncMock):

        @asynccontextmanager
        async def db_session_gen():
            yield test_db

        with patch("liteads.ad_server.services.digest_scheduler.db.session", side_effect=db_session_gen):
            # Pre-acquire lock by Worker 1
            redis_storage[DISTRIBUTED_LOCK_KEY] = "worker-1-uuid"

            # Worker 2 attempts execution while Worker 1 holds lock
            summary_worker2 = await worker2._process_cycle()
            assert summary_worker2["status"] == BATCH_STATUS_NO_OP
            assert summary_worker2.get("skipped_due_to_lock") == 1
            assert LAST_SUCCESSFUL_CYCLE_KEY not in redis_storage

            # Release Worker 1's lock
            await redis_client.release_lock(DISTRIBUTED_LOCK_KEY, "worker-1-uuid")
            assert DISTRIBUTED_LOCK_KEY not in redis_storage

            # Now Worker 2 can acquire lock and run
            summary_worker2_retry = await worker2._process_cycle()
            assert summary_worker2_retry["status"] == BATCH_STATUS_SUCCESS
            assert LAST_SUCCESSFUL_CYCLE_KEY in redis_storage


@pytest.mark.asyncio
async def test_33_atomic_cycle_finalization_lua_success():
    """
    Verify redis_client.finalize_digest_cycle evaluates atomic Lua script correctly:
    - Verifies active cycle key matches expected cycle ID.
    - Applies retention TTL to delivered key.
    - Sets last successful cycle timestamp.
    - Deletes active cycle markers.
    """
    mock_client = AsyncMock()
    mock_client.eval.return_value = 1

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "_client", mock_client):

        success = await redis_client.finalize_digest_cycle(
            active_cycle_key=ACTIVE_CYCLE_KEY,
            active_target_date_key=ACTIVE_CYCLE_TARGET_DATE_KEY,
            last_successful_cycle_key=LAST_SUCCESSFUL_CYCLE_KEY,
            delivered_key=f"{DIGEST_DELIVERED_KEY_PREFIX}cycle_20261017_abc",
            expected_cycle_id="cycle_20261017_abc",
            timestamp_str="1729180800.0",
            timestamp_ttl=1209600,
            retention_ttl=COMPLETED_CYCLE_RETENTION_TTL,
        )
        assert success is True
        mock_client.eval.assert_called_once()
        call_args = mock_client.eval.call_args.args
        lua_script = call_args[0]
        num_keys = call_args[1]
        keys = call_args[2:6]
        argv = call_args[6:]

        assert num_keys == 4
        assert keys == (
            ACTIVE_CYCLE_KEY,
            ACTIVE_CYCLE_TARGET_DATE_KEY,
            LAST_SUCCESSFUL_CYCLE_KEY,
            f"{DIGEST_DELIVERED_KEY_PREFIX}cycle_20261017_abc",
        )
        assert argv == (
            "cycle_20261017_abc",
            "1729180800.0",
            "1209600",
            str(COMPLETED_CYCLE_RETENTION_TTL),
        )
        assert "current_active_id ~= ARGV[1]" in lua_script
        assert "redis.call('expire'" in lua_script
        assert "redis.call('set'" in lua_script
        assert "redis.call('del'" in lua_script


@pytest.mark.asyncio
async def test_34_atomic_cycle_finalization_refuses_mismatched_cycle_id():
    """
    Verify atomic finalization refuses to commit when active cycle ID does not match expected ID:
    - When active cycle ID is different or absent, Lua script returns 0.
    - complete_active_cycle returns False and no state is corrupted.
    """
    # 1. Test direct RedisClient Lua refusal
    mock_client = AsyncMock()
    mock_client.eval.return_value = 0  # Lua script returned 0 (refusal)

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "_client", mock_client):

        success = await redis_client.finalize_digest_cycle(
            active_cycle_key=ACTIVE_CYCLE_KEY,
            active_target_date_key=ACTIVE_CYCLE_TARGET_DATE_KEY,
            last_successful_cycle_key=LAST_SUCCESSFUL_CYCLE_KEY,
            delivered_key=f"{DIGEST_DELIVERED_KEY_PREFIX}cycle_wrong",
            expected_cycle_id="cycle_wrong",
            timestamp_str="1729180800.0",
            timestamp_ttl=1209600,
            retention_ttl=COMPLETED_CYCLE_RETENTION_TTL,
        )
        assert success is False

    # 2. Test Scheduler method handles refusal safely
    scheduler = WeeklyDigestTask()
    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "finalize_digest_cycle", new_callable=AsyncMock, return_value=False):

        result = await scheduler.complete_active_cycle("mismatched_cycle_id")
        assert result is False


@pytest.mark.asyncio
async def test_35_crash_boundary_recovery_prevents_duplicate_cycle(test_db: AsyncSession, setup_digest_publishers):
    """
    Demonstrate crash/interruption recovery boundaries:
    Boundary A: Crash BEFORE atomic finalization commit:
      - Active cycle keys and delivered publisher set remain intact in Redis.
      - On recovery, worker resumes existing cycle, skips already-delivered publishers,
        sends 0 duplicate emails, and commits finalization.
    Boundary B: Crash AFTER atomic finalization commit:
      - Finalization committed (active markers deleted, success timestamp recorded).
      - On recovery, worker reads get_remaining_interval() > 0 and delays.
      - Cannot create a duplicate cycle or send duplicate emails.
    """
    cycle_id = "cycle_20261017_crash_test"
    target_date = date(2026, 10, 17)
    delivered_key = f"{DIGEST_DELIVERED_KEY_PREFIX}{cycle_id}"

    # Simulating Redis storage
    redis_storage: dict[str, Any] = {
        ACTIVE_CYCLE_KEY: cycle_id,
        ACTIVE_CYCLE_TARGET_DATE_KEY: target_date.isoformat(),
    }
    # Both publishers (601 Alpha and 602 Beta) were delivered right before the crash
    redis_sets: dict[str, set[str]] = {
        delivered_key: {"601", "602"}
    }
    redis_expirations: dict[str, int] = {}

    async def mock_get(key):
        return redis_storage.get(key)

    async def mock_smembers(key):
        return set(redis_sets.get(key, set()))

    async def mock_sadd(key, val):
        redis_sets.setdefault(key, set()).add(str(val))
        return 1

    async def mock_finalize(active_cycle_key, active_target_date_key, last_successful_cycle_key, delivered_key, expected_cycle_id, timestamp_str, timestamp_ttl, retention_ttl, **kwargs):
        if redis_storage.get(active_cycle_key) != expected_cycle_id:
            return False
        redis_expirations[delivered_key] = retention_ttl
        redis_storage[last_successful_cycle_key] = timestamp_str
        redis_storage.pop(active_cycle_key, None)
        redis_storage.pop(active_target_date_key, None)
        return True

    # === BOUNDARY A: Crash happened BEFORE finalization was called ===
    # Notice: LAST_SUCCESSFUL_CYCLE_KEY is NOT in redis_storage yet.
    assert LAST_SUCCESSFUL_CYCLE_KEY not in redis_storage

    recovered_scheduler_a = WeeklyDigestTask(max_retries=0)
    sent_emails = []

    async def mock_send(to_address, subject, text_content, html_content=None):
        sent_emails.append(to_address)
        return None

    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "get", side_effect=mock_get), \
         patch.object(redis_client, "smembers", side_effect=mock_smembers), \
         patch.object(redis_client, "sadd", side_effect=mock_sadd), \
         patch.object(redis_client, "set", new_callable=AsyncMock, return_value=True), \
         patch.object(redis_client, "release_lock", new_callable=AsyncMock, return_value=True), \
         patch.object(redis_client, "renew_lock", new_callable=AsyncMock, return_value=True), \
         patch.object(redis_client, "finalize_digest_cycle", side_effect=mock_finalize), \
         patch("liteads.ad_server.services.publisher_digest_service.send_email", side_effect=mock_send), \
         patch("liteads.ad_server.services.digest_scheduler.db.session") as mock_db_sess:

        @asynccontextmanager
        async def db_session_gen():
            yield test_db

        mock_db_sess.side_effect = db_session_gen

        summary = await recovered_scheduler_a._process_cycle()
        assert summary["status"] == BATCH_STATUS_SUCCESS
        assert summary["previously_sent_count"] == 2
        assert summary["newly_sent_count"] == 0

        # ZERO DUPLICATE EMAILS SENT on recovery!
        assert len(sent_emails) == 0, f"Expected 0 emails, but sent: {sent_emails}"

        # Atomic finalization is now committed
        assert LAST_SUCCESSFUL_CYCLE_KEY in redis_storage
        assert ACTIVE_CYCLE_KEY not in redis_storage
        assert ACTIVE_CYCLE_TARGET_DATE_KEY not in redis_storage
        assert redis_expirations.get(delivered_key) == COMPLETED_CYCLE_RETENTION_TTL

    # === BOUNDARY B: Crash happened AFTER finalization was committed ===
    recovered_scheduler_b = WeeklyDigestTask(interval_seconds=604800)
    with patch.object(redis_client, "_connected", True), \
         patch.object(redis_client, "get", side_effect=mock_get):

        remaining = await recovered_scheduler_b.get_remaining_interval()
        assert 604700 < remaining <= 604800



