import pytest
from httpx import AsyncClient, ASGITransport
from liteads.ad_server.main import app
from liteads.common.database import get_session

@pytest.fixture
async def advertiser_a(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "Adv A", "email": "a@adv.com", "password": "password1234", "role": "advertiser"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        yield {"client": c, "user": data["user"]}
        app.dependency_overrides.clear()

@pytest.fixture
async def advertiser_b(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "Adv B", "email": "b@adv.com", "password": "password1234", "role": "advertiser"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        yield {"client": c, "user": data["user"]}
        app.dependency_overrides.clear()

@pytest.fixture
async def publisher_a(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "Pub A", "email": "a@pub.com", "password": "password1234", "role": "publisher"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        yield {"client": c, "user": data["user"]}
        app.dependency_overrides.clear()

@pytest.fixture
async def publisher_b(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "Pub B", "email": "b@pub.com", "password": "password1234", "role": "publisher"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        yield {"client": c, "user": data["user"]}
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_advertiser_cross_tenant_isolation(advertiser_a, advertiser_b):
    adv_a_id = advertiser_a["user"]["advertiser_id"]
    client_a = advertiser_a["client"]
    client_b = advertiser_b["client"]
    
    # A creates campaign
    res = await client_a.post("/api/v1/campaign", json={
        "advertiser_id": adv_a_id,
        "name": "Camp A",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0,
        "bid_type": 1,
        "status": 1
    })
    assert res.status_code == 201
    camp_a_id = res.json()["id"]

    # B tries to get A's campaign
    res = await client_b.get(f"/api/v1/campaign/{camp_a_id}")
    assert res.status_code in [403, 404]

    # B tries to create campaign for A
    res = await client_b.post("/api/v1/campaign", json={
        "advertiser_id": adv_a_id,
        "name": "Camp B to A",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0,
        "bid_type": 1,
        "status": 1
    })
    assert res.status_code in [403, 404]

    # B tries to update A's campaign
    res = await client_b.patch(f"/api/v1/campaign/{camp_a_id}", json={"name": "Hacked"})
    assert res.status_code in [403, 404]

    # B tries to add a creative to A's campaign
    res = await client_b.post("/api/v1/creative", json={
        "campaign_id": camp_a_id,
        "title": "Hack Creative",
        "image_url": "https://img.com",
        "landing_url": "https://land.com"
    })
    assert res.status_code in [403, 404]

@pytest.mark.asyncio
async def test_publisher_cross_tenant_isolation(publisher_a, publisher_b):
    pub_a_id = publisher_a["user"]["publisher_id"]
    client_a = publisher_a["client"]
    client_b = publisher_b["client"]
    
    res = await client_b.get(f"/api/v1/publisher/{pub_a_id}")
    assert res.status_code in [403, 404]
@pytest.mark.asyncio
async def test_login_rate_limiting(test_db):
    from unittest.mock import patch, AsyncMock
    
    class MockPipeline:
        def __init__(self, state):
            self.state = state
            self.commands = []
            
        def incr(self, key):
            self.commands.append(("incr", key))
            return self
            
        def expire(self, key, ttl):
            self.commands.append(("expire", key, ttl))
            return self
            
        async def execute(self):
            results = []
            for cmd, *args in self.commands:
                if cmd == "incr":
                    key = args[0]
                    self.state[key] = self.state.get(key, 0) + 1
                    results.append(self.state[key])
                elif cmd == "expire":
                    results.append(True)
            self.commands.clear()
            return results
            
        @property
        def command_stack(self):
            return self.commands
            
    state = {}
    with patch("liteads.common.security.redis_client.pipeline") as mock_pipe:
        def get_pipeline():
            return MockPipeline(state)
        mock_pipe.side_effect = get_pipeline

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            app.dependency_overrides[get_session] = lambda: test_db
            # register
            await c.post("/api/v1/auth/register", json={"name": "Pub Rate", "email": "r@pub.com", "password": "password1234", "role": "publisher"})
            
            # 5 failed logins
            for _ in range(5):
                res = await c.post("/api/v1/auth/login", json={"email": "r@pub.com", "password": "wrongpassword"})
                assert res.status_code == 401
                
            # 6th login should be 429 even if password is correct
            res = await c.post("/api/v1/auth/login", json={"email": "r@pub.com", "password": "password1234"})
            assert res.status_code == 429
            
            app.dependency_overrides.clear()
