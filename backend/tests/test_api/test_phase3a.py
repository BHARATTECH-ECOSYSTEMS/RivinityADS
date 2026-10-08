import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from liteads.models.base import Status
from liteads.models.ad import Campaign, Advertiser
from liteads.models.finance import Transaction
from liteads.models.base import TransactionType
from decimal import Decimal

def test_status_enum_values():
    # TEST 1: The Campaign Status enum contains DRAFT with value 5.
    assert hasattr(Status, 'DRAFT')
    assert Status.DRAFT.value == 5
    
    # TEST 2: Existing statuses retain their existing numeric values.
    assert Status.INACTIVE.value == 0
    assert Status.ACTIVE.value == 1
    assert Status.PAUSED.value == 2
    assert Status.DELETED.value == 3
    assert Status.PENDING.value == 4

@pytest.mark.asyncio
async def test_campaign_draft_status(test_db):
    # Setup advertiser
    adv = Advertiser(name="Test Adv", balance=Decimal("100.0"))
    test_db.add(adv)
    await test_db.commit()

    # TEST 3: A Campaign can represent the DRAFT status correctly.
    camp = Campaign(
        advertiser_id=adv.id,
        name="Draft Campaign",
        status=Status.DRAFT.value
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)
    
    assert camp.status == 5

@pytest.mark.asyncio
async def test_transaction_unique_reference_id(test_db):
    # Setup advertiser
    adv = Advertiser(name="Tx Adv", balance=Decimal("100.0"))
    test_db.add(adv)
    await test_db.commit()

    # TEST 4: A Transaction with a unique reference_id can be inserted successfully.
    tx1 = Transaction(
        type=TransactionType.DEPOSIT.value,
        amount=Decimal("50.0"),
        advertiser_id=adv.id,
        reference_id="unique_ref_123",
        description="First deposit"
    )
    test_db.add(tx1)
    await test_db.commit()
    
    # TEST 5: Attempting to insert a second Transaction with the same non-null reference_id violates the database uniqueness constraint.
    tx2 = Transaction(
        type=TransactionType.DEPOSIT.value,
        amount=Decimal("50.0"),
        advertiser_id=adv.id,
        reference_id="unique_ref_123",
        description="Duplicate deposit"
    )
    test_db.add(tx2)
    with pytest.raises(IntegrityError):
        await test_db.commit()
        
    await test_db.rollback()

@pytest.mark.asyncio
async def test_transaction_nullable_reference_id(test_db):
    # Setup advertiser
    adv = Advertiser(name="Null Ref Adv", balance=Decimal("100.0"))
    test_db.add(adv)
    await test_db.commit()

    # TEST 6: Existing nullable reference_id behavior remains correct if reference_id is currently nullable.
    # We should be able to insert multiple transactions with null reference_id.
    tx1 = Transaction(
        type=TransactionType.SPEND.value,
        amount=Decimal("-5.0"),
        advertiser_id=adv.id,
        reference_id=None,
        description="First spend"
    )
    tx2 = Transaction(
        type=TransactionType.SPEND.value,
        amount=Decimal("-10.0"),
        advertiser_id=adv.id,
        reference_id=None,
        description="Second spend"
    )
    test_db.add_all([tx1, tx2])
    await test_db.commit()
    
    # Verify both were inserted successfully
    result = await test_db.execute(select(Transaction).where(Transaction.advertiser_id == adv.id))
    txs = result.scalars().all()
    assert len(txs) == 2
    assert txs[0].reference_id is None
    assert txs[1].reference_id is None
