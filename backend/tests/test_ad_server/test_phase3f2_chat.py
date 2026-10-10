import pytest
from httpx import AsyncClient
from decimal import Decimal
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.auth import User
from liteads.ad_server.services.conversation_state import ConversationStateService
from liteads.schemas.chat import ChatSession
from liteads.schemas.intent import IntentResult, SlotData
from liteads.ad_server.services.tools import CreateDraftCampaignResult, ToolValidationError
from liteads.schemas.finance import PaymentOrderResponse
from liteads.common.cache import redis_client

pytestmark = pytest.mark.asyncio

@pytest.fixture(autouse=True)
async def clear_redis():
    await redis_client.connect()
    if redis_client.connected:
        await redis_client.client.flushdb()
    yield
    if redis_client.connected:
        await redis_client.client.flushdb()
        await redis_client.close()

@pytest.fixture
def advertiser_user():
    return User(id=1, email="adv1@test.com", role="advertiser", advertiser_id=100, is_active=True)

@pytest.fixture
def non_advertiser_user():
    return User(id=2, email="pub@test.com", role="publisher", publisher_id=200, is_active=True)

@pytest.fixture
def auth_client(client: AsyncClient, advertiser_user: User):
    from liteads.common.security import current_user
    from liteads.ad_server.main import app
    app.dependency_overrides[current_user] = lambda: advertiser_user
    yield client
    app.dependency_overrides.pop(current_user, None)

@pytest.fixture
def auth_client_non_adv(client: AsyncClient, non_advertiser_user: User):
    from liteads.common.security import current_user
    from liteads.ad_server.main import app
    app.dependency_overrides[current_user] = lambda: non_advertiser_user
    yield client
    app.dependency_overrides.pop(current_user, None)

@pytest.fixture
async def chat_session(advertiser_user: User):
    session = await ConversationStateService.create_session(user_id=advertiser_user.id, advertiser_id=advertiser_user.advertiser_id)
    yield session
    await ConversationStateService.delete_session(session.session_id)

async def test_create_chat_session_success(auth_client: AsyncClient, advertiser_user: User):
    response = await auth_client.post("/api/v1/chat/session")
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    
    session_id = data["session_id"]
    
    # Verify persisted in Redis correctly
    persisted_session = await ConversationStateService.get_session(session_id, user_id=advertiser_user.id)
    assert persisted_session is not None
    assert persisted_session.user_id == advertiser_user.id
    assert persisted_session.advertiser_id == advertiser_user.advertiser_id
    assert persisted_session.current_campaign_id is None
    assert persisted_session.pending_action is None
    assert len(persisted_session.history) == 0

async def test_create_chat_session_unauthenticated(client: AsyncClient):
    response = await client.post("/api/v1/chat/session")
    assert response.status_code == 401

async def test_create_chat_session_non_advertiser(auth_client_non_adv: AsyncClient):
    response = await auth_client_non_adv.post("/api/v1/chat/session")
    assert response.status_code == 403

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.create_draft_campaign", new_callable=AsyncMock)
async def test_create_campaign_payload(mock_create, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="Nike Sale"),
        reply="I'll create the Nike Sale campaign for you."
    ))
    
    mock_create.return_value = CreateDraftCampaignResult(campaign_id=999, name="Nike Sale", status="DRAFT", message="Created")

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Create Nike campaign"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "CREATE_CAMPAIGN"
    assert data["tool_status"] == "Success: Campaign Created"
    assert data["payload"] == {"campaign_id": 999}

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.request_payment", new_callable=AsyncMock)
async def test_request_payment_payload(mock_req_payment, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="REQUEST_PAYMENT",
        slots=SlotData(amount=Decimal("1000")),
        reply="Payment created"
    ))
    mock_req_payment.return_value = PaymentOrderResponse(order_id="order_xyz", amount=Decimal("1000"), currency="INR")

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Add 1000"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["tool_status"] == "Success: Payment Order Created"
    assert data["payload"] == {
        "order_id": "order_xyz",
        "amount": "1000",
        "currency": "INR",
        "qr_image_url": None
    }

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.create_draft_campaign", new_callable=AsyncMock)
async def test_failed_tool_returns_no_payload(mock_create, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="Nike Sale"),
        reply="Creating campaign."
    ))
    mock_create.side_effect = ToolValidationError("Budget cannot be negative")

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Create campaign"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["tool_status"] == "Failed: Validation Error"
    assert data["payload"] is None

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_campaign", new_callable=AsyncMock)
async def test_set_budget_has_no_payload(mock_update, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    chat_session.current_campaign_id = 999
    await ConversationStateService.save_session(chat_session)

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_BUDGET",
        slots=SlotData(budget_daily=Decimal("100")),
        reply="Updated budget."
    ))
    
    # We do not assert mock_update output payload since chat.py doesn't set it for SET_BUDGET
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Set budget to 100"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["tool_status"] == "Success: Budget Updated"
    assert data["payload"] is None

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_cross_user_session_access_remains_rejected(mock_client_cls, auth_client: AsyncClient, advertiser_user: User):
    # Create a session owned by another user (user_id=999)
    other_session = await ConversationStateService.create_session(user_id=999, advertiser_id=999)
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": other_session.session_id,
        "message": "Hello"
    })
    
    assert response.status_code == 404
    assert mock_client_cls.call_count == 0
