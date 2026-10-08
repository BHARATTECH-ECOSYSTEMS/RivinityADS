import pytest
from httpx import AsyncClient
from decimal import Decimal
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.ad import Campaign, Advertiser
from liteads.models.base import Status
from liteads.models.auth import User
from liteads.ad_server.services.conversation_state import ConversationStateService
from liteads.schemas.chat import ChatSession
from liteads.schemas.intent import IntentResult, SlotData
from liteads.ad_server.services.generative_intent_client import GenerativeUnavailableError, GenerativeMalformedResponseError
from liteads.ad_server.services.tools import ToolValidationError, ToolAuthorizationError, ToolError, CreateDraftCampaignResult, UpdateCampaignResult, UpdateTargetingResult, ActivateCampaignResult
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
    return User(id=1, email="adv1@test.com", role="advertiser", advertiser_id=1, is_active=True)

@pytest.fixture
def advertiser_user2():
    return User(id=2, email="adv2@test.com", role="advertiser", advertiser_id=2, is_active=True)

@pytest.fixture
def auth_client(client: AsyncClient, advertiser_user: User):
    from liteads.common.security import current_user
    from liteads.ad_server.main import app
    app.dependency_overrides[current_user] = lambda: advertiser_user
    yield client
    app.dependency_overrides.pop(current_user, None)

@pytest.fixture
async def chat_session(test_db: AsyncSession, advertiser_user: User):
    session = await ConversationStateService.create_session(user_id=advertiser_user.id, advertiser_id=advertiser_user.advertiser_id)
    yield session
    await ConversationStateService.delete_session(session.session_id)

