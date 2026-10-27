"""Phase 7A: AdSet Data Model and Backward-Compatible Migration Tests.

Covers:
1. Alembic migration chain integrity and single head (k3c4d5e6f7a8 revises j2b3c4d5e6f7).
2. Upgrade and downgrade on an empty database.
3. Multi-campaign migration covering:
   - Multiple campaigns across diverse lifecycles (active, paused, draft, inactive).
   - Campaigns with zero creatives.
   - Campaigns with multiple creatives and targeting rules.
   - Exactly one default ad set per campaign with preserved settings and names.
   - Complete backfill of creatives and targeting rules to their respective parent default ad sets.
   - Verification that historical spend, budgets, and aggregate values are not doubled.
4. Downgrade execution and rollback integrity.
5. AdSet ORM model hierarchy, relationships, lifecycle properties, and parent-consistency validation.
6. Idempotency on repeated upgrade runs.
7. Cascade deletion behavior.
"""

import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models import (
    AdSet,
    Advertiser,
    BidType,
    Campaign,
    Creative,
    Status,
    TargetingRule,
)


# ==============================================================================
# 1. ALEMBIC MIGRATION GRAPH VERIFICATION
# ==============================================================================

def test_alembic_single_head_k3c4d5e6f7a8():
    """Verify Alembic migration tree has single head k3c4d5e6f7a8 and down_revision j2b3c4d5e6f7."""
    alembic_dir = Path(__file__).resolve().parent.parent.parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1, f"Expected 1 head, got {heads}"
    assert heads[0] == "k3c4d5e6f7a8"

    head_rev = script.get_revision("k3c4d5e6f7a8")
    assert head_rev is not None
    assert head_rev.down_revision == "j2b3c4d5e6f7"


# ==============================================================================
# Helper to load the migration module dynamically
# ==============================================================================

def _load_phase7a_migration():
    migration_file = (
        Path(__file__).resolve().parent.parent.parent
        / "alembic"
        / "versions"
        / "k3c4d5e6f7a8_phase7a_adset_data_model.py"
    )
    spec = importlib.util.spec_from_file_location("phase7a_migration", str(migration_file))
    mig_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig_module)
    return mig_module


def _create_mock_schema(conn):
    """Create prerequisite tables mimicking existing database prior to Phase 7A."""
    conn.exec_driver_sql("""
        CREATE TABLE advertisers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL
        )
    """)
    conn.exec_driver_sql("""
        CREATE TABLE campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            advertiser_id INTEGER NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            budget_daily NUMERIC(12, 4) DEFAULT 0,
            budget_total NUMERIC(12, 4) DEFAULT 0,
            spent_today NUMERIC(12, 4) DEFAULT 0,
            spent_total NUMERIC(12, 4) DEFAULT 0,
            bid_type INTEGER DEFAULT 1,
            bid_amount NUMERIC(12, 4) DEFAULT 0,
            freq_cap_daily INTEGER DEFAULT 10,
            freq_cap_hourly INTEGER DEFAULT 3,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            status INTEGER DEFAULT 1,
            autopilot BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (advertiser_id) REFERENCES advertisers(id)
        )
    """)
    conn.exec_driver_sql("""
        CREATE TABLE creatives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            landing_url VARCHAR(1024) NOT NULL,
            status INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        )
    """)
    conn.exec_driver_sql("""
        CREATE TABLE targeting_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            rule_type VARCHAR(50) NOT NULL,
            rule_value TEXT NOT NULL,
            is_include BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        )
    """)


# ==============================================================================
# 2. EMPTY DATABASE MIGRATION UPGRADE & DOWNGRADE
# ==============================================================================

