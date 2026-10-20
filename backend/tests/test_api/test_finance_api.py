import hmac
import hashlib
import json
from unittest.mock import MagicMock, patch
import pytest
from httpx import AsyncClient
from decimal import Decimal
from sqlalchemy import select
from liteads.models import Advertiser, Publisher, User, PlatformSettings
from liteads.models.finance import Transaction, PaymentRequest
from liteads.models.base import TransactionType
from liteads.common.security import create_access_token

@pytest.fixture
async def finance_test_data(test_db):
    # Create users (admin and standard) and advertisers/publishers
    admin_user = User(id=1, email="admin@example.com", name="Admin", password_hash="hash", role="admin")
    adv_owner = User(id=2, email="adv@example.com", name="Adv", password_hash="hash", role="advertiser")
    pub_owner = User(id=3, email="pub@example.com", name="Pub", password_hash="hash", role="publisher")
    other_user = User(id=4, email="other@example.com", name="Other", password_hash="hash", role="advertiser")

    adv1 = Advertiser(id=1, name="Adv 1", balance=Decimal("100.0000"), owner_user_id=2)
    adv2 = Advertiser(id=2, name="Adv 2", balance=Decimal("50.0000"), owner_user_id=4)
    pub1 = Publisher(id=1, name="Pub 1", balance=Decimal("10.0000"), owner_user_id=3)
    pub2 = Publisher(id=2, name="Pub 2", balance=Decimal("20.0000"), owner_user_id=4)
    
    settings = PlatformSettings(id=1, publisher_rev_share=Decimal("0.700"))
    
    test_db.add_all([admin_user, adv_owner, pub_owner, other_user, adv1, adv2, pub1, pub2, settings])
    await test_db.flush()
    adv_owner.advertiser_id = 1
    other_user.advertiser_id = 2
    pub_owner.publisher_id = 1
    await test_db.commit()

def get_auth_headers(user_id: int, role: str, advertiser_id: int | None = None):
    user = User(id=user_id, role=role, is_active=True, email=f"test{user_id}@test.com", name="test", password_hash="test", advertiser_id=advertiser_id)
    token = create_access_token(user)
    return {"Authorization": f"Bearer {token}"}

WEBHOOK_SECRET = "test_webhook_secret_key"

def generate_webhook_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

@pytest.fixture
def mock_razorpay():
    with patch("liteads.ad_server.services.tools.get_razorpay_client") as mock_tools_rzp, \
         patch("liteads.ad_server.routers.finance.get_razorpay_client") as mock_finance_rzp:
        mock_client = MagicMock()
        import uuid
        def create_qr_mock(*args, **kwargs):
            return {
                "id": f"qr_order_{uuid.uuid4().hex[:8]}",
                "amount": int(kwargs.get("amount", 100) * 100),
                "currency": "INR",
                "receipt": kwargs.get("receipt", "adv_1_qr_test"),
                "status": "created",
                "image_url": "https://api.razorpay.com/v1/payments/qr_mock/image"
            }
        def verify_sig(body, signature):
            expected = generate_webhook_signature(WEBHOOK_SECRET, body if isinstance(body, bytes) else body.encode("utf-8"))
            return hmac.compare_digest(expected, signature)

        mock_client.create_qr_code.side_effect = create_qr_mock
        mock_client.verify_webhook_signature.side_effect = verify_sig

        from liteads.ad_server.main import app
        from liteads.ad_server.services.razorpay_client import get_razorpay_client
        app.dependency_overrides[get_razorpay_client] = lambda: mock_client
        mock_tools_rzp.return_value = mock_client
        mock_finance_rzp.return_value = mock_client

        yield mock_client
        app.dependency_overrides.pop(get_razorpay_client, None)

@pytest.mark.asyncio
async def test_unauthenticated_endpoints(client: AsyncClient):
    res = await client.get("/api/v1/finance/advertiser/1/ledger")
    assert res.status_code == 401
    
    res = await client.get("/api/v1/finance/publisher/1/ledger")
    assert res.status_code == 401
    
    res = await client.post("/api/v1/finance/advertiser/1/deposit", json={"amount": 10.0})
    assert res.status_code == 401
    
    res = await client.post("/api/v1/finance/publisher/1/payout", json={"amount": 10.0})
    assert res.status_code == 401

@pytest.mark.asyncio
async def test_advertiser_ledger_access(client: AsyncClient, finance_test_data):
    # Owner access
    res = await client.get(
        "/api/v1/finance/advertiser/1/ledger", 
        headers=get_auth_headers(2, "advertiser")
    )
    assert res.status_code == 200
    assert float(res.json()["balance"]) == 100.0
    
    # Admin access
    res = await client.get(
        "/api/v1/finance/advertiser/1/ledger", 
        headers=get_auth_headers(1, "admin")
    )
    assert res.status_code == 200
    
    # Other advertiser access
    res = await client.get(
        "/api/v1/finance/advertiser/1/ledger", 
        headers=get_auth_headers(4, "advertiser")
    )
    assert res.status_code == 403

