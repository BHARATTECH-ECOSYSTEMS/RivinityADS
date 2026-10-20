import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch

from liteads.models.auth import OneTimeToken, User


@pytest.mark.asyncio
async def test_register_creates_unverified_user_with_verification_token(
    client: AsyncClient, test_db: AsyncSession
):
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Test Advertiser",
                "email": "test4a@example.com",
                "password": "Password123!",
                "role": "advertiser",
            },
        )

        assert response.status_code == 201
        user = await test_db.scalar(select(User).where(User.email == "test4a@example.com"))
        assert user is not None
        assert not user.email_verified, "Newly registered user should not be email-verified automatically"
        token = await test_db.scalar(
            select(OneTimeToken).where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == "email_verification",
            )
        )
        assert token is not None, "Registration must create an email_verification OneTimeToken"
        assert token.used_at is None
        assert mock_send.call_count == 1
        assert "Verify your RivinityADS account" in mock_send.call_args[1]["subject"]


@pytest.mark.asyncio
async def test_verification_endpoints_are_active(client: AsyncClient):
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock), \
         patch("liteads.ad_server.routers.auth.check_resend_verification_rate_limit"):
        verify_response = await client.post(
            "/api/v1/auth/verify-email",
            json={"token": "invalid_or_unused_token"},
        )
        resend_response = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "test4a@example.com"},
        )

        # Endpoints are active and functional (not 404)
        assert verify_response.status_code == 400
        assert verify_response.json()["detail"] == "Verification token is invalid"
        assert resend_response.status_code == 200
        assert "If an unverified account exists" in resend_response.json()["message"]
