"""Snapshot caching service for published website pages and routing resolution."""

from __future__ import annotations

import time
from typing import Any

from liteads.common.cache import redis_client
from liteads.common.logger import get_logger
from liteads.common.utils import json_dumps, json_loads

logger = get_logger(__name__)

# Lightweight in-memory fallback for local environments without Redis
_IN_MEMORY_CACHE: dict[str, tuple[str, float]] = {}
_MAX_IN_MEMORY_ENTRIES = 500


def _clean_in_memory_cache() -> None:
    """Evict expired items from in-memory cache if size exceeds threshold."""
    now = time.time()
    if len(_IN_MEMORY_CACHE) > _MAX_IN_MEMORY_ENTRIES:
        expired = [k for k, (_, exp) in _IN_MEMORY_CACHE.items() if exp <= now]
        for k in expired:
            _IN_MEMORY_CACHE.pop(k, None)
        if len(_IN_MEMORY_CACHE) > _MAX_IN_MEMORY_ENTRIES:
            # Drop oldest 100 entries
            for k in list(_IN_MEMORY_CACHE.keys())[:100]:
                _IN_MEMORY_CACHE.pop(k, None)


class WebsiteCacheService:
    """
    Manages caching for published website HTML and site identity resolution.

    Guarantees:
    - Never caches draft or preview responses.
    - Cache keys strictly bind project ID and publication version ID.
    - Invalidation on publish, unpublish, rollback, archive, and takedown.
    - Graceful fallback when Redis is offline.
    """

    @staticmethod
    def _html_key(project_id: int, version_id: int, page_slug: str) -> str:
        clean_slug = page_slug.strip().strip("/").lower()
        if not clean_slug:
            clean_slug = "__home__"
        return f"site:html:{project_id}:{version_id}:{clean_slug}"

    @staticmethod
    def _info_key(slug: str) -> str:
        clean_slug = slug.strip().lower()
        return f"site:info:{clean_slug}"

    @classmethod
    async def get_rendered_page(
        cls, project_id: int, version_id: int, page_slug: str
    ) -> str | None:
        """Retrieve pre-rendered HTML for a published snapshot page."""
        key = cls._html_key(project_id, version_id, page_slug)

        # 1. Try Redis
        if redis_client.connected:
            try:
                cached = await redis_client.get(key)
                if cached:
                    return cached
            except Exception as e:
                logger.debug("Redis cache get error", key=key, error=str(e))

        # 2. Check in-memory fallback
        now = time.time()
        if key in _IN_MEMORY_CACHE:
            html, exp = _IN_MEMORY_CACHE[key]
            if exp > now:
                return html
            _IN_MEMORY_CACHE.pop(key, None)

        return None

    @classmethod
    async def set_rendered_page(
        cls,
        project_id: int,
        version_id: int,
        page_slug: str,
        html_content: str,
        ttl_seconds: int = 3600,
    ) -> None:
        """Store pre-rendered HTML for a published snapshot page."""
        key = cls._html_key(project_id, version_id, page_slug)

        # 1. Store in Redis
        if redis_client.connected:
            try:
                await redis_client.set(key, html_content, ttl=ttl_seconds)
            except Exception as e:
                logger.debug("Redis cache set error", key=key, error=str(e))

        # 2. Store in memory fallback
        _clean_in_memory_cache()
        _IN_MEMORY_CACHE[key] = (html_content, time.time() + ttl_seconds)

    @classmethod
    async def get_site_info(cls, slug: str) -> dict[str, Any] | None:
        """Retrieve cached project routing resolution metadata."""
        key = cls._info_key(slug)

        if redis_client.connected:
            try:
                cached = await redis_client.get(key)
                if cached:
                    return json_loads(cached)
            except Exception as e:
                logger.debug("Redis site_info get error", key=key, error=str(e))

        now = time.time()
        if key in _IN_MEMORY_CACHE:
            val, exp = _IN_MEMORY_CACHE[key]
            if exp > now:
                try:
                    return json_loads(val)
                except Exception:
                    pass
            _IN_MEMORY_CACHE.pop(key, None)

        return None

    @classmethod
    async def set_site_info(
        cls,
        slug: str,
        info: dict[str, Any],
        ttl_seconds: int = 60,
    ) -> None:
        """Cache project routing resolution metadata for fast routing."""
        key = cls._info_key(slug)
        val = json_dumps(info)

        if redis_client.connected:
            try:
                await redis_client.set(key, val, ttl=ttl_seconds)
            except Exception as e:
                logger.debug("Redis site_info set error", key=key, error=str(e))

        _clean_in_memory_cache()
        _IN_MEMORY_CACHE[key] = (val, time.time() + ttl_seconds)

    @classmethod
    async def invalidate_project(
        cls, project_id: int, slug: str | None = None
    ) -> None:
        """
        Invalidate all cached HTML pages and routing entries for a project.

        Called on: publish, unpublish, rollback, archive, and takedown/suspension.
        """
        # 1. Invalidate site info
        if slug:
            info_key = cls._info_key(slug)
            _IN_MEMORY_CACHE.pop(info_key, None)
            if redis_client.connected:
                try:
                    await redis_client.delete(info_key)
                except Exception as e:
                    logger.debug("Failed deleting info key", key=info_key, error=str(e))

        # 2. Evict in-memory cache matching project_id
        prefix = f"site:html:{project_id}:"
        in_mem_keys = [k for k in _IN_MEMORY_CACHE if k.startswith(prefix)]
        for k in in_mem_keys:
            _IN_MEMORY_CACHE.pop(k, None)

        # 3. Evict Redis keys matching project_id pattern
        if redis_client.connected:
            try:
                pattern = f"site:html:{project_id}:*"
                cursor = 0
                while True:
                    cursor, keys = await redis_client.client.scan(cursor=cursor, match=pattern, count=100)
                    if keys:
                        await redis_client.client.delete(*keys)
                    if cursor == 0:
                        break
            except Exception as e:
                logger.debug("Failed invalidating Redis pattern", project_id=project_id, error=str(e))


website_cache = WebsiteCacheService
