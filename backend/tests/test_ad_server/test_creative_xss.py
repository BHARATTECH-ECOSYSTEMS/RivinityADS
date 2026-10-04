import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from liteads.models import Advertiser, Campaign, Creative, AdSlot, Publisher
import html

@pytest.fixture
async def setup_ad_data(test_db: AsyncSession):
    # Create publisher and slot
    pub = Publisher(id=10, name="Pub XSS")
    slot = AdSlot(slot_id="slot_xss", publisher_id=10, name="Slot XSS")
    
    # Create advertiser and campaign
    adv = Advertiser(id=10, name="Adv XSS", balance=100.0)
    camp = Campaign(id=10, advertiser_id=10, name="Camp XSS", bid_type=1, bid_amount=1.0)
    
    test_db.add_all([pub, slot, adv, camp])
    await test_db.commit()
    return camp.id

@pytest.mark.asyncio
async def test_xss_in_serve_endpoint(client: AsyncClient, test_db: AsyncSession, setup_ad_data: int):
    # Create a creative with malicious payloads directly in DB
    malicious_title = "<script>alert(1)</script>"
    malicious_desc = "<img src=x onerror=alert(1)>"
    malicious_image = 'https://example.com/" onerror="alert(1)'
    malicious_landing = 'https://example.com/" onclick="alert(1)'
    
    creative = Creative(
        campaign_id=setup_ad_data,
        title=malicious_title,
        description=malicious_desc,
        image_url=malicious_image,
        landing_url=malicious_landing,
        creative_type=1,
        status=1,
    )
    test_db.add(creative)
    await test_db.commit()
    
    # Request the ad via /serve
    res = await client.get("/api/v1/ad/serve?slot_id=slot_xss")
    assert res.status_code == 200
    html_content = res.text
    
    # Verify escaping
    assert malicious_title not in html_content
    assert html.escape(malicious_title, quote=True) in html_content
    
    # ensure no unescaped onerror attribute
    assert 'onerror="alert(1)' not in html_content
    assert html.escape(malicious_image, quote=True) in html_content

@pytest.mark.asyncio
async def test_xss_native_fallback(client: AsyncClient, test_db: AsyncSession, setup_ad_data: int):
    malicious_title = "<script>alert(1)</script>"
    malicious_desc = "<img src=x onerror=alert(1)>"
    
    creative = Creative(
        campaign_id=setup_ad_data,
        title=malicious_title,
        description=malicious_desc,
        landing_url="https://example.com",
        creative_type=1,  # Keep 1 so it matches ad request, but omit image_url to trigger native fallback
        status=1,
    )
    test_db.add(creative)
    await test_db.commit()
    
    res = await client.get("/api/v1/ad/serve?slot_id=slot_xss")
    assert res.status_code == 200
    html_content = res.text
    
    assert malicious_title not in html_content
    assert html.escape(malicious_title, quote=True) in html_content
    
    assert malicious_desc not in html_content
    assert html.escape(malicious_desc, quote=True) in html_content

@pytest.mark.asyncio
async def test_url_validation_api(client: AsyncClient, test_db: AsyncSession):
    # Create advertiser session
    res = await client.post("/api/v1/auth/register", json={
        "name": "Adv URL Test", "email": "url@test.com", "password": "password1234", "role": "advertiser"
    })
    token = res.json()["access_token"]
    adv_id = res.json()["user"]["advertiser_id"]
    client.headers = {"Authorization": f"Bearer {token}"}
    
    # Make a campaign for this advertiser
    camp_res = await client.post("/api/v1/campaign", json={
        "advertiser_id": adv_id,
        "name": "Test Camp",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0,
        "bid_type": 1,
        "status": 1
    })
    camp_id = camp_res.json()["id"]
    
    # Try invalid URLs
    invalid_urls = [
        "javascript(1)",
        "javascript:alert(1)",
        "data/html,<script>alert(1)</script>",
        "http://example.com",
        "ftp://example.com",
    ]
    
    for url in invalid_urls:
        res_creative = await client.post("/api/v1/creative", json={
            "campaign_id": camp_id,
            "title": "Test",
            "landing_url": url,
            "creative_type": 1
        })
        assert res_creative.status_code == 422
        
    # Try valid URL
    res_valid = await client.post("/api/v1/creative", json={
        "campaign_id": camp_id,
        "title": "Test",
        "landing_url": "https://example.com",
        "creative_type": 1
    })
    assert res_valid.status_code == 201

@pytest.mark.asyncio
async def test_legitimate_creative(client: AsyncClient, test_db: AsyncSession, setup_ad_data: int):
    # Legitimate creative
    creative = Creative(
        campaign_id=setup_ad_data,
        title="Best Developer Tools",
        description="Tools for modern developers",
        image_url="https://cdn.example.com/banner.png",
        landing_url="https://example.com/product",
        creative_type=1,
        status=1,
    )
    test_db.add(creative)
    await test_db.commit()
    
    res = await client.get("/api/v1/ad/serve?slot_id=slot_xss")
    assert res.status_code == 200
    html_content = res.text
    
    assert 'alt="Best Developer Tools"' in html_content
    assert 'href="https://example.com/product"' in html_content
    assert 'src="https://cdn.example.com/banner.png"' in html_content