@pytest.fixture
async def another_user_chat_session(test_db: AsyncSession, advertiser_user2: User):
    session = await ConversationStateService.create_session(user_id=advertiser_user2.id, advertiser_id=advertiser_user2.advertiser_id)
    yield session
    await ConversationStateService.delete_session(session.session_id)

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.create_draft_campaign", new_callable=AsyncMock)
async def test_chat_create_campaign_success(mock_create, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="Nike Sale", budget_daily=Decimal("500")),
        confidence=0.9,
        reply="I'll create the Nike Sale campaign for you."
    ))
    
    mock_create.return_value = CreateDraftCampaignResult(campaign_id=999, name="Nike Sale", status="DRAFT", message="Created")

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Create Nike campaign with 500 budget"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "CREATE_CAMPAIGN"
    assert data["tool_status"] == "Success: Campaign Created"
    assert data["assistant_message"] == "I'll create the Nike Sale campaign for you."

    # Verify session current_campaign_id updated
    updated_session = await ConversationStateService.get_session(chat_session.session_id, chat_session.user_id)
    assert updated_session.current_campaign_id == 999
    # Verify message appended
    assert len(updated_session.history) == 2
    assert updated_session.history[0].role == "user"
    assert updated_session.history[0].content == "Create Nike campaign with 500 budget"
    assert updated_session.history[1].role == "assistant"
    assert updated_session.history[1].content == "I'll create the Nike Sale campaign for you."

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.create_draft_campaign", new_callable=AsyncMock)
async def test_chat_create_campaign_tool_failure(mock_create, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="Nike Sale"),
        confidence=0.9,
        reply="Creating campaign."
    ))
    
    mock_create.side_effect = ToolValidationError("Budget cannot be negative")

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Create campaign with -500 budget"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["tool_status"] == "Failed: Validation Error"
    assert data["assistant_message"] == "Budget cannot be negative"

    # Verify session current_campaign_id NOT updated
    updated_session = await ConversationStateService.get_session(chat_session.session_id, chat_session.user_id)
    assert updated_session.current_campaign_id is None
    # Failed tool does not falsely report success
    assert "Success" not in data["tool_status"]

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_cross_user_session_rejected(mock_client_cls, auth_client: AsyncClient, another_user_chat_session: ChatSession):
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": another_user_chat_session.session_id,
        "message": "Hello"
    })
    
    assert response.status_code == 404
    assert mock_client_cls.call_count == 0

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_llm_unavailable_handled(mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(side_effect=GenerativeUnavailableError("Timeout"))

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Hello"
    })
    assert response.status_code == 200
    assert "LLM Unavailable" in response.json()["tool_status"]
    assert "processing core" in response.json()["assistant_message"]

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_llm_malformed_handled(mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(side_effect=GenerativeMalformedResponseError("Invalid JSON"))

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Hello"
    })
    assert response.status_code == 200
    assert "LLM Malformed" in response.json()["tool_status"]
    assert "understanding your request" in response.json()["assistant_message"]

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_set_budget_without_campaign(mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_BUDGET",
        slots=SlotData(budget_daily=Decimal("100")),
        reply="Updating budget."
    ))

    # Note: current_campaign_id is None
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Set budget to 100"
    })
    
    assert response.status_code == 200
    assert response.json()["tool_status"] == "Failed: No Campaign Selected"

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_campaign", new_callable=AsyncMock)
async def test_set_budget_with_campaign(mock_update, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    chat_session.current_campaign_id = 999
    await ConversationStateService.save_session(chat_session)

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_BUDGET",
        slots=SlotData(budget_daily=Decimal("100")),
        reply="Updated budget."
    ))
    mock_update.return_value = UpdateCampaignResult(campaign_id=999, name="T", status="DRAFT", updated_fields=["budget_daily"])

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Set budget to 100"
    })
    
    assert response.status_code == 200
    assert response.json()["tool_status"] == "Success: Budget Updated"
    mock_update.assert_called_once()
    assert mock_update.call_args[1]["campaign_id"] == 999
    assert mock_update.call_args[1]["budget_daily"] == Decimal("100")

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_set_geo_merges_targeting(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    chat_session.current_campaign_id = 999
    await ConversationStateService.save_session(chat_session)

    # Simulate existing DB rule
    from liteads.models.ad import TargetingRule
    existing_rule = TargetingRule(campaign_id=999, rule_type="sector", is_include=True, rule_value={"sectors": ["tech"]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="IN", state="KA", city="Bengaluru"),
        reply="Targeting updated."
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target Bengaluru"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_called_once()
    ts = mock_update_targeting.call_args[1]["targeting"]
    # Check sector preserved
    assert ts.sector_include == ["tech"]
    # Check geo added
    assert len(ts.geo_include) == 1
    assert ts.geo_include[0].country == "IN"
    assert ts.geo_include[0].city == "Bengaluru"

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.request_payment", new_callable=AsyncMock)
async def test_request_payment(mock_req_payment, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
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
    assert response.json()["tool_status"] == "Success: Payment Order Created"

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.activate_campaign", new_callable=AsyncMock)
async def test_launch_campaign(mock_activate, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    chat_session.current_campaign_id = 999
    await ConversationStateService.save_session(chat_session)

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="LAUNCH_CAMPAIGN",
        slots=SlotData(),
        reply="Launching!"
    ))
    mock_activate.return_value = ActivateCampaignResult(campaign_id=999, status="ACTIVE")

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Launch it"
    })
    
    assert response.status_code == 200
    assert response.json()["tool_status"] == "Success: Campaign Activated"
    mock_activate.assert_called_once()
    assert mock_activate.call_args[1]["campaign_id"] == 999

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_unknown_intent(mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="UNKNOWN",
        slots=SlotData(),
        reply="I didn't understand."
    ))

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "What is the meaning of life?"
    })
    
    assert response.status_code == 200
    assert response.json()["intent"] == "UNKNOWN"
    assert response.json()["tool_status"] is None

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.create_draft_campaign", new_callable=AsyncMock)
async def test_tool_authorization_error_handled(mock_create, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="test"),
        reply="Creating"
    ))
    mock_create.side_effect = ToolAuthorizationError("Nope")

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Create test"
    })
    
    assert response.status_code == 200
    assert response.json()["tool_status"] == "Failed: Authorization Error"
    assert response.json()["assistant_message"] == "You don't have permission to perform this action."

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.create_draft_campaign", new_callable=AsyncMock)
async def test_tool_error_handled(mock_create, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="test"),
        reply="Creating"
    ))
    mock_create.side_effect = ToolError("DB down")

    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Create test"
    })
    
    assert response.status_code == 200
    assert response.json()["tool_status"] == "Failed: Internal Tool Error"
    assert response.json()["assistant_message"] == "An internal error occurred while processing your request."
