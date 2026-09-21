"""
Finance API endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.billing import BillingService
from liteads.common.database import get_session
from liteads.common.logger import get_logger
from liteads.models import Advertiser, Publisher
from liteads.models.finance import Transaction
from liteads.schemas.finance import DepositRequest, LedgerResponse, PayoutRequest, TransactionResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("/advertiser/{advertiser_id}/ledger", response_model=LedgerResponse)
async def get_advertiser_ledger(
    advertiser_id: int,
    session: AsyncSession = Depends(get_session),
) -> LedgerResponse:
    """Get the financial ledger and balance for an advertiser."""
    adv = await session.get(Advertiser, advertiser_id)
    if not adv:
        raise HTTPException(status_code=404, detail="Advertiser not found")

    result = await session.execute(
        select(Transaction)
        .where(Transaction.advertiser_id == advertiser_id)
        .order_by(Transaction.created_at.desc())
        .limit(100)
    )
    transactions = result.scalars().all()

    return LedgerResponse(
        balance=adv.balance,
        transactions=[TransactionResponse.model_validate(tx) for tx in transactions],
    )


@router.post("/advertiser/{advertiser_id}/deposit", response_model=TransactionResponse)
async def deposit_funds(
    advertiser_id: int,
    request: DepositRequest,
    session: AsyncSession = Depends(get_session),
) -> TransactionResponse:
    """Deposit funds into an advertiser wallet."""
    try:
        tx = await BillingService.record_deposit(
            session=session,
            advertiser_id=advertiser_id,
            amount=request.amount,
            reference_id=request.reference_id,
        )
        await session.commit()
        await session.refresh(tx)
        return TransactionResponse.model_validate(tx)
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/publisher/{publisher_id}/ledger", response_model=LedgerResponse)
async def get_publisher_ledger(
    publisher_id: int,
    session: AsyncSession = Depends(get_session),
) -> LedgerResponse:
    """Get the financial ledger and balance for a publisher."""
    pub = await session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")

    result = await session.execute(
        select(Transaction)
        .where(Transaction.publisher_id == publisher_id)
        .order_by(Transaction.created_at.desc())
        .limit(100)
    )
    transactions = result.scalars().all()

    return LedgerResponse(
        balance=pub.balance,
        transactions=[TransactionResponse.model_validate(tx) for tx in transactions],
    )


@router.post("/publisher/{publisher_id}/payout", response_model=TransactionResponse)
async def request_payout(
    publisher_id: int,
    request: PayoutRequest,
    session: AsyncSession = Depends(get_session),
) -> TransactionResponse:
    """Request a payout from publisher earnings."""
    try:
        tx = await BillingService.record_payout(
            session=session,
            publisher_id=publisher_id,
            amount=request.amount,
            reference_id=request.reference_id,
        )
        await session.commit()
        await session.refresh(tx)
        return TransactionResponse.model_validate(tx)
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