@pytest.mark.asyncio
async def test_deposit_funds_authorization(client: AsyncClient, finance_test_data):
    # Admin can deposit
    res = await client.post(
        "/api/v1/finance/advertiser/1/deposit", 
        headers=get_auth_headers(1, "admin"),
        json={"amount": 50.0, "reference_id": "test_dep_1"}
    )
    assert res.status_code == 200
    assert float(res.json()["amount"]) == 50.0
    
    # Owner cannot deposit directly
    res = await client.post(
        "/api/v1/finance/advertiser/1/deposit", 
        headers=get_auth_headers(2, "advertiser"),
        json={"amount": 50.0, "reference_id": "test_dep_2"}
    )
    assert res.status_code == 403

@pytest.mark.asyncio
async def test_publisher_ledger_access(client: AsyncClient, finance_test_data):
    # Owner access
    res = await client.get(
        "/api/v1/finance/publisher/1/ledger", 
        headers=get_auth_headers(3, "publisher")
    )
    assert res.status_code == 200
    assert float(res.json()["balance"]) == 10.0
    
    # Admin access
    res = await client.get(
        "/api/v1/finance/publisher/1/ledger", 
        headers=get_auth_headers(1, "admin")
    )
    assert res.status_code == 200
    
    # Other publisher access
    res = await client.get(
        "/api/v1/finance/publisher/1/ledger", 
        headers=get_auth_headers(4, "publisher")
    )
    assert res.status_code == 403

@pytest.mark.asyncio
async def test_request_payout_authorization(client: AsyncClient, finance_test_data):
    # Owner can payout
    res = await client.post(
        "/api/v1/finance/publisher/1/payout", 
        headers=get_auth_headers(3, "publisher"),
        json={"amount": 5.0, "reference_id": "test_pay_1"}
    )
    assert res.status_code == 200
    
    # Admin can payout
    res = await client.post(
        "/api/v1/finance/publisher/1/payout", 
        headers=get_auth_headers(1, "admin"),
        json={"amount": 1.0, "reference_id": "test_pay_2"}
    )
    assert res.status_code == 200
    
    # Other publisher cannot payout
    res = await client.post(
        "/api/v1/finance/publisher/1/payout", 
        headers=get_auth_headers(4, "publisher"),
        json={"amount": 1.0, "reference_id": "test_pay_3"}
    )
    assert res.status_code == 403


# --- Advertiser Wallet Top-Up and Accounting Regression Tests ---

@pytest.mark.asyncio
async def test_authorized_advertiser_payment_request(client: AsyncClient, finance_test_data, mock_razorpay):
    # Requirement 1: An authorized advertiser can create a payment request
    res = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(2, "advertiser", advertiser_id=1),
        json={"amount": 250.0}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["order_id"].startswith("qr_order_")
    assert Decimal(str(data["amount"])) == Decimal("250.0")
    assert data["currency"] == "INR"
    assert "https://" in data["qr_image_url"]

    # Other non-owner advertiser is forbidden
    unauth_res = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(4, "advertiser", advertiser_id=2),
        json={"amount": 250.0}
    )
    assert unauth_res.status_code == 403

    # Platform admin is authorized for any advertiser
    admin_res = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(1, "admin"),
        json={"amount": 500.0}
    )
    assert admin_res.status_code == 200
    assert Decimal(str(admin_res.json()["amount"])) == Decimal("500.0")


@pytest.mark.asyncio
async def test_non_admin_cannot_use_direct_deposit(client: AsyncClient, finance_test_data):
    # Requirement 2: A non-admin advertiser cannot use administrator-only direct deposit
    adv_res = await client.post(
        "/api/v1/finance/advertiser/1/deposit",
        headers=get_auth_headers(2, "advertiser", advertiser_id=1),
        json={"amount": 100.0, "reference_id": "bypass_attempt"}
    )
    assert adv_res.status_code == 403
    assert "Deposits must happen through the payment flow" in adv_res.json()["detail"]


@pytest.mark.asyncio
async def test_creating_payment_request_does_not_credit_wallet(client: AsyncClient, test_db, finance_test_data, mock_razorpay):
    # Requirement 3: Creating a payment request does not credit the wallet
    adv = await test_db.get(Advertiser, 1)
    initial_balance = adv.balance

    res = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(2, "advertiser", advertiser_id=1),
        json={"amount": 1000.0}
    )
    assert res.status_code == 200

    await test_db.refresh(adv)
    # Balance must be completely unchanged
    assert adv.balance == initial_balance

    # No deposit transactions may be recorded on request creation
    tx_res = await test_db.execute(select(Transaction).where(Transaction.advertiser_id == 1))
    transactions = tx_res.scalars().all()
    assert len(transactions) == 0


@pytest.mark.asyncio
async def test_payment_request_minimum_amount_validation(client: AsyncClient, finance_test_data, mock_razorpay):
    # Minimum top-up is ₹100 INR
    res_under = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(2, "advertiser", advertiser_id=1),
        json={"amount": 99.0}
    )
    assert res_under.status_code == 422

    res_zero = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(2, "advertiser", advertiser_id=1),
        json={"amount": 0}
    )
    assert res_zero.status_code == 422

    res_neg = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(2, "advertiser", advertiser_id=1),
        json={"amount": -50.0}
    )
    assert res_neg.status_code == 422