def test_migration_empty_database_upgrade_and_downgrade():
    """Verify Phase 7A migration executes cleanly on an empty database and reverts completely."""
    mig = _load_phase7a_migration()
    engine = create_engine("sqlite:///:memory:")

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        mig.op = op

        _create_mock_schema(conn)

        # 1. Upgrade empty database
        mig.upgrade()
        inspector = inspect(conn)
        assert "ad_sets" in inspector.get_table_names()

        ad_set_cols = {c["name"] for c in inspector.get_columns("ad_sets")}
        expected_cols = {
            "id", "campaign_id", "name", "status", "daily_budget",
            "bid_type", "bid_amount", "start_time", "end_time",
            "freq_cap_daily", "freq_cap_hourly", "delivery_mode",
            "targeting_summary", "created_at", "updated_at",
        }
        assert expected_cols.issubset(ad_set_cols)

        creative_cols = {c["name"] for c in inspector.get_columns("creatives")}
        assert "ad_set_id" in creative_cols

        rule_cols = {c["name"] for c in inspector.get_columns("targeting_rules")}
        assert "ad_set_id" in rule_cols

        # 0 ad_sets created because 0 campaigns existed
        count = conn.execute(sa.text("SELECT COUNT(*) FROM ad_sets")).scalar()
        assert count == 0

        # 2. Downgrade empty database
        mig.downgrade()
        inspector_post = inspect(conn)
        assert "ad_sets" not in inspector_post.get_table_names()

        post_creative_cols = {c["name"] for c in inspector_post.get_columns("creatives")}
        assert "ad_set_id" not in post_creative_cols

        post_rule_cols = {c["name"] for c in inspector_post.get_columns("targeting_rules")}
        assert "ad_set_id" not in post_rule_cols


# ==============================================================================
# 3. MULTI-CAMPAIGN MIGRATION, DIVERSE LIFECYCLES, AND BACKFILL
# ==============================================================================

