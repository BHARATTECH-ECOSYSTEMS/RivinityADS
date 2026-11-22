"""
Phase 11: PostgreSQL-Backed Concurrency, Unique Index, Ledger & Rollback Verification.
Runs against an isolated, temporary PostgreSQL database (liteads_phase11_pg_audit_db).
Safe to run in local development or CI environments with a running PostgreSQL service.
Never touches production or developer databases.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from decimal import Decimal
from datetime import datetime, timezone

import asyncpg
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from liteads.common.config import get_settings
from liteads.models.ad import Advertiser, Campaign, Creative, AdEvent, CampaignAssignment
from liteads.models.base import EventType, Status
from liteads.models.publisher import Publisher
from liteads.models.finance import Transaction, PaymentRequest, PublisherPayout
from liteads.ad_server.services.billing import BillingService
from liteads.ad_server.services.conversion_service import ConversionService
from liteads.common.tracking_sig import sign_click_token
from liteads.common.alembic_utils import (
    get_authoritative_alembic_head,
    get_preceding_alembic_revision,
)

TEST_DB_NAME = "liteads_phase11_pg_audit_db"
PRE_CHANGE_REVISION = "09062f9ba9c2"
GEO_TARGET_CONSTRAINT = ("uq_geo_location_full", ("country", "state_code", "city", "pincode"))
GEO_LEGACY_CONSTRAINT = ("uq_country_pincode", ("country", "pincode"))


def redact(text: str) -> str:
    s = get_settings()
    if s.database.password:
        text = text.replace(s.database.password, "[REDACTED_DB_PASSWORD]")
    if s.auth.jwt_secret:
        text = text.replace(s.auth.jwt_secret, "[REDACTED_JWT_SECRET]")
    if s.tracking.secret:
        text = text.replace(s.tracking.secret, "[REDACTED_TRACKING_SECRET]")
    return text


def find_alembic_ini() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.abspath(os.path.join(base_dir, "..", "alembic.ini")),
        os.path.abspath(os.path.join(base_dir, "..", "..", "backend", "alembic.ini")),
        os.path.abspath("backend/alembic.ini"),
        os.path.abspath("alembic.ini"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError("Could not locate alembic.ini")


def find_baseline_sql() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, "init_db.sql")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not locate baseline schema: {path}")
    return path


async def run_alembic(alembic_ini: str, database_name: str, *arguments: str) -> str:
    env = os.environ.copy()
    env["LITEADS_DATABASE__NAME"] = database_name
    working_dir = os.path.dirname(alembic_ini)
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", alembic_ini, *arguments],
        env=env,
        capture_output=True,
        text=True,
        cwd=working_dir,
    )
    output = redact(res.stdout + "\n" + res.stderr).strip()
    if res.returncode != 0:
        raise RuntimeError(f"Alembic {' '.join(arguments)} failed:\n{output}")
    return output


async def assert_geo_constraint(
    conn: asyncpg.Connection,
    expected: tuple[str, tuple[str, ...]],
    absent: str,
) -> None:
    name, columns = expected
    row = await conn.fetchrow(
        """
        SELECT c.conname,
               array_agg(a.attname ORDER BY key.ord) AS columns
        FROM pg_constraint AS c
        JOIN unnest(c.conkey) WITH ORDINALITY AS key(attnum, ord) ON TRUE
        JOIN pg_attribute AS a
          ON a.attrelid = c.conrelid AND a.attnum = key.attnum
        WHERE c.conrelid = 'geo_locations'::regclass
          AND c.contype = 'u'
          AND c.conname = $1
        GROUP BY c.conname
        """,
        name,
    )
    assert row is not None, f"Missing unique constraint {name}"
    assert tuple(row["columns"]) == columns, (
        f"Unexpected columns for {name}: {tuple(row['columns'])!r}; "
        f"expected {columns!r}"
    )
    assert not await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'geo_locations'::regclass
              AND conname = $1
        )
        """,
        absent,
    ), f"Unexpected legacy constraint {absent}"


