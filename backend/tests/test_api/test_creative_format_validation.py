"""Test Ad Creative Format Selection, Dimensions Enforcement & Backwards Compatibility."""

from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.security import create_access_token
from liteads.models.ad import Advertiser, Campaign, Creative, AdSet
from liteads.models.domain import AdvertiserDomain
from liteads.models import User
from liteads.models.base import CreativeType, ModerationStatus, Status


@pytest.fixture
def auth_headers():
    def _get_headers(user: User):
        token = create_access_token(user)
        return {"Authorization": f"Bearer {token}"}

    return _get_headers


@pytest.fixture
async def setup_test_context(test_db: AsyncSession):
    """Seed test advertiser, campaign, ad set, and verified domain."""
    adv_user = User(
        name="Format Adv User",
        email="format_adv@test.com",
        password_hash="hashed",
        role="advertiser",
        email_verified=True,
    )
    test_db.add(adv_user)
    await test_db.flush()

    advertiser = Advertiser(
        name="Format Advertiser",
        owner_user_id=adv_user.id,
        balance=Decimal("2000"),
    )
    test_db.add(advertiser)
    await test_db.flush()
    adv_user.advertiser_id = advertiser.id

    campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Format Test Campaign",
        budget_daily=Decimal("200"),
        budget_total=Decimal("2000"),
        bid_amount=Decimal("2"),
        status=Status.ACTIVE,
    )
    test_db.add(campaign)
    await test_db.flush()

    ad_set = AdSet(
        campaign_id=campaign.id,
        name="Format Test AdSet",
        status=Status.ACTIVE,
        daily_budget=Decimal("100"),
        bid_type=1,
        bid_amount=Decimal("2"),
    )
    test_db.add(ad_set)
    await test_db.flush()

    domain = AdvertiserDomain(
        advertiser_id=advertiser.id,
        domain="example.com",
        status="VERIFIED",
        verification_token="token_verified",
    )
    test_db.add(domain)
    await test_db.commit()

    return {
        "user": adv_user,
        "advertiser": advertiser,
        "campaign": campaign,
        "ad_set": ad_set,
    }


