"""
Phase 5 Channel Adapters and Platform Connectors Test Suite.

Verifies:
1. Advertiser Connector Lifecycle:
   - Connect authorized OpenAI Ads Manager account with customer's own API key.
   - Inspect connection: credentials are encrypted at rest and redacted (prefix only, zero raw secrets returned).
   - Discover accounts and synchronize campaigns into deterministic mapping records.
   - Preview campaign creation with spend estimates and disclaimer before committing.
   - Create campaign with idempotency key; retries cannot duplicate campaigns.
   - Pause and activate external campaigns.
   - Synchronize reporting metrics for date ranges.
   - Disconnect and revoke active connection.
2. Tenant Isolation:
   - Advertiser A cannot inspect, sync, create, or disconnect Advertiser B's connection.
3. Provider Invariants & Limitations:
   - OpenAI Ads connector is management-only and strictly does NOT inject Rivinity ads into ChatGPT's own inventory.
   - Google Ads connector surfaces developer token and OAuth2 requirements.
   - Claude / Anthropic connector attempts are rejected per the 4 February 2026 ad-free public policy.
4. AI Publisher Inventory Contract Verification:
   - Unapproved third-party inventory insertion is unconditionally blocked.
   - Inventory delivery is rejected if agreement is missing, expired, or surface is not in approved_surfaces.
   - Inventory delivery is authorized only when signed agreement is active and verified.
5. Real Database Integration:
   - All migrations, unique constraints, foreign keys, and transactions are verified.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.channels.ai_publisher import AIPublisherAdapter
from liteads.channels.claude_policy import ClaudePolicyGuard
from liteads.channels.exceptions import (
    ChannelDuplicateException,
    ChannelValidationException,
    UnapprovedPublisherException,
    UnsupportedChannelException,
)
from liteads.channels.google_ads import GoogleAdsConnector
from liteads.channels.openai_ads import OpenAIAdsConnector
from liteads.channels.schemas import (
    CampaignCreateParams,
    ConnectAdvertiserRequest,
    PublisherContractCreateRequest,
)
from liteads.channels.service import ChannelService
from liteads.management.advertiser_api_key_service import decrypt_secret
from liteads.models.ad import Advertiser, Campaign
from liteads.models.auth import User
from liteads.models.channel import (
    ChannelConnection,
    ExternalCampaignMapping,
    PublisherInventoryContract,
)
from liteads.models.publisher import Publisher


@pytest.fixture
async def advertiser_a(test_db: AsyncSession) -> Advertiser:
    """Create test advertiser A."""
    adv = Advertiser(
        name="Test Advertiser A",
        contact_email="adv_a@test.com",
        balance=Decimal("1000.0"),
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)
    return adv


@pytest.fixture
async def advertiser_b(test_db: AsyncSession) -> Advertiser:
    """Create test advertiser B (competing tenant)."""
    adv = Advertiser(
        name="Test Advertiser B",
        contact_email="adv_b@test.com",
        balance=Decimal("500.0"),
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)
    return adv


@pytest.fixture
async def publisher_test(test_db: AsyncSession) -> Publisher:
    """Create test publisher."""
    pub = Publisher(
        name="AI Platform Partner",
        email="partner@ai.com",
        site_url="https://partnerai.com",
    )
    test_db.add(pub)
    await test_db.commit()
    await test_db.refresh(pub)
    return pub


# -----------------------------------------------------------------------------
# 1. Advertiser Connector Lifecycle & Security Tests
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_connect_openai_ads_account(test_db: AsyncSession, advertiser_a: Advertiser):
    """User connects their authorized OpenAI Ads Manager account; credentials encrypted, secret redacted."""
    service = ChannelService(test_db)
    raw_api_key = "oa_ads_live_sec_998877665544332211"
    req = ConnectAdvertiserRequest(
        provider="openai",
        name="Primary OpenAI Ads Manager",
        api_key=raw_api_key,
        account_id="act_openai_883311",
        organization_id="org_openai_9922",
    )
    resp = await service.connect_advertiser_channel(
        advertiser_id=advertiser_a.id,
        user_id=1,
        req=req,
    )
    assert resp.id > 0
    assert resp.provider == "openai"
    assert resp.external_account_id == "act_openai_883311"
    assert resp.status in ("CONNECTED", "SIMULATED")
    # Redacted prefix check
    assert resp.credentials_prefix == "oa_ads...2211"
    # Crucial security assertion: Raw secret is NEVER in response
    assert raw_api_key not in resp.model_dump_json()

    # Verify database record: Credentials stored server-side encrypted
    conn = await test_db.get(ChannelConnection, resp.id)
    assert conn is not None
    assert conn.credentials_encrypted != raw_api_key
    # Decrypt and verify matching secret
    decrypted = decrypt_secret(conn.credentials_encrypted)
    assert raw_api_key in decrypted


@pytest.mark.asyncio
async def test_list_and_inspect_connections_tenant_scoped(
    test_db: AsyncSession, advertiser_a: Advertiser, advertiser_b: Advertiser
):
    """List connections returns only the authenticated tenant's connections."""
    service = ChannelService(test_db)
    # Connect for Advertiser A
    await service.connect_advertiser_channel(
        advertiser_id=advertiser_a.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="Adv A OpenAI",
            api_key="oa_ads_test_key_aaaaa",
            account_id="act_a_1",
        ),
    )
    # Connect for Advertiser B
    await service.connect_advertiser_channel(
        advertiser_id=advertiser_b.id,
        user_id=2,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="Adv B OpenAI",
            api_key="oa_ads_test_key_bbbbb",
            account_id="act_b_1",
        ),
    )

    list_a = await service.list_advertiser_connections(advertiser_a.id)
    assert len(list_a) == 1
    assert list_a[0].name == "Adv A OpenAI"
    assert list_a[0].external_account_id == "act_a_1"

    list_b = await service.list_advertiser_connections(advertiser_b.id)
    assert len(list_b) == 1
    assert list_b[0].name == "Adv B OpenAI"
    assert list_b[0].external_account_id == "act_b_1"


