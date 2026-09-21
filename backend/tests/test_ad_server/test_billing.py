import pytest
from decimal import Decimal
from liteads.models import Advertiser, Campaign, Publisher, PlatformSettings
from liteads.models.finance import Transaction
from liteads.models.base import TransactionType
from liteads.ad_server.services.billing import BillingService

@pytest.fixture
async def billing_data(test_db):
    adv = Advertiser(id=1, name="Adv 1", balance=Decimal("100.0000"))
    camp = Campaign(id=1, advertiser_id=1, name="Camp 1", bid_amount=Decimal("1.5000"), spent_today=Decimal("0"), spent_total=Decimal("0"))
    pub = Publisher(id=1, name="Pub 1", balance=Decimal("10.0000"))
    settings = PlatformSettings(publisher_rev_share=Decimal("0.700"))
    
    test_db.add_all([adv, camp, pub, settings])
    await test_db.commit()


@pytest.mark.asyncio
async def test_process_impression_charge_success(test_db, billing_data):
    # Charge 1.50
    success = await BillingService.process_impression_charge(
        test_db, advertiser_id=1, campaign_id=1, publisher_id=1, cost=Decimal("1.5000")
    )
    await test_db.commit()
    
    assert success is True
    
    adv = await test_db.get(Advertiser, 1)
    camp = await test_db.get(Campaign, 1)
    pub = await test_db.get(Publisher, 1)
    
    # Advertiser balance: 100 - 1.5 = 98.5
    assert adv.balance == Decimal("98.5000")
    
    # Campaign spend: 1.5
    assert camp.spent_today == Decimal("1.5000")
    
    # Publisher balance: 10 + (1.5 * 0.7) = 11.05
    assert pub.balance == Decimal("11.0500")


@pytest.mark.asyncio
async def test_process_impression_charge_insufficient_funds(test_db, billing_data):
    # Charge 150.00 (balance is only 100)
    success = await BillingService.process_impression_charge(
        test_db, advertiser_id=1, campaign_id=1, publisher_id=1, cost=Decimal("150.0000")
    )
    await test_db.commit()
    
    assert success is False
    
    adv = await test_db.get(Advertiser, 1)
    assert adv.balance == Decimal("100.0000")  # unchanged


@pytest.mark.asyncio
async def test_record_deposit(test_db, billing_data):
    tx = await BillingService.record_deposit(test_db, advertiser_id=1, amount=Decimal("50.00"))
    await test_db.commit()
    
    assert tx.type == TransactionType.DEPOSIT
    assert tx.amount == Decimal("50.00")
    assert tx.advertiser_id == 1
    
    adv = await test_db.get(Advertiser, 1)
    assert adv.balance == Decimal("150.0000")


@pytest.mark.asyncio
async def test_record_payout_success(test_db, billing_data):
    tx = await BillingService.record_payout(test_db, publisher_id=1, amount=Decimal("5.00"))
    await test_db.commit()
    
    assert tx.type == TransactionType.PAYOUT
    assert tx.amount == Decimal("-5.00")
    assert tx.publisher_id == 1
    
    pub = await test_db.get(Publisher, 1)
    assert pub.balance == Decimal("5.0000")


@pytest.mark.asyncio
async def test_record_payout_insufficient(test_db, billing_data):
    with pytest.raises(ValueError, match="Insufficient balance"):
        await BillingService.record_payout(test_db, publisher_id=1, amount=Decimal("50.00"))
    await test_db.rollback()
