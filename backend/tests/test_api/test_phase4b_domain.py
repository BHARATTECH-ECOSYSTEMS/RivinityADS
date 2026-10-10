import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from unittest.mock import patch, AsyncMock, MagicMock
from dns.exception import DNSException
import dns.resolver

from liteads.models.domain import AdvertiserDomain, VerificationStatus
from liteads.models.auth import User
from liteads.common.security import create_access_token


@pytest.fixture
def auth_headers():
    def _get_headers(user: User):
        token = create_access_token(user)
        return {"Authorization": f"Bearer {token}"}
    return _get_headers


@pytest.mark.asyncio
async def test_domain_normalization():
    from liteads.ad_server.services.domain_verification import normalize_domain
    assert normalize_domain("https://www.example.com/path?q=1") == "www.example.com"
    assert normalize_domain("example.com.") == "example.com"
    assert normalize_domain("  SUB.example.com:8080  ") == "sub.example.com"
    
    with pytest.raises(ValueError):
        normalize_domain("localhost")
        
    with pytest.raises(ValueError):
        normalize_domain("192.168.1.1")


@pytest.mark.asyncio
async def test_register_and_list_domain(client: AsyncClient, test_db: AsyncSession, auth_headers):
    # Setup user
    from liteads.models import Advertiser
    user = User(id=400, email="adv400@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Test Adv", owner_user_id=user.id)
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id
    await test_db.commit()

    headers = auth_headers(user)
    
    # Register domain
    resp = await client.post("/api/v1/domain", headers=headers, json={"domain": "https://example.com"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["domain"] == "example.com"
    assert data["status"] == "PENDING"
    assert data["verification_method"] == "DNS_TXT"
    domain_id = data["id"]
    
    # List domains
    resp = await client.get("/api/v1/domain", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    
    # Get instructions
    resp = await client.get(f"/api/v1/domain/{domain_id}/instructions", headers=headers)
    assert resp.status_code == 200
    inst = resp.json()
    assert inst["record_type"] == "TXT"
    assert inst["record_name"] == "_rivinity-challenge.example.com"


@pytest.mark.asyncio
async def test_verify_dns_txt_success(client: AsyncClient, test_db: AsyncSession, auth_headers):
    # Setup user
    from liteads.models import Advertiser
    user = User(id=401, email="adv401@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Test Adv", owner_user_id=user.id)
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id
    await test_db.commit()

    headers = auth_headers(user)
    
    # Register domain
    resp = await client.post("/api/v1/domain", headers=headers, json={"domain": "example.org"})
    domain_id = resp.json()["id"]
    token = resp.json()["verification_token"]
    
    # Mock DNS resolver
    mock_answer = MagicMock()
    mock_answer.strings = [token.encode("utf-8")]
    
    with patch("dns.asyncresolver.Resolver.resolve", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.return_value = [mock_answer]
        
        # Trigger verification
        resp = await client.post(f"/api/v1/domain/{domain_id}/verify", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "VERIFIED"
        
        mock_resolve.assert_called_once_with("_rivinity-challenge.example.org", "TXT")
        
        
@pytest.mark.asyncio
async def test_verify_dns_txt_failure(client: AsyncClient, test_db: AsyncSession, auth_headers):
    # Setup user
    from liteads.models import Advertiser
    user = User(id=402, email="adv402@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Test Adv", owner_user_id=user.id)
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id
    await test_db.commit()

    headers = auth_headers(user)
    
    # Register domain
    resp = await client.post("/api/v1/domain", headers=headers, json={"domain": "example.net"})
    domain_id = resp.json()["id"]
    
    with patch("dns.asyncresolver.Resolver.resolve", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.side_effect = dns.resolver.NXDOMAIN
        
        # Trigger verification
        resp = await client.post(f"/api/v1/domain/{domain_id}/verify", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "FAILED"
        assert "not found" in resp.json()["last_error"]


@pytest.mark.asyncio
async def test_cross_tenant_isolation(client: AsyncClient, test_db: AsyncSession, auth_headers):
    from liteads.models import Advertiser
    user1 = User(id=403, email="adv403@test.com", role="advertiser", name="test1", password_hash="test")
    adv1 = Advertiser(name="Test Adv 1", owner_user_id=user1.id)
    user2 = User(id=404, email="adv404@test.com", role="advertiser", name="test2", password_hash="test")
    adv2 = Advertiser(name="Test Adv 2", owner_user_id=user2.id)
    
    test_db.add_all([user1, adv1, user2, adv2])
    await test_db.commit()
    user1.advertiser_id = adv1.id
    user2.advertiser_id = adv2.id
    await test_db.commit()

    # User 1 registers domain
    resp1 = await client.post("/api/v1/domain", headers=auth_headers(user1), json={"domain": "tenant1.com"})
    domain_id = resp1.json()["id"]
    
    # User 2 tries to access it
    resp2 = await client.get(f"/api/v1/domain/{domain_id}", headers=auth_headers(user2))
    assert resp2.status_code == 404
    
    # User 2 tries to register same domain -> Should fail
    resp3 = await client.post("/api/v1/domain", headers=auth_headers(user2), json={"domain": "tenant1.com"})
    assert resp3.status_code == 400
    
    # User 1 registers it again (idempotent)
    resp4 = await client.post("/api/v1/domain", headers=auth_headers(user1), json={"domain": "tenant1.com"})
    assert resp4.status_code == 201
    assert resp4.json()["id"] == domain_id
