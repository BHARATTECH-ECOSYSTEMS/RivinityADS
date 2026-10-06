import pytest
import asyncio
from decimal import Decimal
from unittest.mock import patch, MagicMock, AsyncMock
from sqlalchemy import select

from liteads.models.ad import Campaign, Advertiser, Suggestion, CampaignChangeLog
from liteads.models.base import Status
from liteads.ad_server.services.optimization_scheduler import OptimizationLoopTask
from liteads.ad_server.services.laya_client import LayaDecisionResult
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def advertiser(test_db):
    adv = Advertiser(
        name="Scheduler Test Advertiser",
        contact_email="scheduler@example.com",
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)
    return adv


async def test_scheduler_startup_shutdown():
    loop = OptimizationLoopTask(interval_seconds=900)
    
    assert loop._task is None
    
    with patch("liteads.ad_server.services.optimization_scheduler.OptimizationLoopTask._process_cycle", new_callable=AsyncMock) as mock_cycle:
        loop.start()
        assert loop._task is not None
        
        # Second start should not create duplicate task
        first_task = loop._task
        loop.start()
        assert loop._task is first_task
        
        # Run event loop briefly to ensure task schedules
        await asyncio.sleep(0.01)
        
        await loop.stop()
        assert loop._task.done()


@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_scheduler_interval(mock_sleep):
    loop = OptimizationLoopTask(interval_seconds=900)
    
    with patch("liteads.ad_server.services.optimization_scheduler.OptimizationLoopTask._process_cycle", new_callable=AsyncMock) as mock_cycle:
        # Run loop for just one iteration by raising CancelledError from sleep
        mock_sleep.side_effect = asyncio.CancelledError
        
        await loop._run_loop()
        
        mock_sleep.assert_called_once_with(900)
        mock_cycle.assert_awaited_once()


from contextlib import asynccontextmanager

@pytest.fixture
def mock_db_session_fixture(test_db):
    @asynccontextmanager
    async def _mock_db_session():
        yield test_db
    return _mock_db_session

async def test_scheduler_active_campaigns_only(test_db, advertiser, mock_db_session_fixture):
    camp_active = Campaign(
        advertiser_id=advertiser.id,
        name="Active Camp",
        budget_daily=Decimal("100.00"),
        status=Status.ACTIVE
    )
    camp_paused = Campaign(
        advertiser_id=advertiser.id,
        name="Paused Camp",
        budget_daily=Decimal("100.00"),
        status=Status.PAUSED
    )
    test_db.add_all([camp_active, camp_paused])
    await test_db.commit()
    await test_db.refresh(camp_active)
    await test_db.refresh(camp_paused)
    
    loop = OptimizationLoopTask(interval_seconds=900)
    
    with patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        with patch("liteads.ad_server.services.optimization_scheduler.OptimizationLoopTask._process_single_campaign", new_callable=AsyncMock) as mock_process:
            await loop._process_cycle()
            
            # Should only be called once, for the active campaign
            assert mock_process.call_count == 1
            called_camp_id = mock_process.call_args[0][1]
            assert called_camp_id == camp_active.id


@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPreparationService")
@patch("liteads.ad_server.services.optimization_scheduler.LayaDecisionClient")
async def test_scheduler_under_500_impressions(mock_laya_cls, mock_prep_cls, test_db, advertiser, mock_db_session_fixture):
    camp_active = Campaign(advertiser_id=advertiser.id, name="Active Camp", status=Status.ACTIVE)
    test_db.add(camp_active)
    await test_db.commit()
    await test_db.refresh(camp_active)
    
    mock_prep = mock_prep_cls.return_value
    mock_prep.prepare_context = MagicMock(return_value=MagicMock(eligible=False)) # <500 imp
    
    mock_laya = mock_laya_cls.return_value
    mock_laya.decide = AsyncMock()
    
    loop = OptimizationLoopTask(interval_seconds=900)
    with patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        await loop._process_cycle()
    
    mock_laya.decide.assert_not_called()


@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPreparationService")
@patch("liteads.ad_server.services.optimization_scheduler.LayaDecisionClient")
@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPolicyService")
@patch("liteads.ad_server.services.optimization_scheduler.OptimizationExecutor")
async def test_scheduler_laya_success(mock_exec_cls, mock_policy_cls, mock_laya_cls, mock_prep_cls, test_db, advertiser, mock_db_session_fixture):
    camp_active = Campaign(advertiser_id=advertiser.id, name="Active Camp", status=Status.ACTIVE)
    test_db.add(camp_active)
    await test_db.commit()
    await test_db.refresh(camp_active)
    
    mock_prep = mock_prep_cls.return_value
    mock_context = MagicMock(eligible=True)
    mock_prep.prepare_context = MagicMock(return_value=mock_context)
    
    mock_laya = mock_laya_cls.return_value
    mock_decision = MagicMock(target_payload={})
    mock_laya.decide = AsyncMock(return_value=mock_decision)
    
    mock_policy = mock_policy_cls.return_value
    mock_policy_result = MagicMock(accepted=True)
    mock_policy.evaluate.return_value = mock_policy_result
    
    mock_executor = mock_exec_cls.return_value
    mock_executor.execute = AsyncMock()
    
    loop = OptimizationLoopTask(interval_seconds=900)
    with patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        await loop._process_cycle()
    
    mock_laya.decide.assert_awaited_once_with(mock_context.state)
    mock_policy.evaluate.assert_called_once()
    mock_executor.execute.assert_awaited_once()


