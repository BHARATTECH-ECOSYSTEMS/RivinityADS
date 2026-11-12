"""Service for managing Advertiser API Keys and S2S Postback Signing Credentials."""

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import os
import secrets
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from liteads.common.config import get_settings
from liteads.common.security import hash_password, verify_password
from liteads.models.ad import Advertiser, AdvertiserApiKey, Campaign
from liteads.models.auth import AuditLog

logger = structlog.get_logger(__name__)


def encrypt_secret(plaintext: str, master_key: str | None = None) -> str:
    """Encrypt secret using authenticated HMAC-CTR keystream with master key."""
    key = (master_key or get_settings().tracking.secret).encode("utf-8")
    iv = os.urandom(16)
    pt = plaintext.encode("utf-8")
    blocks = []
    for i in range((len(pt) + 31) // 32):
        ctr = i.to_bytes(4, "big")
        ks = hmac.new(key, iv + ctr, hashlib.sha256).digest()
        blocks.append(ks)
    keystream = b"".join(blocks)[: len(pt)]
    ct = bytes(p ^ k for p, k in zip(pt, keystream))
    tag = hmac.new(key, iv + ct, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(iv + tag + ct).decode("ascii")


def decrypt_secret(token: str, master_key: str | None = None) -> str:
    """Decrypt authenticated secret using master key. Returns empty string on failure."""
    if not token:
        return ""
    try:
        key = (master_key or get_settings().tracking.secret).encode("utf-8")
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        if len(raw) < 48:
            return ""
        iv, tag, ct = raw[:16], raw[16:48], raw[48:]
        expected_tag = hmac.new(key, iv + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            return ""
        blocks = []
        for i in range((len(ct) + 31) // 32):
            ctr = i.to_bytes(4, "big")
            ks = hmac.new(key, iv + ctr, hashlib.sha256).digest()
            blocks.append(ks)
        keystream = b"".join(blocks)[: len(ct)]
        return bytes(c ^ k for c, k in zip(ct, keystream)).decode("utf-8")
    except Exception as e:
        logger.warning("advertiser_secret_decryption_failed", error=str(e))
        return ""


class AdvertiserApiKeyService:
    """Management and authentication service for advertiser API keys and postback secrets."""

    @staticmethod
    def get_tenant_default_secret(advertiser_id: int) -> str:
        """Derive a deterministic, tenant-isolated signing secret for an advertiser."""
        master_secret = get_settings().tracking.secret.encode("utf-8")
        return hmac.new(master_secret, f"adv_{advertiser_id}".encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def generate_key_pair(is_sandbox: bool = False) -> tuple[str, str, str, str, str, str]:
        """
        Generate a cryptographically random API key and signing secret.
        Returns:
            tuple: (raw_key, key_prefix, key_hash, raw_secret, secret_hash, secret_encrypted)
        """
        random_bytes = secrets.token_urlsafe(32)
        tag = "ak_test" if is_sandbox else "ak_live"
        key_prefix = f"{tag}_{random_bytes[:8]}"
        raw_key = f"{key_prefix}_{random_bytes[8:]}"
        key_hash = hash_password(raw_key)

        raw_secret = f"sec_{tag[3:]}_{secrets.token_urlsafe(32)}"
        secret_hash = hash_password(raw_secret)
        secret_encrypted = encrypt_secret(raw_secret)

        return raw_key, key_prefix, key_hash, raw_secret, secret_hash, secret_encrypted

    @staticmethod
    async def create_api_key(
        db: AsyncSession,
        advertiser_id: int,
        name: str,
        campaign_id: int | None = None,
        is_sandbox: bool = False,
        rate_limit_per_minute: int = 60,
        daily_quota: int = 10000,
        user_id: int | None = None,
        ip_address: str | None = None,
    ) -> tuple[AdvertiserApiKey, str, str]:
        """
        Create a new API key for an advertiser.
        Returns the database model and the RAW KEY + RAW SECRET (shown once to user).
        """
        adv = await db.get(Advertiser, advertiser_id)
        if not adv:
            raise HTTPException(status_code=404, detail="Advertiser not found")

        if campaign_id is not None:
            camp = await db.get(Campaign, campaign_id)
            if not camp or camp.advertiser_id != advertiser_id:
                raise HTTPException(status_code=404, detail="Campaign not found for this advertiser")

        (
            raw_key,
            key_prefix,
            key_hash,
            raw_secret,
            secret_hash,
            secret_encrypted,
        ) = AdvertiserApiKeyService.generate_key_pair(is_sandbox=is_sandbox)

        db_key = AdvertiserApiKey(
            advertiser_id=advertiser_id,
            campaign_id=campaign_id,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            secret_hash=secret_hash,
            secret_encrypted=secret_encrypted,
            is_sandbox=is_sandbox,
            rate_limit_per_minute=rate_limit_per_minute,
            daily_quota=daily_quota,
            revoked_at=None,
        )
        db.add(db_key)

        # Audit event
        db.add(
            AuditLog(
                user_id=user_id,
                action="create_advertiser_api_key",
                ip_address=ip_address,
                details=f"Created key {key_prefix} for advertiser {advertiser_id} (campaign: {campaign_id})",
            )
        )

        await db.commit()
        await db.refresh(db_key)
        return db_key, raw_key, raw_secret

    @staticmethod
    async def rotate_api_key(
        db: AsyncSession,
        advertiser_id: int,
        key_id: int,
        user_id: int | None = None,
        ip_address: str | None = None,
    ) -> tuple[AdvertiserApiKey, str, str]:
        """
        Rotate an existing API key: revokes the old key and issues a replacement.
        """
        stmt = select(AdvertiserApiKey).where(
            AdvertiserApiKey.id == key_id,
            AdvertiserApiKey.advertiser_id == advertiser_id,
        )
        result = await db.execute(stmt)
        old_key = result.scalar_one_or_none()
        if not old_key:
            raise HTTPException(status_code=404, detail="API key not found")
        if old_key.revoked_at is not None:
            raise HTTPException(status_code=400, detail="Cannot rotate an already revoked API key")

        # Revoke old key
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        old_key.revoked_at = now_utc

        # Generate replacement key with same configuration
        (
            raw_key,
            key_prefix,
            key_hash,
            raw_secret,
            secret_hash,
            secret_encrypted,
        ) = AdvertiserApiKeyService.generate_key_pair(is_sandbox=old_key.is_sandbox)

        new_key = AdvertiserApiKey(
            advertiser_id=advertiser_id,
            campaign_id=old_key.campaign_id,
            name=f"{old_key.name} (Rotated)",
            key_prefix=key_prefix,
            key_hash=key_hash,
            secret_hash=secret_hash,
            secret_encrypted=secret_encrypted,
            is_sandbox=old_key.is_sandbox,
            rate_limit_per_minute=old_key.rate_limit_per_minute,
            daily_quota=old_key.daily_quota,
            revoked_at=None,
        )
        db.add(new_key)

        db.add(
            AuditLog(
                user_id=user_id,
                action="rotate_advertiser_api_key",
                ip_address=ip_address,
                details=f"Rotated key {old_key.key_prefix} -> {key_prefix} for advertiser {advertiser_id}",
            )
        )

        await db.commit()
        await db.refresh(new_key)
        return new_key, raw_key, raw_secret

    @staticmethod
    async def revoke_api_key(
        db: AsyncSession,
        advertiser_id: int,
        key_id: int,
        user_id: int | None = None,
        ip_address: str | None = None,
    ) -> bool:
        """Revoke a specific API key."""
        stmt = select(AdvertiserApiKey).where(
            AdvertiserApiKey.id == key_id,
            AdvertiserApiKey.advertiser_id == advertiser_id,
        )
        result = await db.execute(stmt)
        db_key = result.scalar_one_or_none()

        if not db_key or db_key.revoked_at is not None:
            return False

        db_key.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)

        db.add(
            AuditLog(
                user_id=user_id,
                action="revoke_advertiser_api_key",
                ip_address=ip_address,
                details=f"Revoked key {db_key.key_prefix} for advertiser {advertiser_id}",
            )
        )

        await db.commit()
        return True

    @staticmethod
    async def list_api_keys(db: AsyncSession, advertiser_id: int) -> list[AdvertiserApiKey]:
        """List all active and revoked API keys for an advertiser."""
        stmt = (
            select(AdvertiserApiKey)
            .where(AdvertiserApiKey.advertiser_id == advertiser_id)
            .order_by(AdvertiserApiKey.id.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_active_secrets_for_advertiser(
        db: AsyncSession,
        advertiser_id: int,
        campaign_id: int | None = None,
    ) -> list[str]:
        """
        Fetch all active, valid signing secrets for an advertiser (and optional campaign),
        including the tenant-isolated default signing secret.
        """
        secrets_list: list[str] = [
            AdvertiserApiKeyService.get_tenant_default_secret(advertiser_id)
        ]

        # Query all active keys for this advertiser
        stmt = select(AdvertiserApiKey).where(
            AdvertiserApiKey.advertiser_id == advertiser_id,
            AdvertiserApiKey.revoked_at.is_(None),
        )
        result = await db.execute(stmt)
        keys = result.scalars().all()

        for k in keys:
            if k.campaign_id is not None and campaign_id is not None and k.campaign_id != campaign_id:
                continue
            if k.secret_encrypted:
                dec = decrypt_secret(k.secret_encrypted)
                if dec and dec not in secrets_list:
                    secrets_list.append(dec)

        return secrets_list

    @staticmethod
    async def resolve_advertiser_api_key(
        raw_key: str,
        session: AsyncSession,
    ) -> tuple[AdvertiserApiKey, Advertiser]:
        """
        Authenticate an inbound advertiser API key.
        Returns (AdvertiserApiKey, Advertiser).
        Raises HTTPException(401) on invalid/revoked key.
        """
        if not raw_key or not isinstance(raw_key, str):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

        raw_key = raw_key.strip()
        sha256_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        prefixes: list[str] = []
        if len(raw_key) >= 16:
            prefixes.append(raw_key[:16])
        if len(raw_key) >= 12:
            prefixes.append(raw_key[:12])
        if len(raw_key) >= 8:
            prefixes.append(raw_key[:8])

        stmt = select(AdvertiserApiKey).where(
            or_(
                AdvertiserApiKey.key_hash == sha256_hash,
                AdvertiserApiKey.key_prefix.in_(prefixes),
            )
        )
        result = await session.execute(stmt)
        candidates = list(result.scalars().all())

        matching_key: AdvertiserApiKey | None = None
        for candidate in candidates:
            if candidate.key_hash == sha256_hash:
                matching_key = candidate
                break
            try:
                if verify_password(raw_key, candidate.key_hash):
                    matching_key = candidate
                    break
            except Exception:
                continue

        if matching_key is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

        if matching_key.revoked_at is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key is revoked")

        adv = await session.get(Advertiser, matching_key.advertiser_id)
        if not adv or adv.status == 0:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Advertiser account inactive")

        # Update last_used_at
        matching_key.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)

        return matching_key, adv

