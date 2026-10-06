import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone

from liteads.models.ad import Campaign, Advertiser, Suggestion, CampaignChangeLog

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def advertiser(test_db):
    adv = Advertiser(
        name="Test Phase 2 Advertiser",
        contact_email="phase2@example.com",
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)
    return adv


async def test_campaign_autopilot_default(test_db, advertiser):
    """A. newly created Campaign has autopilot=False"""
    camp = Campaign(
        advertiser_id=advertiser.id,
        name="Test Autopilot Campaign",
        budget_daily=100.0,
        budget_total=1000.0,
        bid_amount=1.5,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)
    
    assert camp.autopilot is False, "Campaign should have autopilot=False by default"


async def test_existing_data_compatibility(test_db, advertiser):
    """B. existing Campaign rows remain valid after migration, autopilot is false"""
    # Create a campaign, then manually execute SQL to simulate old row or verify read
    # Since alembic ran, all existing campaigns should have autopilot=False from server_default
    result = await test_db.execute(select(Campaign).limit(1))
    camp = result.scalar_one_or_none()
    if camp:
        assert camp.autopilot is False, "Existing campaign should have autopilot set to False"


async def test_suggestion_persistence(test_db, advertiser):
    """C. Suggestion persistence"""
    camp = Campaign(
        advertiser_id=advertiser.id,
        name="Test Suggestion Campaign",
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)

    suggestion = Suggestion(
        campaign_id=camp.id,
        action={"bid": 2.0, "reason": "high conversion"},
    )
    test_db.add(suggestion)
    await test_db.commit()
    await test_db.refresh(suggestion)

    assert suggestion.id is not None
    assert suggestion.campaign_id == camp.id
    assert suggestion.action == {"bid": 2.0, "reason": "high conversion"}
    assert suggestion.created_at is not None

    # Read back
    result = await test_db.execute(select(Suggestion).where(Suggestion.id == suggestion.id))
    db_suggestion = result.scalar_one()
    assert db_suggestion.action["bid"] == 2.0


async def test_campaign_changelog_persistence(test_db, advertiser):
    """D. CampaignChangeLog persistence"""
    camp = Campaign(
        advertiser_id=advertiser.id,
        name="Test Changelog Campaign",
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)

    changelog = CampaignChangeLog(
        campaign_id=camp.id,
        action={"status": "paused", "reason": "budget exhausted"},
    )
    test_db.add(changelog)
    await test_db.commit()
    await test_db.refresh(changelog)

    assert changelog.id is not None
    assert changelog.campaign_id == camp.id
    assert changelog.action == {"status": "paused", "reason": "budget exhausted"}
    assert changelog.created_at is not None

    # Read back
    result = await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.id == changelog.id))
    db_changelog = result.scalar_one()
    assert db_changelog.action["status"] == "paused"



