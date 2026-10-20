"""
Finance API endpoints.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from liteads.ad_server.services.billing import BillingService
from liteads.common.database import get_session
from liteads.common.logger import get_logger
from liteads.common.security import current_user, verified_user
from liteads.models import Advertiser, Publisher, User
from liteads.models.finance import Transaction
from liteads.schemas.finance import (
    DepositRequest, 
    LedgerResponse, 
    PayoutRequest, 
    TransactionResponse,
    PaymentOrderRequest,
    PaymentOrderResponse,
)
from liteads.models.finance import Transaction, PaymentRequest
from liteads.ad_server.services.razorpay_client import get_razorpay_client, RazorpayClient, RazorpayClientError

logger = get_logger(__name__)
router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("/advertiser/{advertiser_id}/ledger", response_model=LedgerResponse)
async def get_advertiser_ledger(
    advertiser_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> LedgerResponse:
    """Get the financial ledger and balance for an advertiser."""
    adv = await session.get(Advertiser, advertiser_id)
    if not adv:
        raise HTTPException(status_code=404, detail="Advertiser not found")

    if user.role != "admin" and adv.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this ledger")

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
    user: User = Depends(verified_user),
) -> TransactionResponse:
    """Deposit funds into an advertiser wallet."""
    if user.role != "admin":
        raise HTTPException(
            status_code=403, 
            detail="Deposits must happen through the payment flow."
        )
        
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


@router.post("/advertiser/{advertiser_id}/payment-request", response_model=PaymentOrderResponse)
async def create_payment_request(
    advertiser_id: int,
    request: PaymentOrderRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> PaymentOrderResponse:
    """Create a Razorpay payment order/QR for wallet top-up."""
    adv = await session.get(Advertiser, advertiser_id)
    if not adv:
        raise HTTPException(status_code=404, detail="Advertiser not found")

    if user.role != "admin" and adv.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to create payment request for this advertiser")

    from liteads.ad_server.services.tools import request_payment, ToolError, ToolValidationError, ToolAuthorizationError
    try:
        # Re-use the existing service function that handles QR creation and persistence
        return await request_payment(user=user, session=session, amount=request.amount, advertiser_id=advertiser_id)
    except ToolValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ToolAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ToolError as e:
        logger.error(f"Payment request failed for adv {advertiser_id}: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    razorpay_client: RazorpayClient = Depends(get_razorpay_client),
) -> dict:
    """Process incoming Razorpay webhooks."""
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    
    if not signature:
        raise HTTPException(status_code=400, detail="Missing signature")
        
    if not razorpay_client.verify_webhook_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")
        
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    event = payload.get("event")
    
    if event not in ("order.paid", "qr_code.credited"):
        return {"status": "ignored", "reason": "unsupported event"}
        
    if event == "order.paid":
        order_entity = payload.get("payload", {}).get("order", {}).get("entity", {})
        if not order_entity:
            return {"status": "ignored", "reason": "missing order entity"}
            
        order_id = order_entity.get("id")
        amount_in_paise = order_entity.get("amount", 0)
        currency = order_entity.get("currency", "")
        receipt = order_entity.get("receipt", "")
        status = order_entity.get("status")
        
        if not order_id:
            return {"status": "ignored", "reason": "missing order_id"}
        if status != "paid":
            return {"status": "ignored", "reason": "order not paid"}
    else:
        # qr_code.credited
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        qr_code_entity = payload.get("payload", {}).get("qr_code", {}).get("entity", {})
        if not payment_entity or not qr_code_entity:
            return {"status": "ignored", "reason": "missing payment or qr_code entity"}
            
        order_id = qr_code_entity.get("id")
        amount_in_paise = payment_entity.get("amount", 0)
        currency = payment_entity.get("currency", "")
        receipt = payment_entity.get("notes", {}).get("receipt", "")
        status = payment_entity.get("status")
        
        if not order_id:
            return {"status": "ignored", "reason": "missing qr_id"}
        if status != "captured":
            return {"status": "ignored", "reason": "payment not captured"}
        
    if currency != "INR":
        return {"status": "ignored", "reason": "invalid currency"}
        
    amount = Decimal(str(amount_in_paise)) / Decimal("100")
    if amount <= Decimal("0"):
        return {"status": "ignored", "reason": "invalid amount"}
        
    if not receipt or not receipt.startswith("adv_"):
        return {"status": "ignored", "reason": "unrecognized receipt format"}
        
    try:
        advertiser_id = int(receipt.split("_")[1])
    except (IndexError, ValueError):
        return {"status": "ignored", "reason": "invalid advertiser_id in receipt"}
        
    try:
        # Pre-check to avoid unnecessary locks
        stmt = select(Transaction).where(Transaction.reference_id == order_id)
        result = await session.execute(stmt)
        if result.scalar_one_or_none():
            return {"status": "already_processed"}
            
        # Additional validation for QR payments against our PaymentRequest
        if event == "qr_code.credited":
            pr_stmt = select(PaymentRequest).where(PaymentRequest.provider_order_id == order_id)
            pr_result = await session.execute(pr_stmt)
            pr = pr_result.scalar_one_or_none()
            if not pr:
                return {"status": "ignored", "reason": "unknown payment request"}
            if pr.amount != amount:
                return {"status": "ignored", "reason": "amount mismatch"}
            if pr.currency != currency:
                return {"status": "ignored", "reason": "currency mismatch"}
            if pr.advertiser_id != advertiser_id:
                return {"status": "ignored", "reason": "advertiser mismatch"}
            
            pr.status = "paid"
            session.add(pr)
            
        await BillingService.record_deposit(
            session=session,
            advertiser_id=advertiser_id,
            amount=amount,
            reference_id=order_id,
            description=f"Razorpay Wallet Top-up (Order: {order_id})"
        )
        await session.commit()
        return {"status": "success"}
    except IntegrityError:
        # Duplicate delivery caught by DB constraint (concurrency safe)
        await session.rollback()
        return {"status": "already_processed"}
    except ValueError as e:
        await session.rollback()
        return {"status": "ignored", "reason": str(e)}


@router.get("/publisher/{publisher_id}/ledger", response_model=LedgerResponse)
async def get_publisher_ledger(
    publisher_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> LedgerResponse:
    """Get the financial ledger and balance for a publisher."""
    pub = await session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")

    if user.role != "admin" and pub.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this ledger")

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
    user: User = Depends(verified_user),
) -> TransactionResponse:
    """Request a payout from publisher earnings."""
    pub = await session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")

    if user.role != "admin" and pub.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to request payout for this publisher")

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
