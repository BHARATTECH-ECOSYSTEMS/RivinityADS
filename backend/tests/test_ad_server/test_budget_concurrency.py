import asyncio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from liteads.models import Advertiser, Campaign, Creative, Status
from liteads.ad_server.services.event_service import EventService

@pytest.mark.asyncio
async def test_budget_concurrency(test_db: AsyncSession):
    # Setup test data
    adv = Advertiser(name="Adv Concurrency", balance=100.0)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Camp Concurrency",
        budget_daily=10.0,
        budget_total=100.0,
        bid_amount=1.0,  # 1.0 per impression/click for easy math
        bid_type=1,      # CPM
        status=Status.ACTIVE,
        spent_today=0.0,
        spent_total=0.0
    )
    test_db.add(camp)
    await test_db.flush()

    creative = Creative(
        campaign_id=camp.id,
        title="Creative",
        landing_url="http://test.com",
        creative_type=1,
        status=Status.ACTIVE
    )
    test_db.add(creative)
    await test_db.flush()

    camp_id = camp.id
    creative_id = creative.id

    await test_db.commit()

    async def run_event():
        # we must use a separate session for each task for true concurrency,
        # but with SQLite in-memory, we can just use the same engine.
        from liteads.common.database import get_session
        from liteads.ad_server.main import app
        # To make it simple since we're in test_db fixture, we just create a new session
        # from the same engine as test_db
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession
        async_session = sessionmaker(test_db.bind, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            svc = EventService(session)
            await svc.track_event(
                request_id="req1",
                ad_id=f"{camp_id}_{creative_id}",
                event_type="impression",
                user_id="user1",
                timestamp=1700000000,
                extra=None
            )

    # Launch 20 concurrent requests.
    # The budget is 10.0 and bid is 1.0 (so 10 max).
    tasks = [run_event() for _ in range(20)]
    await asyncio.gather(*tasks)

    # Check budget
    async with test_db.begin():
        await test_db.refresh(camp)
        
    # the spent_today should not exceed 10.0!
    assert camp.spent_today <= 10.0