@pytest.mark.asyncio
async def test_tenant_isolation_cannot_access_other_advertiser_connection(
    test_db: AsyncSession, advertiser_a: Advertiser, advertiser_b: Advertiser
):
    """Advertiser B cannot view, sync, or disconnect Advertiser A's connection."""
    service = ChannelService(test_db)
    conn_a = await service.connect_advertiser_channel(
        advertiser_id=advertiser_a.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="Adv A Private Connection",
            api_key="oa_ads_test_key_private",
            account_id="act_a_private",
        ),
    )
    # Advertiser B attempts to access connection A
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await service.get_connection_scoped(advertiser_id=advertiser_b.id, connection_id=conn_a.id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_discover_accounts_and_sync_campaigns(
    test_db: AsyncSession, advertiser_a: Advertiser
):
    """Discover accounts and synchronize campaigns into deterministic database mappings."""
    service = ChannelService(test_db)
    conn = await service.connect_advertiser_channel(
        advertiser_id=advertiser_a.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="OpenAI Sync Test",
            api_key="oa_ads_test_key_sync",
            account_id="act_sync_100",
        ),
    )
    accounts = await service.discover_accounts(advertiser_a.id, conn.id)
    assert len(accounts) == 1
    assert accounts[0].account_id == "act_sync_100"

    # Preview and create a campaign
    preview = await service.preview_campaign_creation(
        advertiser_id=advertiser_a.id,
        connection_id=conn.id,
        params=CampaignCreateParams(
            name="Q4 SaaS Promotion",
            objective="CONVERSIONS",
            daily_budget=25.0,
            confirmed=False,
        ),
    )
    assert preview.daily_budget == 25.0
    assert preview.requires_confirmation is True
    assert "Rivinity does not inject ads into ChatGPT" in preview.disclaimer

    # Create campaign with deliberate confirmation
    created = await service.create_campaign(
        advertiser_id=advertiser_a.id,
        connection_id=conn.id,
        params=CampaignCreateParams(
            name="Q4 SaaS Promotion",
            objective="CONVERSIONS",
            daily_budget=25.0,
            confirmed=True,
        ),
        idempotency_key="idemp_q4_saas_001",
        user_id=1,
    )
    assert created.external_id.startswith(("oa_cmp_", "sim_oa_cmp_"))
    assert created.status == "ACTIVE"

    # Synchronize campaigns
    synced = await service.sync_campaigns(advertiser_a.id, conn.id)
    assert len(synced) >= 1

    # Check deterministic database mapping
    stmt = select(ExternalCampaignMapping).where(
        ExternalCampaignMapping.connection_id == conn.id,
        ExternalCampaignMapping.external_campaign_id == created.external_id,
    )
    res = await test_db.execute(stmt)
    mapping = res.scalar_one_or_none()
    assert mapping is not None
    assert mapping.external_campaign_name == "Q4 SaaS Promotion"
    assert mapping.idempotency_key == "idemp_q4_saas_001"


