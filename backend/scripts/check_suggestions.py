import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from liteads.models.ad import Suggestion, Campaign
import datetime

async def get_suggestions():
    engine = create_async_engine("postgresql+asyncpg://adserver:adserver@localhost:5432/adserver")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        result = await session.execute(select(Suggestion).order_by(Suggestion.id.desc()))
        suggestions = result.scalars().all()
        print(f"Found {len(suggestions)} suggestions")
        for s in suggestions:
            print(f"ID: {s.id}, Campaign: {s.campaign_id}, Status: {s.status}, Action: {s.action['action']}")
            
        c_result = await session.execute(select(Campaign).order_by(Campaign.id.desc()).limit(1))
        c = c_result.scalars().first()
        if c:
            print(f"Latest campaign: ID {c.id}, Name {c.name}, Advertiser {c.advertiser_id}, Budget {c.budget_daily}, Autopilot {c.autopilot}")

if __name__ == "__main__":
    asyncio.run(get_suggestions())
