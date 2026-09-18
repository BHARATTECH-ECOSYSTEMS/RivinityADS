"""
Phase 4 tests — Multi-Format Delivery + Public API for Third Parties

Covers:
- 4.1  Ad format selection filtering
- 4.2  Publisher API key creation / listing / revocation + auth dependency
- 4.3  HMAC signature verification
- 4.4  /api/v1/ad/serve iframe endpoint
- 4.4  /api/v1/classify text sector endpoint
"""

import hashlib
import hmac
import secrets
import time

import pytest
from httpx import AsyncClient

from liteads.common.security import _HMAC_TIMESTAMP_WINDOW, verify_hmac_signature
from liteads.schemas.request import TextClassifyRequest


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.1 — Ad format field is accepted on AdRequest
# ─────────────────────────────────────────────────────────────────────────────

class TestAdRequestFormatField:
    """format field validation on AdRequest schema."""

    def test_format_none_by_default(self) -> None:
        from liteads.schemas.request import AdRequest, DeviceInfo
        req = AdRequest(slot_id="s1", device=DeviceInfo(os="web"))
        assert req.format is None

    def test_format_valid_values(self) -> None:
        from liteads.schemas.request import AdRequest, DeviceInfo
        for val in (1, 2, 3, 4):
            req = AdRequest(slot_id="s1", device=DeviceInfo(os="web"), format=val)
            assert req.format == val

    def test_format_out_of_range_raises(self) -> None:
        from pydantic import ValidationError
        from liteads.schemas.request import AdRequest, DeviceInfo
        with pytest.raises(ValidationError):
            AdRequest(slot_id="s1", device=DeviceInfo(os="web"), format=5)

    def test_format_zero_raises(self) -> None:
        from pydantic import ValidationError
        from liteads.schemas.request import AdRequest, DeviceInfo
        with pytest.raises(ValidationError):
            AdRequest(slot_id="s1", device=DeviceInfo(os="web"), format=0)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.1 — Format filtering in AdService
# ─────────────────────────────────────────────────────────────────────────────

