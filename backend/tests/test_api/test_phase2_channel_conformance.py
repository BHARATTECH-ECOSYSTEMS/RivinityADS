"""
Phase 2 Provider Contract & Conformance Test Suite.

Verifies:
1. Google Ads OAuth2 refresh-token lifecycle (successful exchange, expired token, invalid grant).
2. Google Ads REST API v18 searchStream, mutate, and request-id extraction.
3. Google Ads rate limit (HTTP 429) backoff handling.
4. Remote object truth: distinguishes confirmed remote objects from local drafts.
5. Reporting truthfulness: real provider metrics (cost_micros conversion), never fake mocks.
6. Production isolation: simulated adapters fail closed in production (cannot connect or report CONNECTED).
7. Dev/test simulation mode: reports distinct SIMULATED status (never CONNECTED), is_connected is False.
8. Provider/local reconciliation: create, update, sync, and disconnect flows.
9. Experimental AI platforms: OpenAI and AI Publisher labeled EXPERIMENTAL; empty delivery is not supply.
10. Secrets encryption at rest and zero leakage in API contracts.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from fastapi import HTTPException
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.channels.ai_publisher import AIPublisherAdapter
from liteads.channels.base import BaseAdvertiserConnector
from liteads.channels.exceptions import (
    ChannelAuthenticationException,
    ChannelRateLimitException,
    ChannelValidationException,
    UnapprovedPublisherException,
)
from liteads.channels.google_ads import GoogleAdsConnector
from liteads.channels.openai_ads import OpenAIAdsConnector
from liteads.channels.schemas import (
    CampaignCreateParams,
    ConnectAdvertiserRequest,
    PublisherContractCreateRequest,
)
from liteads.channels.service import ChannelService
from liteads.common.config import get_settings
from liteads.management.advertiser_api_key_service import decrypt_secret
from liteads.models.ad import Advertiser
from liteads.models.channel import (
    ChannelConnection,
    ExternalCampaignMapping,
    PublisherInventoryContract,
)
from liteads.models.publisher import Publisher


@pytest.fixture
async def advertiser_test(test_db: AsyncSession) -> Advertiser:
    adv = Advertiser(
        name="Phase 2 Advertiser",
        contact_email="phase2_adv@test.com",
        balance=Decimal("2000.0"),
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)
    return adv


@pytest.fixture
async def publisher_test(test_db: AsyncSession) -> Publisher:
    pub = Publisher(
        name="Phase 2 AI Publisher",
        email="phase2_pub@test.com",
        site_url="https://phase2ai.com",
    )
    test_db.add(pub)
    await test_db.commit()
    await test_db.refresh(pub)
    return pub


# -----------------------------------------------------------------------------
# 1. Google Ads OAuth2 Lifecycle & Token Refresh
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_ads_oauth_refresh_token_lifecycle():
    """Verify Google Ads OAuth2 refresh token exchange and expired grant handling."""
    def fake_oauth_handler(request: httpx.Request) -> httpx.Response:
        content = request.read().decode("utf-8")
        if "grant_type=refresh_token" in content:
            if "invalid_refresh_token" in content:
                return httpx.Response(
                    400,
                    json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.fake_google_access_token_12345",
                    "expires_in": 3600,
                    "scope": "https://www.googleapis.com/auth/adwords",
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(fake_oauth_handler)
    mock_client = httpx.AsyncClient(transport=transport)

    # 1. Valid refresh token
    connector = GoogleAdsConnector(
        credentials={
            "developer_token": "dev_tok_valid_9988",
            "client_id": "google_client_id_001.apps.googleusercontent.com",
            "client_secret": "google_client_secret_xyz",
            "refresh_token": "valid_refresh_token_abc",
        },
        account_id="123-456-7890",
        http_client=mock_client,
    )
    token = await connector.get_access_token()
    assert token == "ya29.fake_google_access_token_12345"
    assert connector.token_state == "VALID"

    # 2. Invalid/expired refresh token raises ChannelAuthenticationException
    invalid_connector = GoogleAdsConnector(
        credentials={
            "developer_token": "dev_tok_valid_9988",
            "client_id": "google_client_id_001.apps.googleusercontent.com",
            "client_secret": "google_client_secret_xyz",
            "refresh_token": "invalid_refresh_token",
        },
        account_id="123-456-7890",
        http_client=mock_client,
    )
    with pytest.raises(ChannelAuthenticationException) as exc:
        await invalid_connector.get_access_token()
    assert "invalid_grant" in str(exc.value)
    assert invalid_connector.token_state == "REFRESH_FAILED"


# -----------------------------------------------------------------------------
# 2. Google Ads API searchStream, Request ID Extraction & Mutate
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_ads_searchstream_and_request_id_extraction():
    """Verify live searchStream parsing, request-id extraction, and remote object status."""
    def fake_google_api_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "ya29.valid_token", "expires_in": 3600},
            )
        if "customers:listAccessibleCustomers" in url_str:
            return httpx.Response(
                200,
                headers={"request-id": "req-accessible-customers-111"},
                json={"resourceNames": ["customers/1234567890"]},
            )
        if "googleAds:searchStream" in url_str:
            return httpx.Response(
                200,
                headers={"request-id": "req-stream-search-8888"},
                json=[
                    {
                        "results": [
                            {
                                "campaign": {
                                    "id": 987654321,
                                    "name": "Live Search Campaign Q4",
                                    "status": "ENABLED",
                                    "primaryStatus": "ELIGIBLE",
                                    "advertisingChannelType": "SEARCH",
                                    "resourceName": "customers/1234567890/campaigns/987654321",
                                },
                                "campaignBudget": {
                                    "amountMicros": 25000000,  # $25.00
                                },
                            }
                        ]
                    }
                ],
            )
        return httpx.Response(404, text="Not Found")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_google_api_handler))
    connector = GoogleAdsConnector(
        credentials={
            "developer_token": "dev_token_live_123",
            "client_id": "cid_123",
            "client_secret": "csec_123",
            "refresh_token": "ref_123",
        },
        account_id="123-456-7890",
        http_client=mock_client,
    )

    campaigns = await connector.sync_campaigns()
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c.external_id == "987654321"
    assert c.name == "Live Search Campaign Q4"
    assert c.status == "ACTIVE"
    assert c.remote_object_status == "CONFIRMED_ACTIVE"
    assert c.moderation_status == "ELIGIBLE"
    assert c.daily_budget == 25.0
    assert c.provider_request_id == "req-stream-search-8888"
    assert c.remote_resource_name == "customers/1234567890/campaigns/987654321"


# -----------------------------------------------------------------------------
# 3. Google Ads Rate Limit (HTTP 429) Handling
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_ads_rate_limit_backoff():
    """Verify HTTP 429 rate limit triggers ChannelRateLimitException upon exhaustion."""
    call_count = 0

    def fake_rate_limit_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        url_str = str(request.url)
        if "token" in url_str:
            return httpx.Response(200, json={"access_token": "ya29.rate_token", "expires_in": 3600})
        if "customers:listAccessibleCustomers" in url_str:
            return httpx.Response(200, json={"resourceNames": ["customers/1234567890"]})
        # Always return 429 for API calls
        return httpx.Response(
            429,
            headers={"request-id": "req-rate-limit-429", "Retry-After": "0.01"},
            json={"error": {"code": 429, "message": "RESOURCE_EXHAUSTED", "status": "RESOURCE_EXHAUSTED"}},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_rate_limit_handler))
    connector = GoogleAdsConnector(
        credentials={
            "developer_token": "dev_token_rate",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "ref",
        },
        account_id="1234567890",
        http_client=mock_client,
        max_retries=2,
        backoff_base_sec=0.01,
    )

    with pytest.raises(ChannelRateLimitException) as exc:
        await connector.sync_campaigns()
    assert "rate limit exceeded" in str(exc.value).lower()
    assert call_count >= 2


# -----------------------------------------------------------------------------
# 4. Campaign Mutate Remote Object Truth & Idempotency
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_ads_campaign_mutate_remote_object_truth(
    test_db: AsyncSession, advertiser_test: Advertiser
):
    """Verify campaign creation persists remote object truth and request identifiers."""
    def fake_mutate_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "token" in url_str:
            return httpx.Response(200, json={"access_token": "ya29.mutate_token", "expires_in": 3600})
        if "customers:listAccessibleCustomers" in url_str:
            return httpx.Response(200, json={"resourceNames": ["customers/1234567890"]})
        if "campaigns:mutate" in url_str:
            return httpx.Response(
                200,
                headers={"request-id": "req-mutate-campaign-777"},
                json={
                    "results": [
                        {
                            "resourceName": "customers/1234567890/campaigns/5544332211"
                        }
                    ]
                },
            )
        return httpx.Response(404, text="Not Found")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_mutate_handler))
    service = ChannelService(test_db, http_client=mock_client)

    # 1. Connect
    conn_resp = await service.connect_advertiser_channel(
        advertiser_id=advertiser_test.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="google_ads",
            name="Production Google Ads",
            api_key="dev_token_mut",
            account_id="123-456-7890",
            additional_credentials={
                "client_id": "goog_cid_test",
                "client_secret": "goog_sec_test",
                "refresh_token": "goog_ref_test",
            },
        ),
    )
    assert conn_resp.status == "CONNECTED"
    assert conn_resp.adapter_mode == "LIVE"

    # 2. Create campaign
    idemp_key = "idemp_goog_create_001"
    created = await service.create_campaign(
        advertiser_id=advertiser_test.id,
        connection_id=conn_resp.id,
        params=CampaignCreateParams(
            name="Brand Search Campaign",
            objective="SEARCH",
            daily_budget=50.0,
            confirmed=True,
        ),
        idempotency_key=idemp_key,
        user_id=1,
    )
    assert created.external_id == "5544332211"
    assert created.status == "PAUSED"
    assert created.remote_object_status == "CONFIRMED_PAUSED"
    assert created.moderation_status == "UNDER_REVIEW"
    assert created.provider_request_id == "req-mutate-campaign-777"
    assert created.remote_resource_name == "customers/1234567890/campaigns/5544332211"

    # 3. Idempotent replay returns existing record without new provider mutation
    replayed = await service.create_campaign(
        advertiser_id=advertiser_test.id,
        connection_id=conn_resp.id,
        params=CampaignCreateParams(
            name="Brand Search Campaign",
            objective="SEARCH",
            daily_budget=50.0,
            confirmed=True,
        ),
        idempotency_key=idemp_key,
        user_id=1,
    )
    assert replayed.external_id == created.external_id
    assert replayed.provider_request_id == created.provider_request_id


# -----------------------------------------------------------------------------
# 5. Reporting Truthfulness: Real Metrics Only
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_ads_reporting_truthfulness_real_metrics_only():
    """Verify performance reporting uses real provider micros conversion and never returns fake numbers."""
    def fake_reporting_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "token" in url_str:
            return httpx.Response(200, json={"access_token": "ya29.rep_token", "expires_in": 3600})
        if "customers:listAccessibleCustomers" in url_str:
            return httpx.Response(200, json={"resourceNames": ["customers/1234567890"]})
        if "googleAds:searchStream" in url_str:
            return httpx.Response(
                200,
                headers={"request-id": "req-rep-stream-444"},
                json=[
                    {
                        "results": [
                            {
                                "campaign": {"id": 5544332211},
                                "segments": {"date": "2026-11-10"},
                                "metrics": {
                                    "impressions": 12450,
                                    "clicks": 432,
                                    "conversions": 19,
                                    "costMicros": 18750000,  # $18.75
                                },
                                "customer": {"currencyCode": "USD"},
                            }
                        ]
                    }
                ],
            )
        return httpx.Response(404, text="Not Found")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_reporting_handler))
    connector = GoogleAdsConnector(
        credentials={
            "developer_token": "dev_tok",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "ref",
        },
        account_id="1234567890",
        http_client=mock_client,
    )

    rows = await connector.sync_reporting(date(2026, 11, 10), date(2026, 11, 10))
    assert len(rows) == 1
    r = rows[0]
    assert r.external_campaign_id == "5544332211"
    assert r.impressions == 12450
    assert r.clicks == 432
    assert r.conversions == 19
    assert r.spend == 18.75
    assert r.currency == "USD"
    assert r.is_synced is True
    assert r.is_simulated is False


# -----------------------------------------------------------------------------
# 6. Production Isolation: Simulations Fail Closed
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_simulated_adapters_fail_closed_in_production(
    test_db: AsyncSession, advertiser_test: Advertiser, monkeypatch
):
    """Prove that simulated adapters cannot connect or report CONNECTED in production."""
    settings = get_settings()
    monkeypatch.setattr(settings, "env", "prod")

    service = ChannelService(test_db)

    # 1. OpenAI without live enterprise route fails closed in prod
    with pytest.raises(HTTPException) as exc:
        await service.connect_advertiser_channel(
            advertiser_id=advertiser_test.id,
            user_id=1,
            req=ConnectAdvertiserRequest(
                provider="openai",
                name="Production Attempt",
                api_key="oa_ads_live_key_9999",
                account_id="act_prod_fail",
            ),
        )
    assert exc.value.status_code == 403
    assert "prohibited from simulation in production" in exc.value.detail

    # 2. Attempting simulation via additional_credentials fails closed in prod
    with pytest.raises(HTTPException) as exc2:
        await service.connect_advertiser_channel(
            advertiser_id=advertiser_test.id,
            user_id=1,
            req=ConnectAdvertiserRequest(
                provider="google_ads",
                name="Simulated Google Prod Attempt",
                api_key="dev_tok",
                account_id="1234567890",
                additional_credentials={"is_simulation": True},
            ),
        )
    assert exc2.value.status_code == 403
    assert "strictly prohibited in production" in exc2.value.detail


# -----------------------------------------------------------------------------
# 7. Dev/Test Simulation Mode: Distinct SIMULATED Status
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_simulated_adapter_distinct_status_in_dev(
    test_db: AsyncSession, advertiser_test: Advertiser, monkeypatch
):
    """In development/test, simulated adapters report status=SIMULATED and is_connected=False."""
    settings = get_settings()
    monkeypatch.setattr(settings, "env", "dev")

    service = ChannelService(test_db)
    resp = await service.connect_advertiser_channel(
        advertiser_id=advertiser_test.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="Dev Simulated OpenAI",
            api_key="oa_ads_test_998877",
            account_id="act_dev_sim_001",
        ),
    )
    assert resp.status == "SIMULATED"
    assert resp.adapter_mode == "SIMULATED"
    assert resp.token_state == "SIMULATED"

    # Verify is_connected property on database model is False
    conn = await test_db.get(ChannelConnection, resp.id)
    assert conn is not None
    assert conn.is_connected is False


# -----------------------------------------------------------------------------
# 8. Experimental AI Publisher Delivery & Contract Boundaries
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ai_publisher_empty_delivery_truth(
    test_db: AsyncSession, publisher_test: Publisher
):
    """Empty delivery results must never be treated as an active connected supply source."""
    service = ChannelService(test_db)
    now = datetime.now(timezone.utc)

    # Register verified contract
    contract_resp = await service.register_publisher_contract(
        publisher_id=publisher_test.id,
        req=PublisherContractCreateRequest(
            platform_name="AI Partner Delivery",
            signed_agreement_id="AGR-PHASE2-TEST-001",
            approved_surfaces=["chat_sidebar_slot", "response_footer_slot"],
            policy_terms_hash="hash_phase2_terms_abc123",
            contract_valid_until=now + timedelta(days=90),
        ),
        user_id=1,
    )
    assert contract_resp.is_valid is True

    # Adapter capability check
    contract = await test_db.get(PublisherInventoryContract, contract_resp.id)
    adapter = AIPublisherAdapter(publisher_test.id, contract=contract)
    caps = adapter.get_capabilities()
    assert caps.adapter_mode == "EXPERIMENTAL"
    assert caps.is_experimental is True

    # Empty deliver_inventory() indicates absence of supply
    ads = await adapter.deliver_inventory("chat_sidebar_slot", user_context={})
    assert ads == []  # Not a connected supply stream


# -----------------------------------------------------------------------------
# 9. Security & Secrets Review: Encryption at Rest and Zero Leakage
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_secrets_encryption_and_zero_leakage(
    test_db: AsyncSession, advertiser_test: Advertiser
):
    """Tokens are encrypted at rest in DB and completely absent from API responses."""
    raw_secret = "ultra_sensitive_google_refresh_token_xyz999"
    service = ChannelService(test_db)

    def fake_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "token" in url_str:
            return httpx.Response(200, json={"access_token": "ya29.secret_token", "expires_in": 3600})
        if "customers:listAccessibleCustomers" in url_str:
            return httpx.Response(200, json={"resourceNames": ["customers/1234567890"]})
        return httpx.Response(404, text="Not Found")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_handler))
    service.http_client = mock_client

    resp = await service.connect_advertiser_channel(
        advertiser_id=advertiser_test.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="google_ads",
            name="Encrypted Secrets Connection",
            api_key="developer_token_secret_123",
            account_id="123-456-7890",
            additional_credentials={
                "client_id": "my_client_id.apps.googleusercontent.com",
                "client_secret": "my_client_secret_xyz",
                "refresh_token": raw_secret,
            },
        ),
    )

    # 1. Zero raw secrets in API response
    json_resp = resp.model_dump_json()
    assert raw_secret not in json_resp
    assert "my_client_secret_xyz" not in json_resp

    # 2. Database verification: encrypted at rest
    conn = await test_db.get(ChannelConnection, resp.id)
    assert conn is not None
    assert conn.credentials_encrypted != raw_secret
    assert raw_secret not in conn.credentials_encrypted

    # 3. Decrypt matches
    decrypted = decrypt_secret(conn.credentials_encrypted)
    assert raw_secret in decrypted


# -----------------------------------------------------------------------------
# 10. Google Ads Credential Presence Alone Rejected (Never Connected)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_ads_credential_presence_alone_rejected(
    test_db: AsyncSession, advertiser_test: Advertiser
):
    """Presence of credential fields alone without valid OAuth verification MUST fail and NEVER report CONNECTED."""
    def fake_failing_oauth_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "Bad credentials or expired refresh token."},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_failing_oauth_handler))
    service = ChannelService(test_db, http_client=mock_client)

    # Attempt connect with dummy/unverified credentials
    with pytest.raises(HTTPException) as exc:
        await service.connect_advertiser_channel(
            advertiser_id=advertiser_test.id,
            user_id=1,
            req=ConnectAdvertiserRequest(
                provider="google_ads",
                name="Unverified Google Ads Attempt",
                api_key="non_empty_developer_token_123",
                account_id="123-456-7890",
                additional_credentials={
                    "client_id": "non_empty_client_id.apps.googleusercontent.com",
                    "client_secret": "non_empty_secret",
                    "refresh_token": "non_empty_refresh_token",
                },
            ),
        )
    assert exc.value.status_code == 400
    assert "Connection authentication failed" in exc.value.detail
    assert "invalid_grant" in exc.value.detail

    # Database assertion: zero connected records created
    stmt = select(ChannelConnection).where(
        ChannelConnection.advertiser_id == advertiser_test.id,
        ChannelConnection.provider == "google_ads",
    )
    res = await test_db.execute(stmt)
    assert res.scalar_one_or_none() is None


# -----------------------------------------------------------------------------
# 11. Google Ads Error Surfacing Sanitized and Zero Raw Secret Leakage
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_ads_error_surfacing_sanitized_no_token_leak():
    """Provider 401/429/5xx error surfaces sanitized reason and never leaks raw tokens or client secrets."""
    raw_secret = "super_secret_oauth_refresh_token_leak_probe_9999"
    raw_client_secret = "top_secret_client_secret_xyz"

    def fake_error_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "token" in url_str:
            return httpx.Response(
                401,
                json={"error": "unauthorized_client", "error_description": "The OAuth client is not authorized."},
            )
        return httpx.Response(500, text="Internal Provider Failure")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_error_handler))
    connector = GoogleAdsConnector(
        credentials={
            "developer_token": "dev_token_test",
            "client_id": "client_id_test",
            "client_secret": raw_client_secret,
            "refresh_token": raw_secret,
        },
        account_id="1234567890",
        http_client=mock_client,
    )

    is_valid, err_msg, metadata = await connector.verify_credentials()
    assert is_valid is False
    assert "unauthorized_client" in err_msg
    # Ensure neither raw refresh token nor raw client secret is leaked into error message
    assert raw_secret not in err_msg
    assert raw_client_secret not in err_msg
    assert metadata.get("token_state") == "REFRESH_FAILED"


# -----------------------------------------------------------------------------
# 12. Tenant Isolation & Anti-Generic-Proxy Enforcement
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_channel_tenant_isolation_and_anti_proxy(
    test_db: AsyncSession, advertiser_test: Advertiser
):
    """Compromised or foreign tenant cannot view, mutate, sync, report, or disconnect another tenant's channel."""
    from liteads.models.auth import User
    from liteads.ad_server.routers.channels import (
        get_connection_details,
        discover_accounts,
        sync_campaigns,
        preview_campaign_creation,
        create_campaign,
        update_campaign_status,
        sync_reporting,
        disconnect_account,
        verify_publisher_surface,
    )

    # 1. Create Connection for Advertiser A
    service = ChannelService(test_db)
    conn_a = await service.connect_advertiser_channel(
        advertiser_id=advertiser_test.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="Adv A Channel",
            api_key="oa_ads_test_iso_1",
            account_id="act_iso_1",
        ),
    )

    # 2. Foreign tenant user B (advertiser_id = 9999)
    foreign_user = User(
        id=999,
        email="foreign_advertiser@test.com",
        role="advertiser",
        advertiser_id=9999,
    )

    # Verify every endpoint rejects foreign tenant with 404/403
    with pytest.raises(HTTPException) as exc:
        await get_connection_details(connection_id=conn_a.id, user=foreign_user, service=service)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await discover_accounts(connection_id=conn_a.id, user=foreign_user, service=service)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await sync_campaigns(connection_id=conn_a.id, user=foreign_user, service=service)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await preview_campaign_creation(
            connection_id=conn_a.id,
            params=CampaignCreateParams(name="Probe", daily_budget=10.0, confirmed=True),
            user=foreign_user,
            service=service,
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await create_campaign(
            connection_id=conn_a.id,
            params=CampaignCreateParams(name="Probe", daily_budget=10.0, confirmed=True),
            idempotency_key="idemp_foreign_probe",
            user=foreign_user,
            service=service,
        )
    assert exc.value.status_code == 404

    from liteads.channels.schemas import CampaignStatusUpdateRequest
    with pytest.raises(HTTPException) as exc:
        await update_campaign_status(
            connection_id=conn_a.id,
            external_campaign_id="ext_probe",
            req=CampaignStatusUpdateRequest(status="PAUSED"),
            user=foreign_user,
            service=service,
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await sync_reporting(
            connection_id=conn_a.id,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 2),
            user=foreign_user,
            service=service,
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await disconnect_account(connection_id=conn_a.id, user=foreign_user, service=service)
    assert exc.value.status_code == 404

    # 3. Unassociated caller (role != admin, advertiser_id is None) gets 403 Forbidden
    unassociated_user = User(
        id=888,
        email="unassociated@test.com",
        role="viewer",
        advertiser_id=None,
    )
    with pytest.raises(HTTPException) as exc:
        await get_connection_details(connection_id=conn_a.id, user=unassociated_user, service=service)
    assert exc.value.status_code == 403
    assert "User is not associated with an advertiser account" in exc.value.detail

    with pytest.raises(HTTPException) as exc:
        await verify_publisher_surface(surface_id="slot_probe", user=unassociated_user, service=service)
    assert exc.value.status_code == 403
    assert "User is not associated with a publisher account" in exc.value.detail


# -----------------------------------------------------------------------------
# 13. OpenAI Ads Simulation Truthfulness & ID Prefix
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openai_ads_synthetic_id_prefix_and_production_block(
    test_db: AsyncSession, advertiser_test: Advertiser, monkeypatch
):
    """Simulated campaigns must carry explicit 'sim_oa_cmp_' prefix; production unconditionally blocks simulation."""
    settings = get_settings()
    monkeypatch.setattr(settings, "env", "dev")

    service = ChannelService(test_db)
    conn = await service.connect_advertiser_channel(
        advertiser_id=advertiser_test.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="OpenAI Sim Test",
            api_key="oa_ads_test_sim_prefix",
            account_id="act_sim_pref",
        ),
    )

    created = await service.create_campaign(
        advertiser_id=advertiser_test.id,
        connection_id=conn.id,
        params=CampaignCreateParams(
            name="Simulated Promotion",
            objective="TRAFFIC",
            daily_budget=20.0,
            confirmed=True,
        ),
        idempotency_key="idemp_sim_prefix_001",
        user_id=1,
    )
    assert created.external_id.startswith("sim_oa_cmp_")

    # In production mode, OpenAI connector verify_credentials returns False and create_campaign fails closed
    monkeypatch.setattr(settings, "env", "prod")
    prod_connector = OpenAIAdsConnector(
        credentials={"api_key": "oa_ads_live_key"},
        account_id="act_prod_probe",
        env="prod",
    )
    is_valid, err, _ = await prod_connector.verify_credentials()
    assert is_valid is False
    assert "no public general-availability self-serve API" in err

    # Reporting in non-simulated mode returns empty list (never synthetic metrics)
    rows = await prod_connector.sync_reporting(date(2026, 11, 1), date(2026, 11, 2))
    assert rows == []


# -----------------------------------------------------------------------------
# 14. Google Ads Disconnect Cleans Secrets and Revokes Session
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_ads_disconnect_clears_credentials_and_revokes(
    test_db: AsyncSession, advertiser_test: Advertiser
):
    """Disconnect clears stored encrypted secrets and marks token state REVOKED."""
    def fake_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "token" in url_str:
            return httpx.Response(200, json={"access_token": "ya29.disc_token", "expires_in": 3600})
        if "customers:listAccessibleCustomers" in url_str:
            return httpx.Response(200, json={"resourceNames": ["customers/1234567890"]})
        return httpx.Response(404, text="Not Found")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_handler))
    service = ChannelService(test_db, http_client=mock_client)

    conn_resp = await service.connect_advertiser_channel(
        advertiser_id=advertiser_test.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="google_ads",
            name="Disconnect Test Connection",
            api_key="dev_tok_disc",
            account_id="123-456-7890",
            additional_credentials={
                "client_id": "cid_disc",
                "client_secret": "csec_disc",
                "refresh_token": "ref_disc",
            },
        ),
    )
    assert conn_resp.status == "CONNECTED"

    # Disconnect
    ok = await service.disconnect(advertiser_id=advertiser_test.id, connection_id=conn_resp.id)
    assert ok is True

    # Verify DB state
    conn = await test_db.get(ChannelConnection, conn_resp.id)
    assert conn is not None
    assert conn.status == "DISCONNECTED"
    assert conn.token_state == "REVOKED"
    assert conn.credentials_encrypted == ""