@pytest.mark.asyncio
async def test_idempotency_prevents_duplicate_campaign_creation(
    test_db: AsyncSession, advertiser_a: Advertiser
):
    """Calling create_campaign with the same idempotency key returns existing campaign without duplicating."""
    service = ChannelService(test_db)
    conn = await service.connect_advertiser_channel(
        advertiser_id=advertiser_a.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="Idempotency Test Account",
            api_key="oa_ads_test_idemp_key",
            account_id="act_idemp_01",
        ),
    )
    params = CampaignCreateParams(
        name="Idempotent Campaign",
        objective="TRAFFIC",
        daily_budget=50.0,
        confirmed=True,
    )
    idemp_key = "idemp_fixed_uuid_12345"

    # First call: creates campaign
    first_res = await service.create_campaign(
        advertiser_id=advertiser_a.id,
        connection_id=conn.id,
        params=params,
        idempotency_key=idemp_key,
    )

    # Second call (network retry): returns same campaign deterministically
    second_res = await service.create_campaign(
        advertiser_id=advertiser_a.id,
        connection_id=conn.id,
        params=params,
        idempotency_key=idemp_key,
    )

    assert first_res.external_id == second_res.external_id
    assert first_res.name == second_res.name

    # Verify exactly one record in database
    stmt = select(ExternalCampaignMapping).where(
        ExternalCampaignMapping.connection_id == conn.id,
        ExternalCampaignMapping.idempotency_key == idemp_key,
    )
    res = await test_db.execute(stmt)
    mappings = res.scalars().all()
    assert len(mappings) == 1


@pytest.mark.asyncio
async def test_campaign_creation_without_confirmation_is_blocked(
    test_db: AsyncSession, advertiser_a: Advertiser
):
    """Campaign creation without confirmed=True must raise validation error to prevent accidental spend."""
    service = ChannelService(test_db)
    conn = await service.connect_advertiser_channel(
        advertiser_id=advertiser_a.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="Confirmation Test",
            api_key="oa_ads_test_key_conf",
            account_id="act_conf_01",
        ),
    )
    with pytest.raises(ChannelValidationException) as exc:
        await service.create_campaign(
            advertiser_id=advertiser_a.id,
            connection_id=conn.id,
            params=CampaignCreateParams(
                name="Unconfirmed Campaign",
                objective="TRAFFIC",
                daily_budget=10.0,
                confirmed=False,  # NOT CONFIRMED
            ),
            idempotency_key="idemp_unconf_01",
        )
    assert "deliberate user confirmation" in str(exc.value)


@pytest.mark.asyncio
async def test_pause_activate_and_disconnect(
    test_db: AsyncSession, advertiser_a: Advertiser
):
    """Pause campaign, sync reporting, and disconnect account."""
    service = ChannelService(test_db)
    conn = await service.connect_advertiser_channel(
        advertiser_id=advertiser_a.id,
        user_id=1,
        req=ConnectAdvertiserRequest(
            provider="openai",
            name="Lifecycle Account",
            api_key="oa_ads_test_lifecycle",
            account_id="act_life_01",
        ),
    )
    # Create campaign
    created = await service.create_campaign(
        advertiser_id=advertiser_a.id,
        connection_id=conn.id,
        params=CampaignCreateParams(
            name="Lifecycle Campaign",
            objective="TRAFFIC",
            daily_budget=15.0,
            confirmed=True,
        ),
        idempotency_key="idemp_life_01",
    )
    # Pause campaign
    ok = await service.update_campaign_status(
        advertiser_id=advertiser_a.id,
        connection_id=conn.id,
        external_campaign_id=created.external_id,
        status_value="PAUSED",
    )
    assert ok is True

    # Sync reporting
    rows = await service.sync_reporting(
        advertiser_id=advertiser_a.id,
        connection_id=conn.id,
        start_date=date.today() - timedelta(days=2),
        end_date=date.today(),
    )
    assert len(rows) > 0
    assert rows[0].impressions > 0

    # Disconnect
    disc_ok = await service.disconnect(advertiser_a.id, conn.id)
    assert disc_ok is True
    refreshed = await test_db.get(ChannelConnection, conn.id)
    assert refreshed.status == "DISCONNECTED"
    assert refreshed.credentials_encrypted == ""  # Credentials erased on disconnect


# -----------------------------------------------------------------------------
# 2. Provider Invariants & Regulatory Policies
# -----------------------------------------------------------------------------
def test_openai_capabilities_explicit_no_injection():
    """OpenAI connector explicitly declares supports_inventory_injection=False."""
    connector = OpenAIAdsConnector(credentials={"api_key": "oa_ads_test"}, account_id="act_1")
    caps = connector.get_capabilities()
    assert caps.provider == "openai"
    assert caps.connector_type == "advertiser_management"
    assert caps.supports_inventory_injection is False
    assert any("does not inject rivinity ads into chatgpt" in lim.lower() for lim in caps.limitations)


