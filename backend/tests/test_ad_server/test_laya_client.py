import os
import pytest
import httpx
from unittest.mock import patch, MagicMock

from liteads.common.config import get_settings
from liteads.ad_server.services.laya_client import (
    LayaDecisionClient,
    LayaDecisionResult,
    LayaUnavailableError,
    LayaMalformedResponseError,
)

pytestmark = pytest.mark.asyncio

# Mock responses
SUCCESS_RESPONSE = {
    "answers": {
        "opt_decision": {
            "type": "choice",
            "choice": "increase_budget_20",
            "confidence": 0.85
        }
    }
}

NO_CHANGE_RESPONSE = {
    "answers": {
        "opt_decision": {
            "type": "choice",
            "choice": "no_change",
            "confidence": 0.99
        }
    }
}

MALFORMED_NO_ANSWERS = {
    "other_stuff": "here"
}

MALFORMED_NO_CHOICE = {
    "answers": {
        "opt_decision": {
            "confidence": 0.85
        }
    }
}

MALFORMED_NO_CONFIDENCE = {
    "answers": {
        "opt_decision": {
            "choice": "no_change"
        }
    }
}

MALFORMED_INVALID_CONFIDENCE = {
    "answers": {
        "opt_decision": {
            "choice": "no_change",
            "confidence": "not_a_float"
        }
    }
}

MALFORMED_INVALID_CHOICE = {
    "answers": {
        "opt_decision": {
            "choice": "some_random_action",
            "confidence": 0.85
        }
    }
}


class MockAsyncClient:
    def __init__(self, json_data=None, status_code=200, exception=None, text=""):
        self.json_data = json_data
        self.status_code = status_code
        self.exception = exception
        self.text = text
        self.post_calls = []

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self.exception:
            raise self.exception
        
        mock_response = MagicMock()
        mock_response.status_code = self.status_code
        mock_response.text = self.text
        if self.json_data is not None:
            mock_response.json.return_value = self.json_data
        else:
            mock_response.json.side_effect = ValueError("Invalid JSON")
        return mock_response

    async def aclose(self):
        pass


async def test_successful_laya_response():
    """1. Successful Laya response: choice and confidence extracted correctly."""
    mock_http = MockAsyncClient(json_data=SUCCESS_RESPONSE)
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    result = await client.decide("Test state")
    
    assert isinstance(result, LayaDecisionResult)
    assert result.choice == "increase_budget_20"
    assert result.confidence == 0.85
    assert result.raw_response == SUCCESS_RESPONSE


async def test_laya_returns_no_change():
    """2. Laya returns no_change."""
    mock_http = MockAsyncClient(json_data=NO_CHANGE_RESPONSE)
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    result = await client.decide("Test state")
    
    assert result.choice == "no_change"
    assert result.confidence == 0.99


async def test_laya_unavailable():
    """3. Laya unavailable: handled as error."""
    mock_http = MockAsyncClient(exception=httpx.ConnectError("Connection refused"))
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    with pytest.raises(LayaUnavailableError) as exc_info:
        await client.decide("Test state")
    
    assert "request failed" in str(exc_info.value)

    # Test timeout
    mock_http_timeout = MockAsyncClient(exception=httpx.TimeoutException("Timeout"))
    client_timeout = LayaDecisionClient(client=mock_http_timeout)  # type: ignore
    with pytest.raises(LayaUnavailableError) as exc_info:
        await client_timeout.decide("Test state")
    
    assert "timed out" in str(exc_info.value)


async def test_laya_returns_non_2xx():
    """4. Laya returns non-2xx."""
    mock_http = MockAsyncClient(status_code=500, text="Internal Server Error")
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    with pytest.raises(LayaUnavailableError) as exc_info:
        await client.decide("Test state")
    
    assert "status 500" in str(exc_info.value)


async def test_malformed_response_json():
    """5. Malformed response (invalid JSON)."""
    mock_http = MockAsyncClient(status_code=200, text="Not JSON")
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    with pytest.raises(LayaMalformedResponseError) as exc_info:
        await client.decide("Test state")
    
    assert "Invalid JSON" in str(exc_info.value)