class TestAdServiceFormatFilter:
    """serve_ads filters candidates by creative_type when format is set."""

    def _make_candidates(self, types: list[int]):
        from liteads.schemas.internal import AdCandidate
        return [
            AdCandidate(
                campaign_id=i,
                creative_id=i,
                advertiser_id=1,
                bid=1.0,
                bid_type=1,
                title=f"Ad {i}",
                landing_url="https://example.com",
                creative_type=t,
            )
            for i, t in enumerate(types, 1)
        ]

    @pytest.mark.asyncio
    async def test_format_filter_keeps_matching(self) -> None:
        """Candidates matching requested format are returned."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from liteads.ad_server.services.ad_service import AdService
        from liteads.schemas.request import AdRequest, DeviceInfo
        from liteads.rec_engine.engine import RecommendationMetrics

        candidates = self._make_candidates([1, 2, 2, 3])  # two native
        metrics = RecommendationMetrics(retrieval_count=4, final_count=4)

        session = AsyncMock()
        service = AdService(session)
        service._engine = MagicMock()
        service._engine.recommend = AsyncMock(return_value=(candidates, metrics))

        req = AdRequest(slot_id="s1", device=DeviceInfo(os="web"), num_ads=4, format=2)
        result = await service.serve_ads(req, "req_test")
        assert all(c.creative_type == 2 for c in result)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_format_filter_fallback_when_no_match(self) -> None:
        """Falls back to unfiltered candidates when format has no match."""
        from unittest.mock import AsyncMock, MagicMock
        from liteads.ad_server.services.ad_service import AdService
        from liteads.schemas.request import AdRequest, DeviceInfo
        from liteads.rec_engine.engine import RecommendationMetrics

        candidates = self._make_candidates([1, 1, 3])  # no native
        metrics = RecommendationMetrics(retrieval_count=3, final_count=3)

        session = AsyncMock()
        service = AdService(session)
        service._engine = MagicMock()
        service._engine.recommend = AsyncMock(return_value=(candidates, metrics))

        req = AdRequest(slot_id="s1", device=DeviceInfo(os="web"), num_ads=3, format=2)
        result = await service.serve_ads(req, "req_test")
        # Falls back to all 3
        assert len(result) == 3


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.2 — Publisher API key security dependency (unit)
# ─────────────────────────────────────────────────────────────────────────────

class TestPublisherApiKeyModel:
    """PublisherApiKey model can be instantiated cleanly."""

    def test_model_fields(self) -> None:
        from datetime import datetime, timezone
        from liteads.models.publisher import PublisherApiKey

        key = PublisherApiKey(
            publisher_id=1,
            label="test",
            key_hash="abc" * 20,
            key_prefix="rk_live_a1b2",
            status=1,
            created_at=datetime.now(timezone.utc),
        )
        assert key.status == 1
        assert key.label == "test"


class TestPublisherFromApiKeyDependency:
    """optional_publisher_from_api_key unit tests (no DB)."""

    @pytest.mark.asyncio
    async def test_missing_header_returns_none(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from fastapi import Request
        from liteads.common.security import optional_publisher_from_api_key

        request = MagicMock(spec=Request)
        request.headers = {}
        session = AsyncMock()
        result = await optional_publisher_from_api_key(request, session)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_key_returns_none(self) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch
        from fastapi import Request
        from sqlalchemy.ext.asyncio import AsyncSession
        from liteads.common.security import optional_publisher_from_api_key

        request = MagicMock(spec=Request)
        request.headers = {"X-API-Key": "bad_key"}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=mock_result)

        result = await optional_publisher_from_api_key(request, session)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_key_returns_publisher_id(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from fastapi import Request
        from sqlalchemy.ext.asyncio import AsyncSession
        from liteads.common.security import optional_publisher_from_api_key
        from liteads.models.publisher import PublisherApiKey
        from datetime import datetime, timezone

        raw_key = "rk_live_" + secrets.token_urlsafe(24)

        mock_key_row = PublisherApiKey(
            publisher_id=42,
            label="test",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            key_prefix=raw_key[:12],
            status=1,
            created_at=datetime.now(timezone.utc),
        )

        request = MagicMock(spec=Request)
        request.headers = {"X-API-Key": raw_key}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_key_row

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=mock_result)

        result = await optional_publisher_from_api_key(request, session)
        assert result == 42


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.3 — HMAC signature verification (unit)
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyHmacSignature:
    """verify_hmac_signature raises HTTP 401 on any tampering."""

    def _make_sig(self, key: str, ts: str, body: bytes) -> str:
        body_sha = hashlib.sha256(body).hexdigest()
        msg = f"{key}|{ts}|{body_sha}".encode()
        return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()

    def test_valid_signature_passes(self) -> None:
        key = "rk_live_testkey"
        ts = str(int(time.time()))
        body = b'{"slot_id":"s1","device":{"os":"web"}}'
        sig = self._make_sig(key, ts, body)
        # Should not raise
        verify_hmac_signature(key, ts, body, sig)

    def test_bad_signature_raises_401(self) -> None:
        from fastapi import HTTPException
        key = "rk_live_testkey"
        ts = str(int(time.time()))
        body = b'{"slot_id":"s1","device":{"os":"web"}}'
        with pytest.raises(HTTPException) as exc_info:
            verify_hmac_signature(key, ts, body, "badsignature" * 3)
        assert exc_info.value.status_code == 401

    def test_stale_timestamp_raises_401(self) -> None:
        from fastapi import HTTPException
        key = "rk_live_testkey"
        stale_ts = str(int(time.time()) - _HMAC_TIMESTAMP_WINDOW - 10)
        body = b'{"slot_id":"s1"}'
        sig = self._make_sig(key, stale_ts, body)
        with pytest.raises(HTTPException) as exc_info:
            verify_hmac_signature(key, stale_ts, body, sig)
        assert exc_info.value.status_code == 401

    def test_non_integer_timestamp_raises_401(self) -> None:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            verify_hmac_signature("key", "not_a_number", b"body", "sig")
        assert exc_info.value.status_code == 401

    def test_body_tampering_raises_401(self) -> None:
        from fastapi import HTTPException
        key = "rk_live_testkey"
        ts = str(int(time.time()))
        original_body = b'{"slot_id":"s1"}'
        sig = self._make_sig(key, ts, original_body)
        tampered_body = b'{"slot_id":"evil"}'
        with pytest.raises(HTTPException) as exc_info:
            verify_hmac_signature(key, ts, tampered_body, sig)
        assert exc_info.value.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.4 — /api/v1/ad/serve iframe endpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestServeIframeEndpoint:
    async def test_serve_returns_html(self, client: AsyncClient) -> None:
        """GET /api/v1/ad/serve should return HTML (even when no ads are served)."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "liteads.ad_server.routers.ad.AdService.serve_ads",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = await client.get("/api/v1/ad/serve?slot_id=test_banner")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    async def test_serve_html_is_valid_structure(self, client: AsyncClient) -> None:
        from unittest.mock import AsyncMock, patch

        with patch(
            "liteads.ad_server.routers.ad.AdService.serve_ads",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = await client.get("/api/v1/ad/serve?slot_id=test_banner")
        body = resp.text
        assert "<!DOCTYPE html>" in body or "<html" in body


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.4 — /api/v1/classify text sector endpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestClassifyEndpoint:
    async def test_classify_finance_text(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/ad/classify",
            json={"text": "The Federal Reserve raised interest rates, stocks and bond yields surged."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "sectors" in data
        assert "scores" in data
        assert "finance" in data["sectors"]

    async def test_classify_tech_text(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/ad/classify",
            json={"text": "New AI software released for cloud machine learning and data science."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "tech" in data["sectors"]

    async def test_classify_no_match_returns_empty(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/ad/classify",
            json={"text": "xyz zzz qqq"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sectors"] == []

    async def test_classify_empty_text_raises_422(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad/classify", json={"text": ""})
        assert resp.status_code == 422

    async def test_classify_scores_are_normalised(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/ad/classify",
            json={"text": "stock market investment bond interest rate bank trading portfolio"},
        )
        assert resp.status_code == 200
        data = resp.json()
        scores = data["scores"]
        assert all(0.0 <= v <= 1.0 for v in scores.values()), "All scores must be in [0, 1]"
        # Top sector score should be 1.0 (normalised)
        assert max(scores.values()) == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.4 — /static/rivinity.js is served
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestRivinityJsServed:
    async def test_rivinity_js_accessible(self, client: AsyncClient) -> None:
        resp = await client.get("/static/rivinity.js")
        assert resp.status_code == 200
        assert "rivinity" in resp.text.lower() or "slot" in resp.text.lower()

    async def test_rivinity_js_content_type(self, client: AsyncClient) -> None:
        resp = await client.get("/static/rivinity.js")
        assert "javascript" in resp.headers.get("content-type", "")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.4 — /public-api-docs page
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPublicApiDocsPage:
    async def test_public_docs_returns_html(self, client: AsyncClient) -> None:
        resp = await client.get("/public-api-docs")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    async def test_public_docs_contains_key_endpoints(self, client: AsyncClient) -> None:
        resp = await client.get("/public-api-docs")
        body = resp.text
        assert "/api/v1/ad/request" in body
        assert "/api/v1/ad/serve" in body
        assert "/api/v1/classify" in body
        assert "rivinity.js" in body
