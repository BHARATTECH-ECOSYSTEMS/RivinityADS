import pytest
import json
from unittest.mock import patch, AsyncMock
import httpx
from decimal import Decimal

from liteads.schemas.chat import ChatSession, ChatMessage
from liteads.schemas.intent import IntentResult
from liteads.ad_server.services.generative_intent_client import (
    GenerativeIntentClient,
    GenerativeUnavailableError,
    GenerativeMalformedResponseError
)

@pytest.fixture
def mock_session():
    session = ChatSession(user_id=1, advertiser_id=10, current_campaign_id=5)
    session.history.append(ChatMessage(role="user", content="Hello"))
    return session

@pytest.fixture
def mock_client():
    client = GenerativeIntentClient()
    client.api_key = "test_fake_key"
    return client

def mock_httpx_response(status_code: int, json_data: dict = None, text: str = ""):
    if json_data is not None:
        return httpx.Response(status_code, json=json_data, request=httpx.Request("POST", "http://test"))
    return httpx.Response(status_code, text=text, request=httpx.Request("POST", "http://test"))

def build_openai_response(content_dict: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(content_dict)
                }
            }
        ]
    }

@pytest.mark.asyncio
async def test_create_campaign_extraction(mock_client, mock_session):
    # 1. CREATE_CAMPAIGN
    content = {
        "intent": "CREATE_CAMPAIGN",
        "slots": {"name": "Nike", "budget_daily": 5000, "state": "Karnataka"},
        "reply": "I can create that campaign for you.",
        "confidence": 0.95
    }
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("I want to create a Nike campaign with a budget of 5000 per day.", mock_session)
        assert res.intent == "CREATE_CAMPAIGN"
        assert res.slots.name == "Nike"
        assert res.slots.budget_daily == Decimal("5000")
        assert res.slots.state == "Karnataka"
        assert res.reply == "I can create that campaign for you."

