import sys
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func
from liteads.models.ad import CampaignChangeLog, Suggestion
from liteads.common.config import get_settings

async def run(campaign_id: int, suggestion_id: int):
    settings = get_settings()
    engine = create_async_engine(settings.database.async_url)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with AsyncSessionLocal() as session:
        logs = (await session.execute(
            select(func.count(CampaignChangeLog.id)).where(CampaignChangeLog.campaign_id == campaign_id)
        )).scalar_one()
        sugg = (await session.execute(
            select(Suggestion).where(Suggestion.id == suggestion_id)
        )).scalar_one_or_none()
        sugg_status = sugg.status if sugg else "None"
        print(f"CHANGELOG_COUNT={logs}")
        print(f"SUGGESTION_STATUS={sugg_status}")

if __name__ == "__main__":
    asyncio.run(run(int(sys.argv[1]), int(sys.argv[2])))