def is_ci_environment() -> bool:
    ci_val = os.environ.get("CI", "").strip().lower()
    gh_val = os.environ.get("GITHUB_ACTIONS", "").strip().lower()
    return ci_val in ("true", "1", "yes") or gh_val in ("true", "1", "yes")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 11: PostgreSQL-Backed Concurrency, Ledger & Rollback Verification"
    )
    parser.add_argument(
        "--allow-skip-if-unavailable",
        action="store_true",
        help="Allow graceful exit (code 0) ONLY in local non-CI environment if PostgreSQL is unreachable. Disallowed in CI.",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    is_ci = is_ci_environment()

    settings = get_settings()
    host = settings.database.host
    port = settings.database.port
    user = settings.database.user
    password = settings.database.password

    # Check PostgreSQL availability
    try:
        admin_conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database="postgres")
    except Exception as e:
        error_msg = f"PostgreSQL service is not reachable on {host}:{port}: {e}"
        if args.allow_skip_if_unavailable and not is_ci:
            print(f"SKIPPED (Local non-CI opt-in): {error_msg}")
            return 0

        if is_ci:
            print(f"FAIL CLOSED (CI environment detected): {error_msg}", file=sys.stderr)
        else:
            print(
                f"FAIL CLOSED: {error_msg}\n"
                "Pass --allow-skip-if-unavailable to allow local non-CI skip when database is offline.",
                file=sys.stderr,
            )
        return 1

    print("=== Step 1: Setting up isolated PostgreSQL test database ===")
    alembic_ini = find_alembic_ini()
    expected_head = get_authoritative_alembic_head(alembic_ini)
    print(f"Authoritative Alembic head from migration graph: {expected_head}")
    baseline_sql = open(find_baseline_sql(), encoding="utf-8").read()

    try:
        await admin_conn.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{TEST_DB_NAME}' AND pid <> pg_backend_pid();")
        await admin_conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME};")
        await admin_conn.execute(f"CREATE DATABASE {TEST_DB_NAME};")
        print(f"PASS: Isolated test database '{TEST_DB_NAME}' created.")
    finally:
        await admin_conn.close()

    try:
        print("\n=== Step 2: Loading the supported pre-Alembic baseline ===")
        baseline_conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=TEST_DB_NAME,
        )
        try:
            await baseline_conn.execute(baseline_sql)
        finally:
            await baseline_conn.close()
        print("PASS: Baseline schema loaded into an empty PostgreSQL database.")

        print("\n=== Step 3: Running clean Alembic upgrade to head ===")
        print(await run_alembic(alembic_ini, TEST_DB_NAME, "upgrade", "head"))
        current_out = await run_alembic(alembic_ini, TEST_DB_NAME, "current")
        print(current_out)
        assert expected_head in current_out, (
            f"Alembic current output {current_out!r} does not contain expected head {expected_head!r}"
        )
        geo_conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=TEST_DB_NAME,
        )
        try:
            # Verify the alembic_version table in PostgreSQL matches the authoritative head
            db_version = await geo_conn.fetchval("SELECT version_num FROM alembic_version")
            assert db_version == expected_head, (
                f"Isolated PostgreSQL alembic_version {db_version!r} does not match expected head {expected_head!r}"
            )
            print(f"PASS: Isolated PostgreSQL alembic_version matches authoritative head: {db_version}")

            await assert_geo_constraint(geo_conn, GEO_TARGET_CONSTRAINT, GEO_LEGACY_CONSTRAINT[0])
            print(
                "PASS: Clean PostgreSQL upgrade reached "
                f"{expected_head} with {GEO_TARGET_CONSTRAINT[0]} on "
                f"{GEO_TARGET_CONSTRAINT[1]}."
            )
        finally:
            await geo_conn.close()

        print("\n=== Step 4: Verifying the prior schema upgrade path ===")
        print(await run_alembic(alembic_ini, TEST_DB_NAME, "downgrade", PRE_CHANGE_REVISION))
        geo_conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=TEST_DB_NAME,
        )
        try:
            await assert_geo_constraint(geo_conn, GEO_LEGACY_CONSTRAINT, GEO_TARGET_CONSTRAINT[0])
            await geo_conn.execute(
                """
                INSERT INTO geo_locations (country, state_code, city, pincode)
                VALUES ('IN', 'KA', 'Bengaluru', '560001')
                """
            )
            print(
                "PASS: Pre-change schema contains "
                f"{GEO_LEGACY_CONSTRAINT[0]} on {GEO_LEGACY_CONSTRAINT[1]}."
            )
        finally:
            await geo_conn.close()

        print(await run_alembic(alembic_ini, TEST_DB_NAME, "upgrade", "head"))
        print(await run_alembic(alembic_ini, TEST_DB_NAME, "upgrade", "head"))
        geo_conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=TEST_DB_NAME,
        )
        try:
            await assert_geo_constraint(geo_conn, GEO_TARGET_CONSTRAINT, GEO_LEGACY_CONSTRAINT[0])
            assert await geo_conn.fetchval(
                """
                SELECT count(*)
                FROM geo_locations
                WHERE country = 'IN' AND state_code = 'KA'
                  AND city = 'Bengaluru' AND pincode = '560001'
                """
            ) == 1
            print(
                "PASS: Prior schema upgraded without data loss and repeated upgrade "
                "did not duplicate the geo constraint."
            )
        finally:
            await geo_conn.close()

        # Step 4B: Immediately preceding revision (head-1) downgrade and upgrade verification
        print("\n=== Step 4B: Verifying immediately preceding revision (head-1) downgrade and upgrade path ===")
        preceding_rev = get_preceding_alembic_revision(alembic_ini)
        if preceding_rev:
            print(f"Testing downgrade to preceding revision (head-1): {preceding_rev}")
            print(await run_alembic(alembic_ini, TEST_DB_NAME, "downgrade", preceding_rev))
            geo_conn = await asyncpg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=TEST_DB_NAME,
            )
            try:
                db_version = await geo_conn.fetchval("SELECT version_num FROM alembic_version")
                assert db_version == preceding_rev, (
                    f"Expected alembic_version {preceding_rev!r} after downgrade, got {db_version!r}"
                )
                print(f"PASS: Successfully downgraded to preceding revision {preceding_rev}")
            finally:
                await geo_conn.close()

            print(f"Testing re-upgrade from {preceding_rev} to head ({expected_head})")
            print(await run_alembic(alembic_ini, TEST_DB_NAME, "upgrade", "head"))
            geo_conn = await asyncpg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=TEST_DB_NAME,
            )
            try:
                db_version = await geo_conn.fetchval("SELECT version_num FROM alembic_version")
                assert db_version == expected_head, (
                    f"Expected alembic_version {expected_head!r} after re-upgrade, got {db_version!r}"
                )
                print(f"PASS: Successfully re-upgraded from {preceding_rev} to head {expected_head}")
            finally:
                await geo_conn.close()
        else:
            print("INFO: Single revision graph or no preceding revision detected; skipping head-1 step.")

        # Create async engine connected to isolated DB
        db_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{TEST_DB_NAME}"
        engine = create_async_engine(db_url, echo=False)
        async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        # Setup base entities
        async with async_session_factory() as session:
            adv = Advertiser(name="PG Audit Advertiser", balance=Decimal("1000.0000"))
            pub = Publisher(name="PG Audit Publisher", balance=Decimal("0.0000"))
            session.add_all([adv, pub])
            await session.flush()

            camp = Campaign(
                advertiser_id=adv.id,
                name="PG CPA Campaign",
                budget_total=Decimal("5000.0000"),
                budget_daily=Decimal("500.0000"),
                bid_type=3,  # CPA
                bid_amount=Decimal("50.0000"),
                status=Status.ACTIVE,
            )
            session.add(camp)
            await session.flush()

            assign = CampaignAssignment(campaign_id=camp.id, publisher_id=pub.id)
            creat = Creative(campaign_id=camp.id, title="PG Creative", landing_url="https://example.com", status=Status.ACTIVE)
            session.add_all([assign, creat])
            await session.commit()

            adv_id = adv.id
            pub_id = pub.id
            camp_id = camp.id
            creat_id = creat.id

        print(f"Setup complete: Advertiser={adv_id}, Campaign={camp_id}, Publisher={pub_id}")

        # ── Test 1: PostgreSQL-Enforced Unique Partial Index uq_ad_events_order_dedup ──
        print("\n=== Test 1: PostgreSQL Partial Unique Index Constraint Enforcement ===")
        async with async_session_factory() as session:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            ev1 = AdEvent(
                request_id="req_pg_1",
                campaign_id=camp_id,
                creative_id=creat_id,
                event_type=EventType.CONVERSION,
                event_time=now,
                external_order_id="ORD-PG-UNIQUE-1",
                is_attributed=True,
                is_test=False,
            )
            session.add(ev1)
            await session.commit()
            print("PASS: First non-test conversion inserted.")

            # Attempt duplicate insertion of non-test conversion with same order ID
            ev2 = AdEvent(
                request_id="req_pg_2",
                campaign_id=camp_id,
                creative_id=creat_id,
                event_type=EventType.CONVERSION,
                event_time=now,
                external_order_id="ORD-PG-UNIQUE-1",
                is_attributed=True,
                is_test=False,
            )
            session.add(ev2)
            try:
                await session.commit()
                print("FAIL: Duplicate order allowed!")
                return 1
            except IntegrityError as e:
                await session.rollback()
                assert "uq_ad_events_order_dedup" in str(e) or "unique constraint" in str(e).lower()
                print("PASS: PostgreSQL rejected duplicate non-test order with IntegrityError on uq_ad_events_order_dedup!")

            # Verify that is_test=True allows duplicate external_order_id per partial index filter
            ev_test1 = AdEvent(
                request_id="req_pg_test_1",
                campaign_id=camp_id,
                creative_id=creat_id,
                event_type=EventType.CONVERSION,
                event_time=now,
                external_order_id="ORD-PG-UNIQUE-1",
                is_attributed=True,
                is_test=True,
            )
            ev_test2 = AdEvent(
                request_id="req_pg_test_2",
                campaign_id=camp_id,
                creative_id=creat_id,
                event_type=EventType.CONVERSION,
                event_time=now,
                external_order_id="ORD-PG-UNIQUE-1",
                is_attributed=True,
                is_test=True,
            )
            session.add_all([ev_test1, ev_test2])
            await session.commit()
            print("PASS: Test events with identical order ID permitted by partial index WHERE clause!")

        # ── Test 2: Concurrent Duplicate Postbacks ──
        print("\n=== Test 2: Concurrent Duplicate Postbacks Resolution ===")
        async with async_session_factory() as session:
            click_concurrent = AdEvent(
                request_id="req_concurrent",
                campaign_id=camp_id,
                creative_id=creat_id,
                event_type=EventType.CLICK,
                event_time=now,
            )
            session.add(click_concurrent)
            await session.commit()

        tok_concurrent = sign_click_token("req_concurrent", camp_id, creat_id)

        async def postback_worker(worker_id: int):
            async with async_session_factory() as session:
                service = ConversionService(session)
                res = await service.track_conversion(
                    click_token=tok_concurrent,
                    order_id="ORD-CONCURRENT-RACE",
                    conversion_value=120.0,
                    currency="INR",
                    is_test=False,
                )
                await session.commit()
                return res

        res_a, res_b = await asyncio.gather(postback_worker(1), postback_worker(2))
        print(f"Worker A result: success={res_a.success}, duplicate={res_a.is_duplicate}, reason={res_a.attribution_reason}")
        print(f"Worker B result: success={res_b.success}, duplicate={res_b.is_duplicate}, reason={res_b.attribution_reason}")

        duplicates = [res_a.is_duplicate, res_b.is_duplicate]
        assert True in duplicates and False in duplicates, f"Expected exactly one duplicate, got {duplicates}"
        print("PASS: Concurrent postbacks gracefully deduplicated without unhandled exceptions!")

        # ── Test 3: Transaction Rollback on Billing Failure ──
        print("\n=== Test 3: Transaction Rollback on Insufficient Funds ===")
        async with async_session_factory() as session:
            click_fail = AdEvent(
                request_id="req_fail_charge",
                campaign_id=camp_id,
                creative_id=creat_id,
                event_type=EventType.CLICK,
                event_time=now,
            )
            session.add(click_fail)
            adv_obj = await session.get(Advertiser, adv_id)
            adv_obj.balance = Decimal("10.0000")  # Cost is 50, balance is only 10
            await session.commit()

            tok_fail = sign_click_token("req_fail_charge", camp_id, creat_id)
            service = ConversionService(session)
            raised_402 = False
            try:
                await service.track_conversion(
                    click_token=tok_fail,
                    order_id="ORD-FAIL-CHARGE",
                    conversion_value=50.0,
                    is_test=False,
                )
            except HTTPException as e:
                raised_402 = (e.status_code == 402)
                await session.rollback()

            assert raised_402, "Expected HTTPException(402)"

            q_ev = select(func.count()).where(AdEvent.external_order_id == "ORD-FAIL-CHARGE")
            cnt_ev = (await session.execute(q_ev)).scalar() or 0
            assert cnt_ev == 0, f"Expected 0 events, found {cnt_ev}"

            adv_check = await session.get(Advertiser, adv_id)
            assert adv_check.balance == Decimal("10.0000"), f"Balance corrupted: {adv_check.balance}"
            print("PASS: Billing failure rolled back completely; zero events and zero wallet charges!")

        # ── Test 4: Financial Ledger and Wallet Consistency ──
        print("\n=== Test 4: Financial Ledger and Wallet Consistency ===")
        async with async_session_factory() as session:
            click_success = AdEvent(
                request_id="req_success_charge",
                campaign_id=camp_id,
                creative_id=creat_id,
                event_type=EventType.CLICK,
                event_time=now,
            )
            session.add(click_success)
            adv_obj = await session.get(Advertiser, adv_id)
            adv_obj.balance = Decimal("500.0000")
            camp_obj = await session.get(Campaign, camp_id)
            prev_spent_today = camp_obj.spent_today
            prev_spent_total = camp_obj.spent_total
            pub_obj = await session.get(Publisher, pub_id)
            prev_pub_bal = pub_obj.balance
            await session.commit()

            tok_success = sign_click_token("req_success_charge", camp_id, creat_id)
            service = ConversionService(session)
            conv_res = await service.track_conversion(
                click_token=tok_success,
                order_id="ORD-LEDGER-VERIFY-1",
                conversion_value=300.0,
                is_test=False,
            )
            await session.commit()

            adv_after = await session.get(Advertiser, adv_id)
            camp_after = await session.get(Campaign, camp_id)
            pub_after = await session.get(Publisher, pub_id)

            assert adv_after.balance == Decimal("450.0000"), f"Expected 450.0000, got {adv_after.balance}"
            assert camp_after.spent_today == prev_spent_today + Decimal("50.0000"), f"Expected {prev_spent_today + Decimal('50.0000')}, got {camp_after.spent_today}"
            assert camp_after.spent_total == prev_spent_total + Decimal("50.0000"), f"Expected {prev_spent_total + Decimal('50.0000')}, got {camp_after.spent_total}"
            assert pub_after.balance == prev_pub_bal + Decimal("35.0000"), f"Expected {prev_pub_bal + Decimal('35.0000')}, got {pub_after.balance}"

            q_tx = select(Transaction).where(Transaction.reference_id == f"cpa:{camp_id}:ORD-LEDGER-VERIFY-1")
            tx = (await session.execute(q_tx)).scalar_one_or_none()
            assert tx is not None, "Missing spend transaction record in ledger"
            assert tx.amount == Decimal("-50.0000"), f"Expected -50.0000, got {tx.amount}"
            assert tx.advertiser_id == adv_id
            assert tx.campaign_id == camp_id
            assert tx.publisher_id == pub_id
            print(f"PASS: Ledger transaction {tx.id} recorded; advertiser debited 50, publisher credited 35.")

        # ── Test 5: Repeated Requests and Retry Idempotency ──
        print("\n=== Test 5: Repeated Requests Idempotency ===")
        async with async_session_factory() as session:
            service = ConversionService(session)
            retry_res = await service.track_conversion(
                click_token=tok_success,
                order_id="ORD-LEDGER-VERIFY-1",
                conversion_value=300.0,
                is_test=False,
            )
            await session.commit()
            assert retry_res.is_duplicate is True, "Expected duplicate flag on retry"

            adv_retry = await session.get(Advertiser, adv_id)
            assert adv_retry.balance == Decimal("450.0000"), f"Duplicate charge occurred! {adv_retry.balance}"
            print("PASS: Repeated request deduplicated idempotently with strictly zero duplicate charge!")

        # ── Test 6: Concurrent Duplicate Razorpay Webhook Deliveries ──
        print("\n=== Test 6: Concurrent Duplicate Razorpay Webhook Deliveries ===")
        async with async_session_factory() as session:
            adv_obj = await session.get(Advertiser, adv_id)
            initial_adv_balance = adv_obj.balance
            pr = PaymentRequest(
                advertiser_id=adv_id,
                amount=Decimal("250.0000"),
                currency="INR",
                provider_order_id="order_race_test_1",
                status="pending",
            )
            session.add(pr)
            await session.commit()

        async def webhook_delivery_worker(worker_id: int):
            async with async_session_factory() as session:
                try:
                    stmt = select(Transaction).where(Transaction.reference_id == "order_race_test_1")
                    res = await session.execute(stmt)
                    if res.scalar_one_or_none():
                        return {"status": "already_processed", "worker": worker_id}

                    await BillingService.record_deposit(
                        session=session,
                        advertiser_id=adv_id,
                        amount=Decimal("250.0000"),
                        currency="INR",
                        reference_id="order_race_test_1",
                        provider_event_id="evt_race_test_1",
                        description="Razorpay Top-up Concurrent",
                    )
                    await session.commit()
                    return {"status": "success", "worker": worker_id}
                except IntegrityError:
                    await session.rollback()
                    return {"status": "already_processed", "worker": worker_id}

        results = await asyncio.gather(webhook_delivery_worker(1), webhook_delivery_worker(2))
        print(f"Webhook delivery race results: {results}")
        statuses = [r["status"] for r in results]
        assert "success" in statuses, f"Expected at least one success, got {statuses}"
        assert statuses.count("success") == 1, f"Expected exactly one success, got {statuses}"
        assert statuses.count("already_processed") == 1, f"Expected exactly one duplicate caught, got {statuses}"

        async with async_session_factory() as session:
            adv_after_webhook = await session.get(Advertiser, adv_id)
            expected_adv_bal = initial_adv_balance + Decimal("250.0000")
            assert adv_after_webhook.balance == expected_adv_bal, (
                f"Advertiser balance mismatch: expected {expected_adv_bal}, got {adv_after_webhook.balance}"
            )
            tx_count_stmt = select(func.count()).where(Transaction.reference_id == "order_race_test_1")
            tx_count = (await session.execute(tx_count_stmt)).scalar()
            assert tx_count == 1, f"Expected exactly 1 ledger transaction, got {tx_count}"
            print("PASS: Concurrent duplicate Razorpay webhooks credited wallet strictly once with 1 immutable ledger row!")

        # ── Test 7: Competing Simultaneous Payout Requests Against Exact Available Balance ──
        print("\n=== Test 7: Competing Simultaneous Payout Requests (No Overdraw) ===")
        async with async_session_factory() as session:
            pub_obj = await session.get(Publisher, pub_id)
            pub_obj.balance = Decimal("200.0000")
            await session.commit()

        async def payout_worker(worker_id: int):
            async with async_session_factory() as session:
                try:
                    payout, tx = await BillingService.request_payout(
                        session=session,
                        publisher_id=pub_id,
                        amount=Decimal("200.0000"),
                        reference_id=f"payout_race_worker_{worker_id}",
                    )
                    await session.commit()
                    return {"status": "success", "payout_id": payout.id, "worker": worker_id}
                except (ValueError, IntegrityError) as e:
                    await session.rollback()
                    return {"status": "rejected", "error": str(e), "worker": worker_id}

        payout_results = await asyncio.gather(payout_worker(1), payout_worker(2))
        print(f"Payout race results: {payout_results}")
        p_statuses = [r["status"] for r in payout_results]
        assert p_statuses.count("success") == 1, f"Expected exactly 1 success, got {p_statuses}"
        assert p_statuses.count("rejected") == 1, f"Expected exactly 1 rejection, got {p_statuses}"

        async with async_session_factory() as session:
            pub_check = await session.get(Publisher, pub_id)
            assert pub_check.balance == Decimal("0.0000"), (
                f"Publisher balance overdrawn! Expected 0.0000, got {pub_check.balance}"
            )
            q_po = select(PublisherPayout).where(PublisherPayout.publisher_id == pub_id, PublisherPayout.amount == Decimal("200.0000"))
            payouts = (await session.execute(q_po)).scalars().all()
            assert len(payouts) == 1, f"Expected 1 payout record, found {len(payouts)}"
            assert payouts[0].status == "requested"
            print("PASS: Competing payout requests allowed strictly 1 reservation and rejected the second; zero overdraw!")

        # ── Test 8: Concurrent Duplicate Impression / CPA Charges Deduplication ──
        print("\n=== Test 8: Concurrent Duplicate Impression / CPA Charges Deduplication ===")
        async with async_session_factory() as session:
            adv_obj = await session.get(Advertiser, adv_id)
            adv_bal_before_imp = adv_obj.balance

        canonical_imp_key = f"cpa:{camp_id}:ORD-CONCURRENT-CPA-DEDUP"

        async def charge_worker(worker_id: int):
            async with async_session_factory() as session:
                try:
                    res = await BillingService.process_cpa_charge(
                        session=session,
                        advertiser_id=adv_id,
                        campaign_id=camp_id,
                        cost=Decimal("25.0000"),
                        order_id="ORD-CONCURRENT-CPA-DEDUP",
                    )
                    await session.commit()
                    return {"status": "success", "result": res, "worker": worker_id}
                except IntegrityError:
                    await session.rollback()
                    return {"status": "duplicate_caught", "worker": worker_id}

        charge_results = await asyncio.gather(charge_worker(1), charge_worker(2))
        print(f"Charge race results: {charge_results}")

        async with async_session_factory() as session:
            adv_bal_after = (await session.get(Advertiser, adv_id)).balance
            expected_bal = adv_bal_before_imp - Decimal("25.0000")
            assert adv_bal_after == expected_bal, f"Expected {expected_bal}, got {adv_bal_after}"

            tx_stmt = select(func.count()).where(Transaction.reference_id == canonical_imp_key)
            tx_cnt = (await session.execute(tx_stmt)).scalar()
            assert tx_cnt == 1, f"Expected exactly 1 spend transaction, got {tx_cnt}"
            print("PASS: Concurrent duplicate charges deduplicated cleanly at PostgreSQL level with zero double billing!")


        await engine.dispose()
        print(f"\nALL POSTGRESQL CONCURRENCY & LEDGER AUDIT TESTS PASSED (HEAD {expected_head})!")
        return 0

    finally:
        print("\n=== Teardown: Dropping isolated test database ===")
        admin_conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database="postgres")
        try:
            await admin_conn.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{TEST_DB_NAME}' AND pid <> pg_backend_pid();")
            await admin_conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME};")
            print(f"PASS: Isolated test database '{TEST_DB_NAME}' dropped cleanly.")
        finally:
            await admin_conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