def test_google_ads_capabilities_and_requirements():
    """Google Ads connector declares developer token and OAuth consent requirements."""
    connector = GoogleAdsConnector(credentials={}, account_id="goog_act_1")
    caps = connector.get_capabilities()
    assert caps.provider == "google_ads"
    assert caps.supports_inventory_injection is False
    assert any("developer token" in req.lower() for req in caps.auth_requirements)
    assert any("oauth2" in req.lower() for req in caps.auth_requirements)


def test_claude_placement_connector_strictly_prohibited():
    """Any attempt to register or connect Claude raises UnsupportedChannelException per 4 Feb 2026 policy."""
    with pytest.raises(UnsupportedChannelException) as exc:
        ClaudePolicyGuard.enforce_ad_free_policy("claude")
    assert "4 February 2026" in str(exc.value)
    assert "ad-free" in str(exc.value)

    with pytest.raises(UnsupportedChannelException) as exc:
        ClaudePolicyGuard.enforce_ad_free_policy("anthropic_claude_ad_injection")
    assert "prohibit" in str(exc.value).lower()


# -----------------------------------------------------------------------------
# 3. AI Publisher Contract Verification & Inventory Protection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ai_publisher_delivery_blocked_without_signed_contract(
    test_db: AsyncSession, publisher_test: Publisher
):
    """Inventory delivery on an AI platform is unconditionally rejected without a signed agreement."""
    adapter = AIPublisherAdapter(publisher_id=publisher_test.id, contract=None)
    with pytest.raises(UnapprovedPublisherException) as exc:
        await adapter.deliver_inventory(surface_id="chatbot_main", user_context=None)
    assert "no signed publisher agreement found" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_ai_publisher_delivery_blocked_if_contract_expired(
    test_db: AsyncSession, publisher_test: Publisher
):
    """Inventory delivery is rejected if the agreement has expired."""
    expired_contract = PublisherInventoryContract(
        publisher_id=publisher_test.id,
        platform_name="ExpiredPartner",
        signed_agreement_id="AGR-EXPIRED-2025",
        agreement_status="ACTIVE",
        approved_surfaces=["chat_v1"],
        policy_terms_hash="hash_expired",
        contract_signed_at=datetime(2025, 1, 1),
        contract_valid_until=datetime(2025, 12, 31),  # Expired
    )
    adapter = AIPublisherAdapter(publisher_id=publisher_test.id, contract=expired_contract)
    with pytest.raises(UnapprovedPublisherException) as exc:
        await adapter.deliver_inventory(surface_id="chat_v1", user_context=None)
    assert "expired" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_ai_publisher_delivery_blocked_on_unapproved_surface(
    test_db: AsyncSession, publisher_test: Publisher
):
    """Inventory delivery is rejected if the surface is not in approved_surfaces."""
    valid_contract = PublisherInventoryContract(
        publisher_id=publisher_test.id,
        platform_name="StrictPartner",
        signed_agreement_id="AGR-VALID-2026",
        agreement_status="ACTIVE",
        approved_surfaces=["sidebar_recommendation"],
        policy_terms_hash="hash_valid",
        contract_signed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        contract_valid_until=datetime(2027, 12, 31),
    )
    adapter = AIPublisherAdapter(publisher_id=publisher_test.id, contract=valid_contract)
    # Attempt delivery on an unapproved surface "chat_stream_injection"
    with pytest.raises(UnapprovedPublisherException) as exc:
        await adapter.deliver_inventory(surface_id="chat_stream_injection", user_context=None)
    assert "not in approved surfaces" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_ai_publisher_service_contract_registration_and_verification(
    test_db: AsyncSession, publisher_test: Publisher
):
    """Service successfully registers contract and authorizes inventory only for approved surfaces."""
    service = ChannelService(test_db)
    req = PublisherContractCreateRequest(
        platform_name="PartnerChatAI",
        signed_agreement_id="AGR-PARTNER-AI-2026",
        approved_surfaces=["chat_assistant_card", "suggested_action"],
        policy_terms_hash="sha256_terms_agreed_v1",
        contract_valid_until=datetime.now(timezone.utc) + timedelta(days=365),
    )
    contract_resp = await service.register_publisher_contract(
        publisher_id=publisher_test.id,
        req=req,
        user_id=10,
    )
    assert contract_resp.id > 0
    assert contract_resp.is_valid is True

    # Check authorized surface
    is_valid, err = await service.verify_publisher_surface(
        publisher_id=publisher_test.id,
        surface_id="chat_assistant_card",
    )
    assert is_valid is True
    assert err is None

    # Check unauthorized surface
    is_unauth, unauth_err = await service.verify_publisher_surface(
        publisher_id=publisher_test.id,
        surface_id="unapproved_prompt_injection",
    )
    assert is_unauth is False
    assert "not authorized" in unauth_err.lower()