# -----------------------------------------------------------------------------
# 15. Explicit Capability Flags Contract
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_channel_capability_flags_explicit_truth():
    """Verify that all adapters expose truthful capability flags."""
    # 1. Google Ads
    g_conn = GoogleAdsConnector(credentials={}, account_id="1234567890")
    g_caps = g_conn.get_capabilities()
    assert g_caps.supports_live_delivery is False  # Management only
    assert g_caps.supports_oauth is True
    assert g_caps.supports_reporting is True
    assert g_caps.is_simulated is False
    assert g_caps.is_experimental is False
    assert g_caps.adapter_mode == "LIVE"

    # 2. OpenAI Ads
    oa_conn = OpenAIAdsConnector(credentials={"api_key": "oa_ads_test_caps"}, account_id="act_caps", env="dev")
    oa_caps = oa_conn.get_capabilities()
    assert oa_caps.supports_live_delivery is False
    assert oa_caps.supports_oauth is False
    assert oa_caps.supports_reporting is True
    assert oa_caps.is_simulated is True
    assert oa_caps.is_experimental is True
    assert oa_caps.adapter_mode == "SIMULATED"

    # 3. AI Publisher
    pub_adapter = AIPublisherAdapter(publisher_id=1)
    pub_caps = pub_adapter.get_capabilities()
    assert pub_caps.supports_live_delivery is True
    assert pub_caps.supports_oauth is False
    assert pub_caps.supports_reporting is True
    assert pub_caps.is_simulated is False
    assert pub_caps.is_experimental is True
    assert pub_caps.adapter_mode == "EXPERIMENTAL"

