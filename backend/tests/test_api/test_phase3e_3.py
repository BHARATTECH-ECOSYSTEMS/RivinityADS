import pytest
from unittest.mock import patch, AsyncMock
import httpx
from decimal import Decimal

from liteads.schemas.chat import ChatSession, ChatMessage
from liteads.schemas.intent import IntentResult
from liteads.ad_server.services.laya_client import LayaUnavailableError, LayaMalformedResponseError
from liteads.ad_server.services.laya_intent_client import LayaIntentClient

@pytest.fixture
def mock_session():
    session = ChatSession(user_id=1, advertiser_id=10, current_campaign_id=5)
    session.history.append(ChatMessage(role="user", content="Hello"))
    return session

@pytest.fixture
def mock_client():
    return LayaIntentClient()

def mock_httpx_response(status_code: int, json_data: dict = None, text: str = ""):
    if json_data is not None:
        return httpx.Response(status_code, json=json_data, request=httpx.Request("POST", "http://test"))
    return httpx.Response(status_code, text=text, request=httpx.Request("POST", "http://test"))

@pytest.mark.asyncio
async def test_create_campaign_extraction(mock_client, mock_session):
    # 1. CREATE_CAMPAIGN extraction
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "CREATE_CAMPAIGN", "slots": {"name": "Nike"}, "confidence": 0.9}
        )
        res = await mock_client.extract_intent("Create a Nike campaign", mock_session)
        assert res.intent == "CREATE_CAMPAIGN"
        assert res.slots.name == "Nike"
        assert res.confidence == 0.9

@pytest.mark.asyncio
async def test_create_campaign_with_budget(mock_client, mock_session):
    # 2. CREATE_CAMPAIGN + budget
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "CREATE_CAMPAIGN", "slots": {"name": "Nike", "budget_daily": 5000}}
        )
        res = await mock_client.extract_intent("Create a Nike campaign with 5000 daily budget", mock_session)
        assert res.intent == "CREATE_CAMPAIGN"
        assert res.slots.name == "Nike"
        assert res.slots.budget_daily == Decimal("5000")

@pytest.mark.asyncio
async def test_set_budget(mock_client, mock_session):
    # 3. SET_BUDGET
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "SET_BUDGET", "slots": {"budget_daily": 2000}}
        )
        res = await mock_client.extract_intent("Set the daily budget to 2000", mock_session)
        assert res.intent == "SET_BUDGET"
        assert res.slots.budget_daily == Decimal("2000")

@pytest.mark.asyncio
async def test_set_geo(mock_client, mock_session):
    # 4. SET_GEO
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "SET_GEO", "slots": {"city": "Bengaluru", "is_exclude": False}}
        )
        res = await mock_client.extract_intent("Target Bengaluru", mock_session)
        assert res.intent == "SET_GEO"
        assert res.slots.city == "Bengaluru"

@pytest.mark.asyncio
async def test_set_sector(mock_client, mock_session):
    # 5. SET_SECTOR
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "SET_SECTOR", "slots": {"sector": "technology", "is_exclude": False}}
        )
        res = await mock_client.extract_intent("Target technology sector", mock_session)
        assert res.intent == "SET_SECTOR"
        assert res.slots.sector == "technology"

@pytest.mark.asyncio
async def test_request_payment(mock_client, mock_session):
    # 6. REQUEST_PAYMENT
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "REQUEST_PAYMENT", "slots": {"amount": 5000}}
        )
        res = await mock_client.extract_intent("Add 5000 rupees", mock_session)
        assert res.intent == "REQUEST_PAYMENT"
        assert res.slots.amount == Decimal("5000")

@pytest.mark.asyncio
async def test_launch(mock_client, mock_session):
    # 7. LAUNCH
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "LAUNCH_CAMPAIGN"}
        )
        res = await mock_client.extract_intent("Launch the campaign", mock_session)
        assert res.intent == "LAUNCH_CAMPAIGN"

@pytest.mark.asyncio
async def test_partial_information(mock_client, mock_session):
    # 8. PARTIAL INFORMATION
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "CREATE_CAMPAIGN", "slots": {}}
        )
        res = await mock_client.extract_intent("Create a campaign", mock_session)
        assert res.intent == "CREATE_CAMPAIGN"
        assert res.slots.name is None

@pytest.mark.asyncio
async def test_invalid_intent(mock_client, mock_session):
    # 9. INVALID INTENT
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(
            200, 
            {"intent": "MAKE_ME_COFFEE", "slots": {}}
        )
        with pytest.raises(LayaMalformedResponseError):
            await mock_client.extract_intent("Make me coffee", mock_session)

@pytest.mark.asyncio
async def test_malformed_response(mock_client, mock_session):
    # 10. MALFORMED RESPONSE
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, text="Not JSON")
        with pytest.raises(LayaMalformedResponseError):
            await mock_client.extract_intent("Break json", mock_session)

@pytest.mark.asyncio
async def test_laya_timeout(mock_client, mock_session):
    # 11. LAYA TIMEOUT
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Timeout")
        with pytest.raises(LayaUnavailableError):
            await mock_client.extract_intent("Timeout please", mock_session)

@pytest.mark.asyncio
async def test_endpoint_missing(mock_client, mock_session):
    # Verifying the architectural blocker safely errors out
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(404)
        with pytest.raises(LayaUnavailableError) as exc_info:
            await mock_client.extract_intent("test missing", mock_session)
        assert "not supported" in str(exc_info.value)
