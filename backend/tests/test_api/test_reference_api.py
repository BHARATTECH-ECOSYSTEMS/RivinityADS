"""
Tests for reference data APIs.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from liteads.ad_server.main import app
from liteads.common.database import get_session
from liteads.models.geo import GeoLocation

pytestmark = pytest.mark.asyncio

@pytest.fixture
async def advertiser_client(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "AdvRef", "email": "adv_ref@adv.com", "password": "password1234", "role": "advertiser"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        
        yield c
        app.dependency_overrides.clear()


@pytest.fixture
async def sample_geo_data(test_db):
    """Insert sample geo locations."""
    locations = [
        GeoLocation(country="IN", state_code="MH", state_name="Maharashtra", city="Mumbai", pincode="400001", lat=18.9, lon=72.8),
        GeoLocation(country="IN", state_code="MH", state_name="Maharashtra", city="Mumbai", pincode="400002", lat=18.95, lon=72.81),
        GeoLocation(country="IN", state_code="MH", state_name="Maharashtra", city="Pune", pincode="411001", lat=18.5, lon=73.8),
        GeoLocation(country="IN", state_code="KA", state_name="Karnataka", city="Bangalore", pincode="560001", lat=12.9, lon=77.5),
        GeoLocation(country="US", state_code="NY", state_name="New York", city="New York", pincode="10001", lat=40.7, lon=-74.0),
    ]
    for loc in locations:
        test_db.add(loc)
    await test_db.flush()
    return locations


async def test_get_sectors(advertiser_client):
    response = await advertiser_client.get("/api/v1/reference/sectors")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert "developers" in data
    assert "data/AI" in data
    assert len(data) == 24


async def test_get_countries(advertiser_client, sample_geo_data):
    response = await advertiser_client.get("/api/v1/reference/locations/countries")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert "IN" in data
    assert "US" in data
    assert len(data) == 2


async def test_get_states(advertiser_client, sample_geo_data):
    response = await advertiser_client.get("/api/v1/reference/locations/states?country=IN")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    codes = [s["code"] for s in data]
    assert "MH" in codes
    assert "KA" in codes

    # Test case insensitivity
    response2 = await advertiser_client.get("/api/v1/reference/locations/states?country=in")
    assert response2.json() == data


async def test_get_cities(advertiser_client, sample_geo_data):
    response = await advertiser_client.get("/api/v1/reference/locations/cities?country=IN&state=MH")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert "Mumbai" in data
    assert "Pune" in data


async def test_get_pincodes(advertiser_client, sample_geo_data):
    response = await advertiser_client.get("/api/v1/reference/locations/pincodes?country=IN&state=MH&city=Mumbai")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert "400001" in data
    assert "400002" in data


async def test_reference_auth_required(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        response = await c.get("/api/v1/reference/sectors")
        assert response.status_code == 401
        app.dependency_overrides.clear()