async def test_malformed_response_missing_answers():
    """5. Malformed response (missing answers dict)."""
    mock_http = MockAsyncClient(json_data=MALFORMED_NO_ANSWERS)
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    with pytest.raises(LayaMalformedResponseError) as exc_info:
        await client.decide("Test state")
    
    assert "Missing 'choice'" in str(exc_info.value)


async def test_missing_choice():
    """6. Missing choice."""
    mock_http = MockAsyncClient(json_data=MALFORMED_NO_CHOICE)
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    with pytest.raises(LayaMalformedResponseError) as exc_info:
        await client.decide("Test state")
    
    assert "Missing 'choice'" in str(exc_info.value)


async def test_missing_confidence():
    """7. Missing confidence."""
    mock_http = MockAsyncClient(json_data=MALFORMED_NO_CONFIDENCE)
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    with pytest.raises(LayaMalformedResponseError) as exc_info:
        await client.decide("Test state")
    
    assert "Missing 'confidence'" in str(exc_info.value)


async def test_invalid_confidence():
    """8. Invalid confidence."""
    mock_http = MockAsyncClient(json_data=MALFORMED_INVALID_CONFIDENCE)
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    with pytest.raises(LayaMalformedResponseError) as exc_info:
        await client.decide("Test state")
    
    assert "could not convert string to float" in str(exc_info.value)


async def test_invalid_choice():
    """8b. Invalid choice."""
    mock_http = MockAsyncClient(json_data=MALFORMED_INVALID_CHOICE)
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    with pytest.raises(LayaMalformedResponseError) as exc_info:
        await client.decide("Test state")
    
    assert "Invalid choice" in str(exc_info.value)


async def test_actual_request_path_and_schema():
    """9. Actual request path & schema."""
    mock_http = MockAsyncClient(json_data=SUCCESS_RESPONSE)
    client = LayaDecisionClient(client=mock_http)  # type: ignore
    
    await client.decide("Context XYZ")
    
    assert len(mock_http.post_calls) == 1
    url, kwargs = mock_http.post_calls[0]
    
    assert url.endswith("/v1/systemone")
    
    payload = kwargs["json"]
    assert payload["state"] == "Context XYZ"
    assert "opt_decision" in payload["questions"]


async def test_configuration():
    """10. Configuration."""
    mock_http = MockAsyncClient(json_data=SUCCESS_RESPONSE)
    
    # Use patch to modify settings temporarily
    with patch("liteads.ad_server.services.laya_client.get_settings") as mock_settings:
        settings_instance = MagicMock()
        settings_instance.laya.base_url = "http://custom-laya:9999/"
        settings_instance.laya.timeout_ms = 2000
        mock_settings.return_value = settings_instance
        
        client = LayaDecisionClient(client=mock_http)  # type: ignore
        await client.decide("Test")
        
        url, _ = mock_http.post_calls[0]
        assert url == "http://custom-laya:9999/v1/systemone"


@pytest.mark.smoke
async def test_real_runtime_smoke():
    """11. Real runtime smoke test against live Laya decision engine."""
    target_url = os.getenv("LAYA_BASE_URL")
    if not target_url:
        settings = get_settings()
        target_url = settings.laya.base_url
        if "laya:8000" in target_url or "laya" in target_url:
            target_url = "http://127.0.0.1:8100"

    try:
        async with httpx.AsyncClient(timeout=3.0) as check_client:
            resp = await check_client.get(f"{target_url.rstrip('/')}/health")
            if resp.status_code != 200:
                pytest.skip(f"Laya health check returned HTTP {resp.status_code} at {target_url}")
    except Exception as exc:
        pytest.skip(f"Laya service not reachable at {target_url}: {exc}")

    client = LayaDecisionClient()
    client.base_url = target_url.rstrip("/")
    result = await client.decide("The campaign is performing very well with low CPA.")
    assert isinstance(result, LayaDecisionResult)
    assert result.choice in [
        "increase_budget_20",
        "decrease_budget_20",
        "shift_to_new_geo",
        "shift_to_new_sector",
        "improve_creative",
        "no_change",
    ]
    assert isinstance(result.confidence, float)
    assert 0.0 <= result.confidence <= 1.0
    assert "answers" in result.raw_response
