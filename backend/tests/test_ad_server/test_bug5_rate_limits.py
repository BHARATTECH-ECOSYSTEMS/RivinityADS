import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession
from redis.exceptions import ConnectionError, TimeoutError

from liteads.models import User, Advertiser
from liteads.common.security import hash_password
from liteads.common.cache import redis_client
from liteads.common.config import get_settings

@pytest.fixture(autouse=True)
async def clear_redis():
    try:
        if redis_client.connected:
            await redis_client.client.flushdb()
    except Exception:
        pass
    yield
    try:
        if redis_client.connected:
            await redis_client.client.flushdb()
    except Exception:
        pass

@pytest.fixture
async def setup_test_user(test_db: AsyncSession):
    adv = Advertiser(name="Adv Test")
    test_db.add(adv)
    await test_db.flush()
    
    user = User(
        name="Test User",
        email="target@example.com",
        password_hash=hash_password("password123"),
        role="advertiser",
        advertiser_id=adv.id,
        is_active=True
    )
    test_db.add(user)
    await test_db.commit()
    return user

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

@pytest.fixture
def mock_redis():
    state = {}
    with patch("liteads.common.security.redis_client.pipeline") as mock_pipe:
        def get_pipeline():
            return MockPipeline(state)
        mock_pipe.side_effect = get_pipeline
        yield state

@pytest.mark.asyncio
async def test_normal_login_below_limits(client: AsyncClient, setup_test_user, mock_redis):
    # Test 3: Normal login below limits
    res = await client.post("/api/v1/auth/login", json={
        "email": "target@example.com",
        "password": "password123"
    })
    assert res.status_code == 200
    assert "access_token" in res.json()

@pytest.mark.asyncio
async def test_ip_limit_different_emails(client: AsyncClient, setup_test_user, mock_redis):
    # Test 1 & 4: Same IP, different emails
    max_attempts = get_settings().auth.max_login_attempts
    
    for i in range(max_attempts):
        res = await client.post("/api/v1/auth/login", json={
            "email": f"random{i}@example.com",
            "password": "wrong"
        })
        assert res.status_code == 401
        
    # IP is now blocked
    res = await client.post("/api/v1/auth/login", json={
        "email": "another_random@example.com",
        "password": "wrong"
    })
    assert res.status_code == 429
    assert res.json()["detail"] == "Too many login attempts"

@pytest.mark.asyncio
async def test_account_limit_different_ips(client: AsyncClient, setup_test_user, mock_redis):
    # Test 2 & 5: Same account, different IPs
    max_attempts = get_settings().auth.max_login_attempts
    
    with patch("fastapi.Request.client") as mock_client:
        for i in range(max_attempts):
            from dataclasses import dataclass
            
            @dataclass
            class MockClient:
                host: str
                
            mock_client.__get__ = lambda self, instance, owner: MockClient(host=f"192.168.1.{i}")
            
            res = await client.post("/api/v1/auth/login", json={
                "email": "target@example.com",
                "password": "wrong"
            })
            assert res.status_code == 401
            
        mock_client.__get__ = lambda self, instance, owner: MockClient(host="192.168.1.99")
        res = await client.post("/api/v1/auth/login", json={
            "email": "target@example.com",
            "password": "wrong"
        })
        assert res.status_code == 429
        assert res.json()["detail"] == "Too many login attempts"

@pytest.mark.asyncio
async def test_email_normalization(client: AsyncClient, setup_test_user, mock_redis):
    # Test 6: Email normalization
    max_attempts = get_settings().auth.max_login_attempts
    
    # Use different casing and spacing
    variations = [
        " Target@example.com",
        "TARGET@EXAMPLE.COM ",
        "tArGeT@example.com",
        "target@example.com"
    ]
    
    for i in range(max_attempts):
        email = variations[i % len(variations)]
        res = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "wrong"
        })
        assert res.status_code == 401
        
    # Now it should be rate limited
    res = await client.post("/api/v1/auth/login", json={
        "email": " TaRgEt@ExAmPlE.cOm ",
        "password": "wrong"
    })
    assert res.status_code == 429

@pytest.mark.asyncio
async def test_redis_unavailable(client: AsyncClient, setup_test_user):
    # Test 7 & 8: Redis failure fails closed
    with patch("liteads.common.security.redis_client.pipeline") as mock_pipeline:
        mock_pipe_instance = AsyncMock()
        mock_pipe_instance.execute.side_effect = ConnectionError("Redis is down")
        mock_pipeline.return_value = mock_pipe_instance
        
        res = await client.post("/api/v1/auth/login", json={
            "email": "target@example.com",
            "password": "password123"
        })
        
        # Must fail closed with 503
        assert res.status_code == 503
        assert res.json()["detail"] == "Service unavailable"

@pytest.mark.asyncio
async def test_redis_timeout(client: AsyncClient, setup_test_user):
    # Test 7 & 8: Redis timeout fails closed
    with patch("liteads.common.security.redis_client.pipeline") as mock_pipeline:
        mock_pipe_instance = AsyncMock()
        mock_pipe_instance.execute.side_effect = TimeoutError("Redis timeout")
        mock_pipeline.return_value = mock_pipe_instance
        
        res = await client.post("/api/v1/auth/login", json={
            "email": "target@example.com",
            "password": "password123"
        })
        
        assert res.status_code == 503
