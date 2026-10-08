import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from liteads.models.ad import Campaign
from liteads.ad_server.services.optimization_executor import OptimizationExecutor
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult
from liteads.common.config import get_settings

async def run():
    settings = get_settings()
    engine = create_async_engine(settings.database.async_url)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with AsyncSessionLocal() as session:
        campaign = (await session.execute(select(Campaign).where(Campaign.id == 73))).scalar_one()
        executor = OptimizationExecutor(session)
        print("Running executor for campaign", campaign.id)
        # Suggestion creation logic requires autopilot=False
        action = OptimizationPolicyResult(
            accepted=True,
            reason="High confidence",
            action='increase_budget_20',
            confidence=0.85,
            autopilot=False,
            proposed_budget_delta=20.0,
            operation_type="BUDGET"
        )
        await executor.execute(campaign, action)
        await session.commit()
        print("Success")

if __name__ == "__main__":
    asyncio.run(run())