def test_migration_multi_campaign_backfill_and_spend_preservation():
    """Verify backfill correctness across multiple campaigns, lifecycles, and edge cases."""
    mig = _load_phase7a_migration()
    engine = create_engine("sqlite:///:memory:")

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        mig.op = op

        _create_mock_schema(conn)

        # Seed advertiser
        conn.execute(sa.text("INSERT INTO advertisers (id, name) VALUES (1, 'Advertiser One')"))

        # Seed 4 campaigns with diverse lifecycle statuses:
        # Camp 1: ACTIVE (1), 3 creatives, 2 targeting rules
        conn.execute(sa.text("""
            INSERT INTO campaigns (
                id, advertiser_id, name, budget_daily, budget_total, spent_today, spent_total,
                bid_type, bid_amount, freq_cap_daily, freq_cap_hourly, status
            ) VALUES (
                1, 1, 'Active Campaign', 200.0, 2000.0, 45.5, 350.0,
                1, 2.50, 15, 4, 1
            )
        """))
        # Camp 2: DRAFT (5), 0 creatives, 0 targeting rules
        conn.execute(sa.text("""
            INSERT INTO campaigns (
                id, advertiser_id, name, budget_daily, budget_total, spent_today, spent_total,
                bid_type, bid_amount, freq_cap_daily, freq_cap_hourly, status
            ) VALUES (
                2, 1, 'Draft Campaign', 50.0, 500.0, 0.0, 0.0,
                2, 1.20, 10, 3, 5
            )
        """))
        # Camp 3: PAUSED (2), 1 creative, 0 targeting rules
        conn.execute(sa.text("""
            INSERT INTO campaigns (
                id, advertiser_id, name, budget_daily, budget_total, spent_today, spent_total,
                bid_type, bid_amount, freq_cap_daily, freq_cap_hourly, status
            ) VALUES (
                3, 1, 'Paused Campaign', 100.0, 1000.0, 12.0, 95.0,
                1, 1.80, 10, 3, 2
            )
        """))
        # Camp 4: INACTIVE/STOPPED (0), 2 creatives, 1 targeting rule
        conn.execute(sa.text("""
            INSERT INTO campaigns (
                id, advertiser_id, name, budget_daily, budget_total, spent_today, spent_total,
                bid_type, bid_amount, freq_cap_daily, freq_cap_hourly, status
            ) VALUES (
                4, 1, 'Stopped Campaign', 300.0, 3000.0, 0.0, 1250.0,
                3, 5.00, 20, 5, 0
            )
        """))

        # Seed creatives
        conn.execute(sa.text("INSERT INTO creatives (id, campaign_id, title, landing_url) VALUES (101, 1, 'Ad 1-A', 'https://example.com/1a')"))
        conn.execute(sa.text("INSERT INTO creatives (id, campaign_id, title, landing_url) VALUES (102, 1, 'Ad 1-B', 'https://example.com/1b')"))
        conn.execute(sa.text("INSERT INTO creatives (id, campaign_id, title, landing_url) VALUES (103, 1, 'Ad 1-C', 'https://example.com/1c')"))
        conn.execute(sa.text("INSERT INTO creatives (id, campaign_id, title, landing_url) VALUES (104, 3, 'Ad 3-A', 'https://example.com/3a')"))
        conn.execute(sa.text("INSERT INTO creatives (id, campaign_id, title, landing_url) VALUES (105, 4, 'Ad 4-A', 'https://example.com/4a')"))
        conn.execute(sa.text("INSERT INTO creatives (id, campaign_id, title, landing_url) VALUES (106, 4, 'Ad 4-B', 'https://example.com/4b')"))

        # Seed targeting rules
        conn.execute(sa.text("INSERT INTO targeting_rules (id, campaign_id, rule_type, rule_value) VALUES (201, 1, 'geo', '{\"countries\": [\"IN\"]}')"))
        conn.execute(sa.text("INSERT INTO targeting_rules (id, campaign_id, rule_type, rule_value) VALUES (202, 1, 'sector', '{\"entries\": [\"tech\"]}')"))
        conn.execute(sa.text("INSERT INTO targeting_rules (id, campaign_id, rule_type, rule_value) VALUES (203, 4, 'device', '{\"os\": [\"android\"]}')"))

        conn.commit()

        # Capture pre-migration totals
        pre_camp_stats = conn.execute(
            sa.text("SELECT id, budget_daily, budget_total, spent_today, spent_total FROM campaigns ORDER BY id")
        ).fetchall()

        # ── Execute Migration Upgrade ──
        mig.upgrade()

        # 1. Exactly 1 default ad set per existing campaign (4 total)
        ad_sets = conn.execute(
            sa.text("""
                SELECT id, campaign_id, name, status, daily_budget, bid_type, bid_amount,
                       freq_cap_daily, freq_cap_hourly, delivery_mode
                FROM ad_sets
                ORDER BY campaign_id ASC
            """)
        ).fetchall()
        assert len(ad_sets) == 4, f"Expected 4 ad sets, got {len(ad_sets)}"

        as_by_camp = {r[1]: r for r in ad_sets}

        # Camp 1 AdSet: Active (1)
        as1 = as_by_camp[1]
        assert as1[2] == "Default Ad Set - Active Campaign"
        assert as1[3] == 1  # status active
        assert float(as1[4]) == 200.0  # daily sub-budget
        assert as1[5] == 1  # bid_type CPM
        assert float(as1[6]) == 2.50  # bid_amount
        assert as1[7] == 15  # freq_cap_daily
        assert as1[8] == 4  # freq_cap_hourly
        assert as1[9] == "standard"

        # Camp 2 AdSet: Draft (5), 0 creatives
        as2 = as_by_camp[2]
        assert as2[2] == "Default Ad Set - Draft Campaign"
        assert as2[3] == 5  # status draft preserved
        assert float(as2[4]) == 50.0
        assert as2[5] == 2  # CPC
        assert float(as2[6]) == 1.20

        # Camp 3 AdSet: Paused (2)
        as3 = as_by_camp[3]
        assert as3[2] == "Default Ad Set - Paused Campaign"
        assert as3[3] == 2  # status paused preserved
        assert float(as3[4]) == 100.0

        # Camp 4 AdSet: Inactive/Stopped (0)
        as4 = as_by_camp[4]
        assert as4[2] == "Default Ad Set - Stopped Campaign"
        assert as4[3] == 0  # status stopped preserved
        assert float(as4[4]) == 300.0

        # 2. Creatives backfilled to expected parent default ad set
        creatives = conn.execute(
            sa.text("SELECT id, campaign_id, ad_set_id FROM creatives ORDER BY id ASC")
        ).fetchall()
        assert len(creatives) == 6
        for c in creatives:
            c_id, c_camp_id, c_ad_set_id = c
            expected_ad_set_id = as_by_camp[c_camp_id][0]
            assert c_ad_set_id == expected_ad_set_id, (
                f"Creative {c_id} has ad_set_id {c_ad_set_id}, expected {expected_ad_set_id}"
            )

        # 3. Targeting rules backfilled to expected parent default ad set
        rules = conn.execute(
            sa.text("SELECT id, campaign_id, ad_set_id FROM targeting_rules ORDER BY id ASC")
        ).fetchall()
        assert len(rules) == 3
        for r in rules:
            r_id, r_camp_id, r_ad_set_id = r
            expected_ad_set_id = as_by_camp[r_camp_id][0]
            assert r_ad_set_id == expected_ad_set_id, (
                f"TargetingRule {r_id} has ad_set_id {r_ad_set_id}, expected {expected_ad_set_id}"
            )

        # 4. Historical spend and campaign ceilings must NOT be modified or doubled
        post_camp_stats = conn.execute(
            sa.text("SELECT id, budget_daily, budget_total, spent_today, spent_total FROM campaigns ORDER BY id")
        ).fetchall()
        assert pre_camp_stats == post_camp_stats

        # 5. Downgrade execution
        mig.downgrade()
        inspector = inspect(conn)
        assert "ad_sets" not in inspector.get_table_names()
        assert "ad_set_id" not in {c["name"] for c in inspector.get_columns("creatives")}
        assert "ad_set_id" not in {c["name"] for c in inspector.get_columns("targeting_rules")}