@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPreparationService")
@patch("liteads.ad_server.services.optimization_scheduler.LayaDecisionClient")
@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPolicyService")
@patch("liteads.ad_server.services.optimization_scheduler.OptimizationExecutor")
async def test_scheduler_laya_failure_isolation(mock_exec_cls, mock_policy_cls, mock_laya_cls, mock_prep_cls, test_db, advertiser, mock_db_session_fixture):
    camp1 = Campaign(advertiser_id=advertiser.id, name="Camp1", status=Status.ACTIVE)
    camp2 = Campaign(advertiser_id=advertiser.id, name="Camp2", status=Status.ACTIVE)
    test_db.add_all([camp1, camp2])
    await test_db.commit()
    
    mock_prep = mock_prep_cls.return_value
    mock_prep.prepare_context = MagicMock(return_value=MagicMock(eligible=True))
    
    mock_laya = mock_laya_cls.return_value
    mock_laya.decide = AsyncMock(side_effect=[Exception("Laya 502 Bad Gateway"), MagicMock(target_payload={})])
    
    mock_policy = mock_policy_cls.return_value
    mock_policy.evaluate.return_value = MagicMock(accepted=True)
    
    mock_executor = mock_exec_cls.return_value
    mock_executor.execute = AsyncMock()
    
    loop = OptimizationLoopTask(interval_seconds=900)
    with patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        await loop._process_cycle()
    
    assert mock_laya.decide.call_count == 2
    assert mock_executor.execute.call_count == 1


@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPreparationService")
@patch("liteads.ad_server.services.optimization_scheduler.LayaDecisionClient")
@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPolicyService")
@patch("liteads.ad_server.services.optimization_scheduler.OptimizationExecutor")
async def test_scheduler_policy_rejection(mock_exec_cls, mock_policy_cls, mock_laya_cls, mock_prep_cls, test_db, advertiser, mock_db_session_fixture):
    camp_active = Campaign(advertiser_id=advertiser.id, name="Active Camp", status=Status.ACTIVE)
    test_db.add(camp_active)
    await test_db.commit()
    
    mock_prep = mock_prep_cls.return_value
    mock_prep.prepare_context = MagicMock(return_value=MagicMock(eligible=True))
    
    mock_laya = mock_laya_cls.return_value
    mock_laya.decide = AsyncMock(return_value=MagicMock(target_payload={}))
    
    mock_policy = mock_policy_cls.return_value
    mock_policy.evaluate.return_value = MagicMock(accepted=False)
    
    mock_executor = mock_exec_cls.return_value
    mock_executor.execute = AsyncMock()
    
    loop = OptimizationLoopTask(interval_seconds=900)
    with patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        await loop._process_cycle()
    
    mock_executor.execute.assert_not_called()


@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPreparationService")
@patch("liteads.ad_server.services.optimization_scheduler.LayaDecisionClient")
@patch("liteads.ad_server.services.optimization_scheduler.OptimizationPolicyService")
@patch("liteads.ad_server.services.optimization_scheduler.OptimizationExecutor")
async def test_scheduler_executor_failure_isolation(mock_exec_cls, mock_policy_cls, mock_laya_cls, mock_prep_cls, test_db, advertiser, mock_db_session_fixture):
    camp1 = Campaign(advertiser_id=advertiser.id, name="Camp1", status=Status.ACTIVE)
    camp2 = Campaign(advertiser_id=advertiser.id, name="Camp2", status=Status.ACTIVE)
    test_db.add_all([camp1, camp2])
    await test_db.commit()
    
    mock_prep = mock_prep_cls.return_value
    mock_prep.prepare_context = MagicMock(return_value=MagicMock(eligible=True))
    
    mock_laya = mock_laya_cls.return_value
    mock_laya.decide = AsyncMock(return_value=MagicMock(target_payload={}))
    
    mock_policy = mock_policy_cls.return_value
    mock_policy.evaluate.return_value = MagicMock(accepted=True)
    
    mock_executor = mock_exec_cls.return_value
    mock_executor.execute = AsyncMock(side_effect=[Exception("DB Commit Failure"), None])
    
    loop = OptimizationLoopTask(interval_seconds=900)
    with patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        await loop._process_cycle()
    
    assert mock_executor.execute.call_count == 2
