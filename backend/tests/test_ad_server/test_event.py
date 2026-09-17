"""
Tests for event tracking endpoints.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from liteads.common.tracking_sig import sign_tracking


@pytest.mark.asyncio
async def test_track_event_post(
    client: AsyncClient,
    sample_event_request: dict[str, Any],
) -> None:
    """Test event tracking via POST."""
    # Add signature
    sig = sign_tracking("test_request_123", "ad_100_200", "")
    sample_event_request["sig"] = sig

    response = await client.post("/api/v1/event/track", json=sample_event_request)

    assert response.status_code == 200
    data = response.json()
    if not data["success"]: raise ValueError(str(data))

@pytest.mark.asyncio
async def test_track_event_post_missing_sig(
    client: AsyncClient,
    sample_event_request: dict[str, Any],
) -> None:
    """Test event tracking fails without signature."""
    sample_event_request.pop("sig", None)
    response = await client.post("/api/v1/event/track", json=sample_event_request)
    assert response.status_code == 400  # explicit signature check

@pytest.mark.asyncio
async def test_track_event_post_invalid_sig(
    client: AsyncClient,
    sample_event_request: dict[str, Any],
) -> None:
    """Test event tracking fails with invalid signature."""
    sample_event_request["sig"] = "1700000000.invalid_signature"
    response = await client.post("/api/v1/event/track", json=sample_event_request)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_track_event_get(client: AsyncClient) -> None:
    """Test event tracking via GET (pixel tracking)."""
    sig = sign_tracking("test_request_123", "ad_100_200", "")
    
    response = await client.get(
        "/api/v1/event/track",
        params={
            "type": "impression",
            "req": "test_request_123",
            "ad": "ad_100_200",
            "sig": sig,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_track_click_event(
    client: AsyncClient,
    sample_event_request: dict[str, Any],
) -> None:
    """Test click event tracking."""
    sig = sign_tracking("test_request_123", "ad_100_200", "")
    
    sample_event_request["event_type"] = "click"
    sample_event_request["sig"] = sig

    response = await client.post("/api/v1/event/track", json=sample_event_request)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_track_conversion_event(
    client: AsyncClient,
    sample_event_request: dict[str, Any],
) -> None:
    """Test conversion event tracking."""
    sig = sign_tracking("test_request_123", "ad_100_200", "")
    
    sample_event_request["event_type"] = "conversion"
    sample_event_request["extra"] = {"value": 100.0}
    sample_event_request["sig"] = sig

    response = await client.post("/api/v1/event/track", json=sample_event_request)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