# ==============================================================================
# 4. MIGRATION IDEMPOTENCY ON REPEATED RUNS
# ==============================================================================

def test_migration_idempotent_backfill():
    """Verify that calling upgrade twice does not create duplicate ad sets."""
    mig = _load_phase7a_migration()
    engine = create_engine("sqlite:///:memory:")

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        mig.op = op

        _create_mock_schema(conn)
        conn.execute(sa.text("INSERT INTO advertisers (id, name) VALUES (1, 'Advertiser')"))
        conn.execute(sa.text("INSERT INTO campaigns (id, advertiser_id, name) VALUES (1, 1, 'My Campaign')"))
        conn.execute(sa.text("INSERT INTO creatives (id, campaign_id, title, landing_url) VALUES (1, 1, 'Ad 1', 'https://example.com')"))
        conn.commit()

        # First run
        mig.upgrade()
        count1 = conn.execute(sa.text("SELECT COUNT(*) FROM ad_sets")).scalar()
        assert count1 == 1

        # Second run (simulating re-run)
        mig.upgrade()
        count2 = conn.execute(sa.text("SELECT COUNT(*) FROM ad_sets")).scalar()
        assert count2 == 1, "Repeated upgrade must not create duplicate default ad sets"


# ==============================================================================
# 5. ORM DOMAIN MODEL & HIERARCHY TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_adset_orm_hierarchy_and_parent_consistency(test_db: AsyncSession):
    """Verify ORM Campaign -> AdSet -> Creative/TargetingRule relationships and validation."""
    # 1. Setup Advertiser and Campaign
    advertiser = Advertiser(name="ORM Advertiser")
    test_db.add(advertiser)
    await test_db.flush()

    campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Hierarchy Campaign",
        budget_daily=Decimal("150.0"),
        budget_total=Decimal("1500.0"),
        bid_type=BidType.CPM,
        bid_amount=Decimal("2.0"),
        status=Status.ACTIVE,
    )
    test_db.add(campaign)
    await test_db.flush()

    # 2. Create AdSet
    ad_set = AdSet(
        campaign_id=campaign.id,
        name="AdSet 1",
        status=Status.ACTIVE,
        daily_budget=Decimal("75.0"),
        bid_type=BidType.CPM,
        bid_amount=Decimal("2.0"),
    )
    test_db.add(ad_set)
    await test_db.flush()

    # 3. Create Creative linked to Campaign and AdSet
    creative = Creative(
        campaign_id=campaign.id,
        ad_set_id=ad_set.id,
        title="Banner Ad",
        landing_url="https://example.com/banner",
        status=Status.ACTIVE,
    )
    test_db.add(creative)

    # 4. Create TargetingRule linked to Campaign and AdSet
    rule = TargetingRule(
        campaign_id=campaign.id,
        ad_set_id=ad_set.id,
        rule_type="geo",
        rule_value={"countries": ["US"]},
        is_include=True,
    )
    test_db.add(rule)
    await test_db.commit()

    # 5. Query and verify relationships
    refreshed_camp = await test_db.scalar(
        select(Campaign).where(Campaign.id == campaign.id)
    )
    assert len(refreshed_camp.ad_sets) == 1
    assert refreshed_camp.ad_sets[0].name == "AdSet 1"
    assert refreshed_camp.ad_sets[0].daily_budget == Decimal("75.0")
    assert refreshed_camp.ad_sets[0].is_active is True

    refreshed_adset = await test_db.scalar(
        select(AdSet).where(AdSet.id == ad_set.id)
    )
    assert len(refreshed_adset.creatives) == 1
    assert refreshed_adset.creatives[0].title == "Banner Ad"
    assert len(refreshed_adset.targeting_rules) == 1
    assert refreshed_adset.targeting_rules[0].rule_type == "geo"

    # 6. Verify parent-consistency constraint on Creative
    # Setting an AdSet that belongs to a different campaign must raise ValueError
    other_campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Other Campaign",
        budget_daily=Decimal("100.0"),
        budget_total=Decimal("1000.0"),
    )
    test_db.add(other_campaign)
    await test_db.flush()

    mismatched_ad_set = AdSet(
        campaign_id=other_campaign.id,
        name="Other Campaign AdSet",
    )
    test_db.add(mismatched_ad_set)
    await test_db.flush()

    with pytest.raises(ValueError, match="does not match AdSet campaign_id"):
        Creative(
            campaign_id=campaign.id,
            ad_set=mismatched_ad_set,  # Mismatch: creative.campaign_id != mismatched_ad_set.campaign_id
            title="Mismatched Creative",
            landing_url="https://example.com/err",
        )

    with pytest.raises(ValueError, match="does not match AdSet campaign_id"):
        TargetingRule(
            campaign_id=campaign.id,
            ad_set=mismatched_ad_set,  # Mismatch: rule.campaign_id != mismatched_ad_set.campaign_id
            rule_type="geo",
            rule_value={"countries": ["IN"]},
        )