@pytest.mark.asyncio
async def test_set_budget(mock_client, mock_session):
    # 2. SET_BUDGET
    content = {"intent": "SET_BUDGET", "slots": {"budget_daily": 2000}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("Set the daily budget to 2000", mock_session)
        assert res.intent == "SET_BUDGET"
        assert res.slots.budget_daily == Decimal("2000")

@pytest.mark.asyncio
async def test_set_geo(mock_client, mock_session):
    # 3. SET_GEO
    content = {"intent": "SET_GEO", "slots": {"city": "Bengaluru", "is_exclude": False}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("Target Bengaluru", mock_session)
        assert res.intent == "SET_GEO"
        assert res.slots.city == "Bengaluru"

@pytest.mark.asyncio
async def test_set_sector(mock_client, mock_session):
    # 4. SET_SECTOR
    content = {"intent": "SET_SECTOR", "slots": {"sector": "technology", "is_exclude": False}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("Target technology sector", mock_session)
        assert res.intent == "SET_SECTOR"
        assert res.slots.sector == "technology"

@pytest.mark.asyncio
async def test_request_payment(mock_client, mock_session):
    # 5. REQUEST_PAYMENT
    content = {"intent": "REQUEST_PAYMENT", "slots": {"amount": 5000}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("Add 5000 rupees", mock_session)
        assert res.intent == "REQUEST_PAYMENT"
        assert res.slots.amount == Decimal("5000")

@pytest.mark.asyncio
async def test_launch(mock_client, mock_session):
    # 6. LAUNCH
    content = {"intent": "LAUNCH_CAMPAIGN", "slots": {}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("Launch the campaign", mock_session)
        assert res.intent == "LAUNCH_CAMPAIGN"

@pytest.mark.asyncio
async def test_unknown(mock_client, mock_session):
    # 7. UNKNOWN
    content = {"intent": "UNKNOWN", "slots": {}, "reply": "I'm not sure what you mean.", "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("What is the weather?", mock_session)
        assert res.intent == "UNKNOWN"

@pytest.mark.asyncio
async def test_partial_information(mock_client, mock_session):
    # 8. PARTIAL INFORMATION
    content = {"intent": "CREATE_CAMPAIGN", "slots": {"name": "Nike"}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("Create a campaign for Nike.", mock_session)
        assert res.intent == "CREATE_CAMPAIGN"
        assert res.slots.name == "Nike"
        assert res.slots.budget_daily is None

@pytest.mark.asyncio
async def test_malformed_response(mock_client, mock_session):
    # 9. MALFORMED RESPONSE
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, text="Not JSON")
        with pytest.raises(GenerativeMalformedResponseError):
            await mock_client.extract_intent("Break json", mock_session)

@pytest.mark.asyncio
async def test_invalid_intent(mock_client, mock_session):
    # 10. INVALID INTENT
    content = {"intent": "MAKE_ME_COFFEE", "slots": {}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        with pytest.raises(GenerativeMalformedResponseError):
            await mock_client.extract_intent("Make coffee", mock_session)

@pytest.mark.asyncio
async def test_invalid_slot_type(mock_client, mock_session):
    # 11. INVALID SLOT TYPE
    content = {"intent": "CREATE_CAMPAIGN", "slots": {"budget_daily": "not-a-number"}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        with pytest.raises(GenerativeMalformedResponseError):
            await mock_client.extract_intent("Create", mock_session)

@pytest.mark.asyncio
async def test_timeout(mock_client, mock_session):
    # 12. TIMEOUT
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Timeout")
        with pytest.raises(GenerativeUnavailableError):
            await mock_client.extract_intent("Timeout please", mock_session)

@pytest.mark.asyncio
async def test_network_failure(mock_client, mock_session):
    # 13. NETWORK FAILURE
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.RequestError("Network dropped")
        with pytest.raises(GenerativeUnavailableError):
            await mock_client.extract_intent("Hello", mock_session)

@pytest.mark.asyncio
async def test_provider_http_error(mock_client, mock_session):
    # 14. PROVIDER HTTP ERROR
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        response = mock_httpx_response(500, text="Internal Server Error")
        mock_post.side_effect = httpx.HTTPStatusError("500 Server Error", request=httpx.Request("POST", ""), response=response)
        with pytest.raises(GenerativeUnavailableError):
            await mock_client.extract_intent("Hello", mock_session)

@pytest.mark.asyncio
async def test_no_sensitive_backend_info_sent(mock_client, mock_session):
    # 15. Verify sensitive backend information is NOT included in the outgoing model request.
    content = {"intent": "UNKNOWN", "slots": {}, "confidence": 0.9}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        await mock_client.extract_intent("Hello", mock_session)
        
        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]
        
        # Check payload doesn't contain JWT, advertiser_id. 
        # Skip 'wallet' check as a strict string because 'wallet' is literally in the system prompt rules.
        payload_str = json.dumps(payload)
        assert "advertiser_id" not in payload_str
        assert "user_id" not in payload_str
        assert "jwt" not in payload_str
        
        # Ensure session history doesn't leak session attributes into the history arrays
        for msg in payload["messages"]:
            if msg["role"] == "user":
                assert "advertiser_id" not in msg["content"]

@pytest.mark.asyncio
async def test_advertiser_id_cannot_be_supplied(mock_client, mock_session):
    # 16. Verify advertiser_id cannot be supplied through model output.
    content = {
        "intent": "CREATE_CAMPAIGN", 
        "slots": {"name": "Test", "advertiser_id": 999}, # Malicious injection
        "confidence": 0.9
    }
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_httpx_response(200, build_openai_response(content))
        res = await mock_client.extract_intent("Hello", mock_session)
        
        # Pydantic ignores or rejects fields not in the model. Since IntentResult/SlotData
        # don't have advertiser_id, it should not be accessible.
        assert not hasattr(res.slots, "advertiser_id")

@pytest.mark.asyncio
async def test_provider_failure_raises_exception(mock_client, mock_session):
    # 17. Verify provider failure raises an exception rather than returning UNKNOWN.
    # (Already tested by test_timeout and test_network_failure, but let's re-verify)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Timeout")
        try:
            await mock_client.extract_intent("Hello", mock_session)
            pytest.fail("Should have raised GenerativeUnavailableError")
        except Exception as e:
            assert isinstance(e, GenerativeUnavailableError)