@pytest.mark.asyncio
async def test_create_display_card_assigns_520x120(
    client: AsyncClient,
    test_db: AsyncSession,
    auth_headers,
    setup_test_context,
):
    ctx = setup_test_context
    headers = auth_headers(ctx["user"])

    payload = {
        "campaign_id": ctx["campaign"].id,
        "ad_set_id": ctx["ad_set"].id,
        "title": "Display Card Ad",
        "description": "Conversational assistant recommendation card",
        "category": "ai_chatbot",
        "format": "display_card",
        "landing_url": "https://example.com/card",
    }

    res = await client.post("/api/v1/creative", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["category"] == "ai_chatbot"
    assert data["format"] == "display_card"
    assert data["creative_type"] == CreativeType.DISPLAY_CARD.value
    assert data["width"] == 520
    assert data["height"] == 120

    # Verify persistence in database
    db_creative = await test_db.get(Creative, data["id"])
    assert db_creative is not None
    assert db_creative.category == "ai_chatbot"
    assert db_creative.format == "display_card"
    assert db_creative.width == 520
    assert db_creative.height == 120


@pytest.mark.asyncio
async def test_create_label_ad_assigns_400x100(
    client: AsyncClient,
    test_db: AsyncSession,
    auth_headers,
    setup_test_context,
):
    ctx = setup_test_context
    headers = auth_headers(ctx["user"])

    payload = {
        "campaign_id": ctx["campaign"].id,
        "ad_set_id": ctx["ad_set"].id,
        "title": "Label Ad",
        "description": "Compact suggestion label",
        "category": "ai_chatbot",
        "format": "label_ad",
        "landing_url": "https://example.com/label",
    }

    res = await client.post("/api/v1/creative", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["category"] == "ai_chatbot"
    assert data["format"] == "label_ad"
    assert data["creative_type"] == CreativeType.LABEL_AD.value
    assert data["width"] == 400
    assert data["height"] == 100


@pytest.mark.asyncio
async def test_create_element_ad_assigns_310x150(
    client: AsyncClient,
    test_db: AsyncSession,
    auth_headers,
    setup_test_context,
):
    ctx = setup_test_context
    headers = auth_headers(ctx["user"])

    payload = {
        "campaign_id": ctx["campaign"].id,
        "ad_set_id": ctx["ad_set"].id,
        "title": "Element Ad",
        "description": "Embedded interactive element",
        "category": "ai_chatbot",
        "format": "element_ad",
        "landing_url": "https://example.com/element",
    }

    res = await client.post("/api/v1/creative", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["category"] == "ai_chatbot"
    assert data["format"] == "element_ad"
    assert data["creative_type"] == CreativeType.ELEMENT_AD.value
    assert data["width"] == 310
    assert data["height"] == 150


@pytest.mark.asyncio
async def test_create_banner_ad_assigns_500x500(
    client: AsyncClient,
    test_db: AsyncSession,
    auth_headers,
    setup_test_context,
):
    ctx = setup_test_context
    headers = auth_headers(ctx["user"])

    payload = {
        "campaign_id": ctx["campaign"].id,
        "ad_set_id": ctx["ad_set"].id,
        "title": "Square Banner Ad",
        "description": "Prominent display banner",
        "category": "banner",
        "format": "banner",
        "landing_url": "https://example.com/banner",
    }

    res = await client.post("/api/v1/creative", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["category"] == "banner"
    assert data["format"] == "banner"
    assert data["creative_type"] == CreativeType.BANNER.value
    assert data["width"] == 500
    assert data["height"] == 500


@pytest.mark.asyncio
async def test_reject_arbitrary_dimensions_for_banner(
    client: AsyncClient,
    auth_headers,
    setup_test_context,
):
    ctx = setup_test_context
    headers = auth_headers(ctx["user"])

    # Attempting to submit non-500x500 dimensions for banner
    payload = {
        "campaign_id": ctx["campaign"].id,
        "ad_set_id": ctx["ad_set"].id,
        "title": "Arbitrary Banner",
        "category": "banner",
        "format": "banner",
        "width": 300,
        "height": 250,
        "landing_url": "https://example.com/banner",
    }

    res = await client.post("/api/v1/creative", json=payload, headers=headers)
    assert res.status_code == 422
    assert "Invalid width for Banner Ad" in res.text


@pytest.mark.asyncio
async def test_reject_arbitrary_dimensions_for_display_card(
    client: AsyncClient,
    auth_headers,
    setup_test_context,
):
    ctx = setup_test_context
    headers = auth_headers(ctx["user"])

    payload = {
        "campaign_id": ctx["campaign"].id,
        "ad_set_id": ctx["ad_set"].id,
        "title": "Invalid Card",
        "category": "ai_chatbot",
        "format": "display_card",
        "width": 600,
        "height": 120,
        "landing_url": "https://example.com/card",
    }

    res = await client.post("/api/v1/creative", json=payload, headers=headers)
    assert res.status_code == 422
    assert "Invalid width for Display Card" in res.text


@pytest.mark.asyncio
async def test_reject_unsupported_category_and_format(
    client: AsyncClient,
    auth_headers,
    setup_test_context,
):
    ctx = setup_test_context
    headers = auth_headers(ctx["user"])

    # 1. Unsupported category
    res1 = await client.post(
        "/api/v1/creative",
        json={
            "campaign_id": ctx["campaign"].id,
            "title": "Test",
            "category": "popunder_ads",
            "landing_url": "https://example.com",
        },
        headers=headers,
    )
    assert res1.status_code == 422
    assert "Invalid ad category" in res1.text

    # 2. Unsupported format under AI and Chatbot
    res2 = await client.post(
        "/api/v1/creative",
        json={
            "campaign_id": ctx["campaign"].id,
            "title": "Test",
            "category": "ai_chatbot",
            "format": "floating_video",
            "landing_url": "https://example.com",
        },
        headers=headers,
    )
    assert res2.status_code == 422
    assert "Unsupported format" in res2.text

    # 3. Missing format when AI and Chatbot selected
    res3 = await client.post(
        "/api/v1/creative",
        json={
            "campaign_id": ctx["campaign"].id,
            "title": "Test",
            "category": "ai_chatbot",
            "landing_url": "https://example.com",
        },
        headers=headers,
    )
    assert res3.status_code == 422
    assert "Subcategory is required" in res3.text


@pytest.mark.asyncio
async def test_legacy_creatives_remain_compatible(
    client: AsyncClient,
    test_db: AsyncSession,
    auth_headers,
    setup_test_context,
):
    ctx = setup_test_context
    headers = auth_headers(ctx["user"])

    # Insert a legacy creative directly into DB with 300x250 dimensions
    legacy_creative = Creative(
        campaign_id=ctx["campaign"].id,
        ad_set_id=ctx["ad_set"].id,
        title="Historical 300x250 Ad",
        creative_type=1,
        category="legacy",
        format="legacy_banner",
        width=300,
        height=250,
        landing_url="https://example.com/legacy",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED,
    )
    test_db.add(legacy_creative)
    await test_db.commit()
    await test_db.refresh(legacy_creative)

    # Must be retrievable via GET /api/v1/creative/{id} without error
    res = await client.get(f"/api/v1/creative/{legacy_creative.id}", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["id"] == legacy_creative.id
    assert data["width"] == 300
    assert data["height"] == 250
    assert data["title"] == "Historical 300x250 Ad"