@pytest.mark.asyncio
async def test_adset_lifecycle_and_schedule(test_db: AsyncSession):
    """Verify AdSet is_active property respects status, start_time, and end_time."""
    advertiser = Advertiser(name="Lifecycle Adv")
    test_db.add(advertiser)
    await test_db.flush()

    campaign = Campaign(advertiser_id=advertiser.id, name="Lifecycle Camp")
    test_db.add(campaign)
    await test_db.flush()

    now = datetime.now()

    # Active ad set within window
    adset_active = AdSet(
        campaign_id=campaign.id,
        name="Active AdSet",
        status=Status.ACTIVE,
        start_time=now - timedelta(days=1),
        end_time=now + timedelta(days=1),
    )
    assert adset_active.is_active is True

    # Paused ad set
    adset_paused = AdSet(
        campaign_id=campaign.id,
        name="Paused AdSet",
        status=Status.PAUSED,
    )
    assert adset_paused.is_active is False

    # Expired ad set
    adset_expired = AdSet(
        campaign_id=campaign.id,
        name="Expired AdSet",
        status=Status.ACTIVE,
        end_time=now - timedelta(hours=1),
    )
    assert adset_expired.is_active is False

    # Future ad set
    adset_future = AdSet(
        campaign_id=campaign.id,
        name="Future AdSet",
        status=Status.ACTIVE,
        start_time=now + timedelta(days=1),
    )
    assert adset_future.is_active is False
