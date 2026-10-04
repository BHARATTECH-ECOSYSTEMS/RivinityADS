import pytest
from httpx import AsyncClient
from liteads.models import User
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch

@pytest.mark.asyncio
async def test_bug7_login_sets_cookie(client: AsyncClient, test_db: AsyncSession):
    # Setup user
    await client.post("/api/v1/auth/register", json={
        "name": "BUG7 Adv",
        "email": "bug7@test.com",
        "password": "Password1234!",
        "role": "advertiser"
    })
    
    # Login
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        res = await client.post("/api/v1/auth/login", json={
            "email": "bug7@test.com",
            "password": "Password1234!"
        })
    
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" not in data
    
    # Verify cookie
    assert "refresh_token" in res.cookies
    cookie = res.cookies["refresh_token"]
    
    # Wait, client.cookies returns string values. We can get the Set-Cookie header to check attributes.
    set_cookie = res.headers.get("set-cookie")
    assert "httponly" in set_cookie.lower()
    assert "secure" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()

@pytest.mark.asyncio
async def test_bug7_refresh_works_with_cookie(client: AsyncClient):
    await client.post("/api/v1/auth/register", json={
        "name": "BUG7 Adv2",
        "email": "bug7_2@test.com",
        "password": "Password1234!",
        "role": "advertiser"
    })
    
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        login_res = await client.post("/api/v1/auth/login", json={
            "email": "bug7_2@test.com",
            "password": "Password1234!"
        })
    
    first_token = login_res.json()["access_token"]
    
    import asyncio
    await asyncio.sleep(1)
    
    # Refresh token (manually extract and send cookie if testclient doesn't persist it for the path correctly)
    cookies = {"refresh_token": login_res.cookies.get("refresh_token")}
    refresh_res = await client.post("/api/v1/auth/refresh", cookies=cookies)
    
    assert refresh_res.status_code == 200
    data = refresh_res.json()
    assert "access_token" in data
    assert "refresh_token" not in data
    new_token = data["access_token"]
    assert new_token != first_token

@pytest.mark.asyncio
async def test_bug7_protected_api_works(client: AsyncClient):
    await client.post("/api/v1/auth/register", json={
        "name": "BUG7 Adv3",
        "email": "bug7_3@test.com",
        "password": "Password1234!",
        "role": "advertiser"
    })
    
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        login_res = await client.post("/api/v1/auth/login", json={
            "email": "bug7_3@test.com",
            "password": "Password1234!"
        })
    
    token = login_res.json()["access_token"]
    
    # Call protected API
    res = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["email"] == "bug7_3@test.com"

@pytest.mark.asyncio
async def test_bug7_logout_clears_cookie(client: AsyncClient):
    await client.post("/api/v1/auth/register", json={
        "name": "BUG7 Adv4",
        "email": "bug7_4@test.com",
        "password": "Password1234!",
        "role": "advertiser"
    })
    
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        login_res = await client.post("/api/v1/auth/login", json={
            "email": "bug7_4@test.com",
            "password": "Password1234!"
        })
    
    # Logout
    logout_cookies = {"refresh_token": login_res.cookies.get("refresh_token")}
    res = await client.post("/api/v1/auth/logout", cookies=logout_cookies)
    assert res.status_code == 200
    
    # Attempt to refresh
    refresh_res = await client.post("/api/v1/auth/refresh", cookies=logout_cookies)
    # Refresh should fail because the token in the DB was revoked
    assert refresh_res.status_code == 401
