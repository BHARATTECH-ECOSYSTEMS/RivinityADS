"""
Finance API endpoints.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from liteads.ad_server.services.billing import BillingService
from liteads.ad_server.services.reconciliation_service import ReconciliationService
from liteads.common.database import get_session
from liteads.common.logger import get_logger
from liteads.common.security import current_user, require_roles, verified_user
from liteads.models import Advertiser, Publisher, User
from liteads.models.finance import BillingDispute, PaymentRequest, PublisherPayout, Transaction
from liteads.schemas.finance import (
    DepositRequest, 
    DisputeCreate,
    DisputeResponse,
    DisputeResolveRequest,
    LedgerResponse, 
    PayoutConfirmRequest,
    PayoutRecordResponse,
    PayoutRejectRequest,
    PayoutRequest, 
    ReconciliationRequest,
    ReconciliationResponse,
    TransactionResponse,
    PaymentOrderRequest,
    PaymentOrderResponse,
)
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
            currency=request.currency,
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
            currency=currency,
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
    """Request a payout from publisher earnings. Holds funds in reservation pending admin/provider execution."""
    pub = await session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")

    if user.role != "admin" and pub.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to request payout for this publisher")

    try:
        payout, tx = await BillingService.request_payout(
            session=session,
            publisher_id=publisher_id,
            amount=request.amount,
            currency=request.currency,
            reference_id=request.reference_id,
        )
        await session.commit()
        await session.refresh(tx)
        return TransactionResponse.model_validate(tx)
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/publisher/{publisher_id}/payouts", response_model=list[PayoutRecordResponse])
async def list_publisher_payouts(
    publisher_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> list[PayoutRecordResponse]:
    """List all payout records and their lifecycle statuses for a publisher."""
    pub = await session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")

    if user.role != "admin" and pub.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view payouts for this publisher")

    stmt = (
        select(PublisherPayout)
        .where(PublisherPayout.publisher_id == publisher_id)
        .order_by(PublisherPayout.id.desc())
    )
    result = await session.execute(stmt)
    records = result.scalars().all()
    return [PayoutRecordResponse.model_validate(r) for r in records]


@router.post("/publisher/{publisher_id}/payout/{payout_id}/reject", response_model=TransactionResponse)
async def reject_publisher_payout(
    publisher_id: int,
    payout_id: int,
    request: PayoutRejectRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> TransactionResponse:
    """Reject a pending payout request and restore reserved funds to publisher balance (admin only)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin authorization required to reject payouts")

    try:
        _, reversal_tx = await BillingService.reject_payout(
            session=session,
            payout_id=payout_id,
            reason=request.reason,
        )
        await session.commit()
        await session.refresh(reversal_tx)
        return TransactionResponse.model_validate(reversal_tx)
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/publisher/{publisher_id}/payout/{payout_id}/confirm", response_model=PayoutRecordResponse)
async def confirm_publisher_payout(
    publisher_id: int,
    payout_id: int,
    request: PayoutConfirmRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> PayoutRecordResponse:
    """Confirm that an external payment provider successfully executed the payout (admin only)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin authorization required to confirm payouts")

    try:
        payout = await BillingService.confirm_payout(
            session=session,
            payout_id=payout_id,
            provider_payout_id=request.provider_payout_id,
            notes=request.notes,
        )
        await session.commit()
        await session.refresh(payout)
        return PayoutRecordResponse.model_validate(payout)
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/dev/simulate-payment/{order_id}")
async def dev_simulate_payment(
    order_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> dict:
    """
    Development-only endpoint to simulate payment completion for local testing.
    Allowed only when env == 'dev' and the caller owns the payment request (or is admin).
    """
    settings = get_settings()
    if settings.env != "dev":
        raise HTTPException(status_code=403, detail="Simulate payment is only available in dev environment")

    stmt = select(PaymentRequest).where(PaymentRequest.provider_order_id == order_id)
    result = await session.execute(stmt)
    pr = result.scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail="Payment request not found")

    adv = await session.get(Advertiser, pr.advertiser_id)
    if not adv:
        raise HTTPException(status_code=404, detail="Advertiser not found")

    if user.role != "admin" and adv.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to simulate payment for this advertiser")

    # Check if already processed
    tx_stmt = select(Transaction).where(Transaction.reference_id == order_id)
    tx_result = await session.execute(tx_stmt)
    if tx_result.scalar_one_or_none():
        return {"status": "already_processed", "message": "Payment has already been credited"}

    success = await BillingService.record_deposit(
        session=session,
        advertiser_id=pr.advertiser_id,
        amount=pr.amount,
        reference_id=order_id,
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to credit wallet")

    pr.status = "paid"
    await session.commit()
    return {"status": "success", "message": f"Dev payment confirmed. Credited ₹{pr.amount} to advertiser {pr.advertiser_id}."}


# ── Phase 3: Dispute & Appeal Workflow ────────────────────────────────────────

@router.post("/disputes", response_model=DisputeResponse, status_code=201)
async def create_dispute(
    payload: DisputeCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> BillingDispute:
    """Submit a billing dispute or IVT traffic appeal."""
    adv_id = payload.advertiser_id
    pub_id = payload.publisher_id

    # Tenant scoping and validation
    if user.role == "advertiser":
        adv_id = user.advertiser_id
    elif user.role == "publisher":
        pub_id = user.publisher_id

    if adv_id:
        adv = await session.get(Advertiser, adv_id)
        if not adv:
            raise HTTPException(status_code=404, detail="Advertiser not found")
        if user.role != "admin" and adv.owner_user_id != user.id:
            raise HTTPException(status_code=403, detail="Not authorized to dispute on behalf of this advertiser")

    if pub_id:
        pub = await session.get(Publisher, pub_id)
        if not pub:
            raise HTTPException(status_code=404, detail="Publisher not found")
        if user.role != "admin" and pub.owner_user_id != user.id:
            raise HTTPException(status_code=403, detail="Not authorized to appeal on behalf of this publisher")

    dispute = BillingDispute(
        dispute_type=payload.dispute_type,
        user_id=user.id,
        advertiser_id=adv_id,
        publisher_id=pub_id,
        campaign_id=payload.campaign_id,
        slot_id=payload.slot_id,
        transaction_id=payload.transaction_id,
        amount_disputed=payload.amount_disputed,
        amount_credited=Decimal("0"),
        currency=payload.currency.upper(),
        status="OPEN",
        reason=payload.reason,
        evidence=payload.evidence,
    )
    session.add(dispute)
    await session.commit()
    await session.refresh(dispute)
    return dispute


@router.get("/disputes", response_model=list[DisputeResponse])
async def list_disputes(
    status_filter: str | None = Query(default=None, alias="status"),
    advertiser_id: int | None = Query(default=None),
    publisher_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> list[BillingDispute]:
    """List billing disputes and traffic appeals, strictly scoped to role and ownership."""
    query = select(BillingDispute).order_by(BillingDispute.created_at.desc())

    if user.role == "advertiser":
        query = query.where(BillingDispute.advertiser_id == user.advertiser_id)
    elif user.role == "publisher":
        query = query.where(BillingDispute.publisher_id == user.publisher_id)
    elif user.role == "admin":
        if advertiser_id:
            query = query.where(BillingDispute.advertiser_id == advertiser_id)
        if publisher_id:
            query = query.where(BillingDispute.publisher_id == publisher_id)
    else:
        query = query.where(BillingDispute.user_id == user.id)

    if status_filter:
        query = query.where(BillingDispute.status == status_filter.upper())

    result = await session.execute(query)
    return list(result.scalars().all())


@router.get("/disputes/{dispute_id}", response_model=DisputeResponse)
async def get_dispute(
    dispute_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> BillingDispute:
    """Retrieve details for a single dispute."""
    dispute = await session.get(BillingDispute, dispute_id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    if user.role != "admin":
        if user.role == "advertiser" and dispute.advertiser_id != user.advertiser_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this dispute")
        if user.role == "publisher" and dispute.publisher_id != user.publisher_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this dispute")
        if user.role not in ("advertiser", "publisher") and dispute.user_id != user.id:
            raise HTTPException(status_code=403, detail="Not authorized to access this dispute")

    return dispute


@router.post("/disputes/{dispute_id}/resolve", response_model=DisputeResponse, dependencies=[Depends(require_roles("admin"))])
async def resolve_dispute(
    dispute_id: int,
    payload: DisputeResolveRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> BillingDispute:
    """Admin-only endpoint to resolve a dispute with optional automated refund/credit."""
    try:
        dispute = await BillingService.process_dispute_resolution(
            session=session,
            dispute_id=dispute_id,
            status=payload.status,
            amount_credited=payload.amount_credited,
            notes=payload.resolution_notes,
            admin_user_id=user.id,
        )
        await session.commit()
        await session.refresh(dispute)
        return dispute
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


# ── Phase 3: Financial & Measurement Reconciliation ──────────────────────────

@router.post("/reconcile", response_model=ReconciliationResponse, dependencies=[Depends(require_roles("admin"))])
async def run_reconciliation(
    payload: ReconciliationRequest = ReconciliationRequest(),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(verified_user),
) -> ReconciliationResponse:
    """
    Run authoritative financial and delivery telemetry audit (Phase 3 Task D).
    Compares ad_events spend, ledger transactions, advertiser balances, and publisher earnings.
    """
    service = ReconciliationService(session)
    report = await service.reconcile(
        start_date=payload.start_date,
        end_date=payload.end_date,
        advertiser_id=payload.advertiser_id,
        publisher_id=payload.publisher_id,
    )
    return ReconciliationResponse(
        status=report.status,
        start_date=report.start_date,
        end_date=report.end_date,
        total_event_spend=report.total_event_spend,
        total_ledger_spend=report.total_ledger_spend,
        spend_delta=report.spend_delta,
        total_ivt_events=report.total_ivt_events,
        ivt_suppressed_spend=report.ivt_suppressed_spend,
        discrepancies=report.discrepancies,
        alerts=report.alerts,
    )


