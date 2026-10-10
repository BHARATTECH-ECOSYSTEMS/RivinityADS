import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.auth import OneTimeToken, User


@pytest.mark.asyncio
async def test_register_creates_active_user_without_verification_token(
    client: AsyncClient, test_db: AsyncSession
):
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
    assert user.email_verified
    token = await test_db.scalar(
        select(OneTimeToken).where(
            OneTimeToken.user_id == user.id,
            OneTimeToken.purpose == "email_verification",
        )
    )
    assert token is None


@pytest.mark.asyncio
async def test_verification_endpoints_are_removed(client: AsyncClient):
    verify_response = await client.post(
        "/api/v1/auth/verify-email?token=unused",
    )
    resend_response = await client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "test4a@example.com"},
    )

    assert verify_response.status_code == 404
    assert resend_response.status_code == 404
