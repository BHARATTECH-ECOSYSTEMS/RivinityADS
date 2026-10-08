import sys
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from liteads.models.ad import Campaign
from liteads.ad_server.services.optimization_executor import OptimizationExecutor
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult
from liteads.common.config import get_settings

async def run(campaign_id: int):
    settings = get_settings()
    engine = create_async_engine(settings.database.async_url)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with AsyncSessionLocal() as session:
        campaign = (await session.execute(select(Campaign).where(Campaign.id == campaign_id))).scalar_one()
        executor = OptimizationExecutor(session)
        print("Running executor for campaign", campaign.id)
        # Create an increase budget suggestion
        action = OptimizationPolicyResult(
            accepted=True,
            reason="High confidence budget increase",
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
    if len(sys.argv) < 2:
        print("Usage: python trigger_suggestion.py <campaign_id>")
        sys.exit(1)
    asyncio.run(run(int(sys.argv[1])))