@pytest.mark.asyncio
async def test_valid_webhook_credits_advertiser_exactly_once(client: AsyncClient, test_db, finance_test_data, mock_razorpay):
    # Requirement 4 & 5: Valid payment webhook credits correct advertiser exactly once; duplicate delivery idempotent
    # 1. Create a payment request first
    req_res = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(2, "advertiser", advertiser_id=1),
        json={"amount": 200.0}
    )
    assert req_res.status_code == 200
    order_id = req_res.json()["order_id"]

    adv = await test_db.get(Advertiser, 1)
    balance_before = adv.balance

    # 2. Construct valid qr_code.credited webhook
    payload = {
        "event": "qr_code.credited",
        "payload": {
            "qr_code": {
                "entity": {
                    "id": order_id
                }
            },
            "payment": {
                "entity": {
                    "amount": 20000, # 200.00 INR in paise
                    "currency": "INR",
                    "status": "captured",
                    "notes": {
                        "receipt": f"adv_1_qr_{order_id[-6:]}"
                    }
                }
            }
        }
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(WEBHOOK_SECRET, body)

    # 3. Deliver webhook
    resp = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=body,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}

    # 4. Verify wallet credited
    await test_db.refresh(adv)
    assert adv.balance == balance_before + Decimal("200.0000")

    # Verify PaymentRequest marked paid
    pr_res = await test_db.execute(select(PaymentRequest).where(PaymentRequest.provider_order_id == order_id))
    pr = pr_res.scalar_one()
    assert pr.status == "paid"

    # Requirement 5: Repeated delivery does NOT credit twice
    resp_dup = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=body,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"}
    )
    assert resp_dup.status_code == 200
    assert resp_dup.json() == {"status": "already_processed"}

    await test_db.refresh(adv)
    assert adv.balance == balance_before + Decimal("200.0000")


@pytest.mark.asyncio
async def test_webhook_invalid_signatures_rejected(client: AsyncClient, finance_test_data, mock_razorpay):
    # Requirement 6: Invalid webhook signatures are rejected
    payload = {"event": "qr_code.credited", "payload": {}}
    body = json.dumps(payload).encode("utf-8")

    # Missing signature
    resp_missing = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=body,
        headers={"content-type": "application/json"}
    )
    assert resp_missing.status_code == 400
    assert "Missing signature" in resp_missing.json()["detail"]

    # Invalid signature
    resp_bad = await client.post(
        "/api/v1/finance/webhooks/razorpay",
        content=body,
        headers={"x-razorpay-signature": "invalid_hex_signature", "content-type": "application/json"}
    )
    assert resp_bad.status_code == 400
    assert "Invalid signature" in resp_bad.json()["detail"]


@pytest.mark.asyncio
async def test_webhook_mismatches_cannot_credit(client: AsyncClient, test_db, finance_test_data, mock_razorpay):
    # Requirement 7: Amount, currency or advertiser/payment mismatches cannot produce incorrect credit
    req_res = await client.post(
        "/api/v1/finance/advertiser/1/payment-request",
        headers=get_auth_headers(2, "advertiser", advertiser_id=1),
        json={"amount": 300.0}
    )
    assert req_res.status_code == 200
    order_id = req_res.json()["order_id"]

    adv = await test_db.get(Advertiser, 1)
    balance_before = adv.balance

    def send_webhook(amount_paise, currency, receipt_str):
        payload = {
            "event": "qr_code.credited",
            "payload": {
                "qr_code": {"entity": {"id": order_id}},
                "payment": {
                    "entity": {
                        "amount": amount_paise,
                        "currency": currency,
                        "status": "captured",
                        "notes": {"receipt": receipt_str}
                    }
                }
            }
        }
        b = json.dumps(payload).encode("utf-8")
        s = generate_webhook_signature(WEBHOOK_SECRET, b)
        return client.post(
            "/api/v1/finance/webhooks/razorpay",
            content=b,
            headers={"x-razorpay-signature": s, "content-type": "application/json"}
        )

    # A: Currency mismatch (USD instead of INR)
    r1 = await send_webhook(30000, "USD", f"adv_1_qr_{order_id[-6:]}")
    assert r1.status_code == 200
    assert r1.json()["status"] == "ignored"
    await test_db.refresh(adv)
    assert adv.balance == balance_before

    # B: Amount mismatch (15000 paise vs 30000 paise expected)
    r2 = await send_webhook(15000, "INR", f"adv_1_qr_{order_id[-6:]}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "ignored"
    assert r2.json()["reason"] == "amount mismatch"
    await test_db.refresh(adv)
    assert adv.balance == balance_before

    # C: Advertiser mismatch (receipt says adv_2 instead of adv_1)
    r3 = await send_webhook(30000, "INR", f"adv_2_qr_{order_id[-6:]}")
    assert r3.status_code == 200
    assert r3.json()["status"] == "ignored"
    assert r3.json()["reason"] == "advertiser mismatch"
    await test_db.refresh(adv)
    assert adv.balance == balance_before
