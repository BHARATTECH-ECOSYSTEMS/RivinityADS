"""Service for managing Publisher API Keys."""

import secrets
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from liteads.models.publisher import PublisherApiKey
from liteads.schemas.management import ApiKeyCreate

from liteads.common.security import hash_password, verify_password

logger = structlog.get_logger(__name__)

class ApiKeyService:
    @staticmethod
    def generate_key_pair(is_sandbox: bool = False) -> tuple[str, str, str]:
        """
        Generate a cryptographically random API key.
        Returns:
            tuple: (raw_key, key_prefix, key_hash)
        """
        random_bytes = secrets.token_urlsafe(32)
        tag = "pk_test" if is_sandbox else "pk_live"
        key_prefix = f"{tag}_{random_bytes[:8]}"
        raw_key = f"{key_prefix}_{random_bytes[8:]}"
        key_hash = hash_password(raw_key)
        return raw_key, key_prefix, key_hash

    @staticmethod
    def verify_key(raw_key: str, key_hash: str) -> bool:
        """Verify an incoming raw key against a stored hash."""
        try:
            return verify_password(raw_key, key_hash)
        except Exception as e:
            logger.error("api_key_verification_failed", error=str(e))
            return False

    @staticmethod
    async def create_api_key(
        db: AsyncSession, publisher_id: int, schema: ApiKeyCreate
    ) -> tuple[PublisherApiKey, str]:
        """
        Create a new API key for a publisher.
        Returns the database model and the RAW KEY (which must be shown to the user once).
        """
        is_sandbox = getattr(schema, "is_sandbox", False)
        rate_limit = getattr(schema, "rate_limit_per_minute", 60)
        daily_quota = getattr(schema, "daily_quota", 10000)

        raw_key, key_prefix, key_hash = ApiKeyService.generate_key_pair(is_sandbox=is_sandbox)
        
        db_key = PublisherApiKey(
            publisher_id=publisher_id,
            name=schema.name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            is_sandbox=is_sandbox,
            rate_limit_per_minute=rate_limit,
            daily_quota=daily_quota,
            revoked_at=None,
        )
        db.add(db_key)
        await db.commit()
        await db.refresh(db_key)
        
        return db_key, raw_key

    @staticmethod
    async def list_api_keys(
        db: AsyncSession, publisher_id: int
    ) -> list[PublisherApiKey]:
        """List all active and revoked API keys for a publisher."""
        stmt = select(PublisherApiKey).where(PublisherApiKey.publisher_id == publisher_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def revoke_api_key(
        db: AsyncSession, publisher_id: int, key_id: int
    ) -> bool:
        """Revoke a specific API key."""
        stmt = select(PublisherApiKey).where(
            PublisherApiKey.id == key_id,
            PublisherApiKey.publisher_id == publisher_id
        )
        result = await db.execute(stmt)
        db_key = result.scalar_one_or_none()
        
        if not db_key or db_key.revoked_at is not None:
            return False
            
        db_key.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        return True

    @staticmethod
    async def get_key_by_prefix(db: AsyncSession, prefix: str) -> PublisherApiKey | None:
        """Retrieve an API key record by its prefix."""
        stmt = select(PublisherApiKey).where(
            PublisherApiKey.key_prefix == prefix,
            PublisherApiKey.revoked_at.is_(None)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
