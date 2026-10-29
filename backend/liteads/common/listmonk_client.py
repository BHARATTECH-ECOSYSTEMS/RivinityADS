"""Listmonk API client for transactional email and subscriber management (Phase 6.1B & 6.1C).

Provides an async client to interact with the Listmonk v4.1.0 API:
- Transactional delivery (`POST /api/tx`) using external-recipient mode (`subscriber_mode='external'`)
- Subscriber lifecycle management (`GET/POST/PUT/DELETE /api/subscribers`)
- Bounded timeouts, HTTP Basic Authentication, safe error classification, and credential redaction.

Credentials, tokens, and sensitive headers are strictly redacted from logs and exceptions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from liteads.common.email_error import EmailDeliveryError

logger = logging.getLogger(__name__)


# ── Exception Hierarchy ───────────────────────────────────────────────────────

class ListmonkError(EmailDeliveryError):
    """Base exception for all Listmonk client operations."""
    pass


class ListmonkConfigError(ListmonkError):
    """Raised when Listmonk is misconfigured or a required template/list ID is missing."""
    pass


class ListmonkAuthError(ListmonkError):
    """Raised on 401 Unauthorized or 403 Forbidden responses (permanent error)."""
    pass


class ListmonkNotFoundError(ListmonkError):
    """Raised on 404 Not Found responses (e.g. template or subscriber ID does not exist)."""
    pass


class ListmonkRateLimitError(ListmonkError):
    """Raised on 429 Too Many Requests responses (transient error, retryable)."""
    pass


class ListmonkServerError(ListmonkError):
    """Raised on 5xx server responses or transport-level network errors (transient, retryable)."""
    pass


class ListmonkConnectTimeoutError(ListmonkError):
    """Raised when establishing the TCP connection to Listmonk times out BEFORE sending the request.

    Safe to retry because the remote server never received the request payload.
    """
    pass


class ListmonkAmbiguousTimeoutError(ListmonkError):
    """Raised when read/response times out AFTER the request was transmitted.

    CRITICAL: The remote server may have already accepted and queued the email or subscriber mutation.
    Blind retry risks duplicate actions!
    """
    pass


class ListmonkResponseError(ListmonkError):
    """Raised when Listmonk returns an invalid or malformed response payload."""
    pass


# ── Helpers ──────────────────────────────────────────────────────────────────

def mask_email(email: str) -> str:
    """Mask email address for privacy-safe logging (e.g., 'u***@example.com')."""
    if not email or "@" not in email:
        return "[REDACTED_EMAIL]"
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        masked_local = "*"
    else:
        masked_local = f"{local[0]}***"
    return f"{masked_local}@{domain}"


def sanitize_message(msg: Any, max_len: int = 300) -> str:
    """Sanitize message by stripping tokens, secrets, and auth credentials."""
    raw = str(msg) if msg is not None else ""
    cleaned = re.sub(
        r"(token|secret|password|auth|authorization|key)=['\"]?[^'\";\s]+['\"]?",
        r"\1=[REDACTED]",
        raw,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"(https?://\S+?\?)\S+", r"\1[PARAMS_REDACTED]", cleaned)
    return cleaned[:max_len]


# ── Client Implementation ─────────────────────────────────────────────────────

class ListmonkClient:
    """Async client for Listmonk transactional API and subscriber management."""

    def __init__(
        self,
        base_url: str,
        username: str,
        token: str,
        request_timeout: float = 10.0,
        connect_timeout: float = 3.0,
        client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.token = token
        self.request_timeout = request_timeout
        self.connect_timeout = connect_timeout
        self._external_client = client
        self._transport = transport

    def _create_client(self) -> httpx.AsyncClient:
        """Create a configured httpx.AsyncClient instance."""
        if self._external_client is not None:
            return self._external_client

        timeout = httpx.Timeout(
            timeout=self.request_timeout,
            connect=self.connect_timeout,
        )
        auth = httpx.BasicAuth(username=self.username, password=self.token)
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=auth,
            timeout=timeout,
            transport=self._transport,
            headers={"User-Agent": "RivinityAds-AdServer/1.0"},
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        """Execute an HTTP request with bounded timeouts and classified error mapping."""
        async def _do(http_client: httpx.AsyncClient) -> httpx.Response:
            try:
                response = await http_client.request(
                    method=method,
                    url=endpoint,
                    json=json_data,
                    params=params,
                )
            except httpx.ConnectTimeout as exc:
                logger.warning(
                    "Connection timeout connecting to Listmonk before dispatch",
                    extra={"endpoint": endpoint, "method": method},
                )
                raise ListmonkConnectTimeoutError(
                    f"Connection timeout connecting to Listmonk: {exc}"
                ) from exc
            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                logger.error(
                    "Ambiguous timeout during Listmonk request; outcome uncertain",
                    extra={"endpoint": endpoint, "method": method},
                )
                raise ListmonkAmbiguousTimeoutError(
                    f"Ambiguous timeout waiting for Listmonk response: {exc}"
                ) from exc
            except httpx.ConnectError as exc:
                logger.warning(
                    "Network connection error to Listmonk",
                    extra={"endpoint": endpoint, "method": method},
                )
                raise ListmonkServerError(f"Failed to connect to Listmonk: {exc}" ) from exc
            except httpx.RequestError as exc:
                logger.error(
                    "Network error during Listmonk request",
                    extra={"endpoint": endpoint, "method": method},
                )
                raise ListmonkServerError(f"Network error communicating with Listmonk: {exc}") from exc

            status_code = response.status_code
            if 200 <= status_code < 300:
                return response

            sanitized_body = sanitize_message(response.text)

            if status_code in (401, 403):
                logger.error("Listmonk authentication/authorization failed", extra={"status_code": status_code})
                raise ListmonkAuthError(f"Listmonk authentication failed (HTTP {status_code})")

            if status_code == 404:
                logger.error("Listmonk resource not found", extra={"status_code": 404, "endpoint": endpoint})
                raise ListmonkNotFoundError(f"Listmonk resource not found (HTTP 404): {sanitized_body}")

            if status_code == 429:
                logger.warning("Listmonk rate limit exceeded (HTTP 429)")
                raise ListmonkRateLimitError("Listmonk rate limit exceeded (HTTP 429)")

            if 500 <= status_code < 600:
                logger.error("Listmonk remote server error", extra={"status_code": status_code})
                raise ListmonkServerError(f"Listmonk server error (HTTP {status_code}): {sanitized_body}")

            raise ListmonkError(f"Listmonk rejected request (HTTP {status_code}): {sanitized_body}")

        if self._external_client is not None:
            return await _do(self._external_client)

        async with self._create_client() as client:
            return await _do(client)

    # ── Transactional Email API (Phase 6.1B) ──────────────────────────────────

    async def send_transactional(
        self,
        recipient_email: str,
        template_id: int,
        data: Optional[dict[str, Any]] = None,
        subscriber_mode: str = "external",
        headers: Optional[list[dict[str, str]]] = None,
    ) -> bool:
        """Send a transactional email via Listmonk POST /api/tx.

        Enforces subscriber_mode='external' so transactional recipients are never
        enrolled as marketing subscribers.
        """
        if not recipient_email or "@" not in recipient_email:
            raise ListmonkError(f"Invalid recipient email syntax: {mask_email(recipient_email)}")

        if not isinstance(template_id, int) or template_id <= 0:
            raise ListmonkConfigError(f"Invalid template_id: {template_id}. Must be a positive integer.")

        payload: dict[str, Any] = {
            "subscriber_email": recipient_email,
            "subscriber_mode": subscriber_mode,
            "template_id": template_id,
            "data": data or {},
        }
        if headers:
            payload["headers"] = headers

        masked = mask_email(recipient_email)
        response = await self._request("POST", "/api/tx", json_data=payload)

        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response from Listmonk 200 OK") from exc

        if isinstance(res_json, dict) and res_json.get("data") is True:
            logger.info(
                "Transactional email accepted by Listmonk",
                extra={
                    "template_id": template_id,
                    "recipient": masked,
                    "subscriber_mode": subscriber_mode,
                },
            )
            return True
        else:
            raise ListmonkResponseError(
                f"Unexpected response structure from Listmonk: {sanitize_message(res_json)}"
            )

    # ── Subscriber Management API (Phase 6.1C) ────────────────────────────────

    async def get_subscriber_by_email(self, email: str) -> Optional[dict[str, Any]]:
        """Query Listmonk for an existing subscriber by email address.

        Uses GET /api/subscribers?query=subscribers.email='...'
        """
        normalized_email = email.strip().lower()
        query_str = f"subscribers.email = '{normalized_email}'"
        response = await self._request("GET", "/api/subscribers", params={"query": query_str})

        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response from Listmonk subscribers query") from exc

        data = res_json.get("data")
        if isinstance(data, dict):
            results = data.get("results", [])
            if results and isinstance(results, list):
                return results[0]
        return None

    async def create_subscriber(
        self,
        email: str,
        name: str = "",
        list_ids: Optional[list[int]] = None,
        attribs: Optional[dict[str, Any]] = None,
        preconfirm: bool = True,
    ) -> dict[str, Any]:
        """Create a new subscriber in Listmonk (POST /api/subscribers)."""
        payload: dict[str, Any] = {
            "email": email.strip().lower(),
            "name": name.strip(),
            "status": "enabled",
            "lists": list_ids or [],
            "attribs": attribs or {},
            "preconfirm_subscriptions": preconfirm,
        }

        response = await self._request("POST", "/api/subscribers", json_data=payload)
        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response creating subscriber") from exc

        sub_data = res_json.get("data")
        if isinstance(sub_data, dict):
            return sub_data
        raise ListmonkResponseError("Unexpected response creating subscriber")

    async def update_subscriber(
        self,
        subscriber_id: int,
        email: Optional[str] = None,
        name: Optional[str] = None,
        list_ids: Optional[list[int]] = None,
        status: str = "enabled",
        attribs: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Update an existing subscriber in Listmonk (PUT /api/subscribers/{id})."""
        payload: dict[str, Any] = {
            "status": status,
        }
        if email:
            payload["email"] = email.strip().lower()
        if name is not None:
            payload["name"] = name.strip()
        if list_ids is not None:
            payload["lists"] = list_ids
        if attribs is not None:
            payload["attribs"] = attribs

        response = await self._request("PUT", f"/api/subscribers/{subscriber_id}", json_data=payload)
        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response updating subscriber") from exc

        sub_data = res_json.get("data")
        if isinstance(sub_data, dict):
            return sub_data
        raise ListmonkResponseError("Unexpected response updating subscriber")

    async def blocklist_subscriber(self, subscriber_id: int) -> bool:
        """Globally opt-out and blocklist a subscriber (PUT /api/subscribers/{id})."""
        await self.update_subscriber(
            subscriber_id=subscriber_id,
            status="blocklisted",
            list_ids=[],
        )
        return True

    async def delete_subscriber(self, subscriber_id: int) -> bool:
        """Delete a subscriber from Listmonk (DELETE /api/subscribers/{id})."""
        await self._request("DELETE", f"/api/subscribers/{subscriber_id}")
        return True

    async def get_subscribers(
        self,
        query: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List subscribers with pagination and query filtering."""
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if query:
            params["query"] = query

        response = await self._request("GET", "/api/subscribers", params=params)
        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response listing subscribers") from exc

        data = res_json.get("data", {})
        if isinstance(data, dict):
            return data.get("results", [])
        return []

    # ── List Management API (Phase 6.1D) ──────────────────────────────────────

    async def create_list(
        self,
        name: str,
        list_type: str = "private",
        optin: str = "single",
        tags: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Create a new mailing list in Listmonk (POST /api/lists)."""
        payload: dict[str, Any] = {
            "name": name.strip(),
            "type": list_type,
            "optin": optin,
            "tags": tags or [],
        }
        response = await self._request("POST", "/api/lists", json_data=payload)
        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response creating list") from exc

        data = res_json.get("data")
        if isinstance(data, dict):
            return data
        raise ListmonkResponseError("Unexpected response creating list")

    async def get_list(self, list_id: int) -> Optional[dict[str, Any]]:
        """Retrieve details for a specific list (GET /api/lists/{id})."""
        try:
            response = await self._request("GET", f"/api/lists/{list_id}")
            res_json = response.json()
            return res_json.get("data")
        except ListmonkNotFoundError:
            return None

    async def get_lists(self, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        """List all lists with pagination (GET /api/lists)."""
        response = await self._request("GET", "/api/lists", params={"page": page, "per_page": per_page})
        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response listing lists") from exc

        data = res_json.get("data", {})
        if isinstance(data, dict):
            return data.get("results", [])
        return []

    async def delete_list(self, list_id: int) -> bool:
        """Delete a list from Listmonk (DELETE /api/lists/{id})."""
        await self._request("DELETE", f"/api/lists/{list_id}")
        return True

    # ── Campaign Management API (Phase 6.1D) ──────────────────────────────────

    async def create_campaign(
        self,
        name: str,
        subject: str,
        list_ids: list[int],
        body: str,
        content_type: str = "html",
        campaign_type: str = "regular",
    ) -> dict[str, Any]:
        """Create a campaign in Listmonk (POST /api/campaigns)."""
        payload: dict[str, Any] = {
            "name": name.strip(),
            "subject": subject.strip(),
            "lists": list_ids,
            "body": body,
            "content_type": content_type,
            "type": campaign_type,
        }
        response = await self._request("POST", "/api/campaigns", json_data=payload)
        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response creating campaign") from exc

        data = res_json.get("data")
        if isinstance(data, dict):
            return data
        raise ListmonkResponseError("Unexpected response creating campaign")

    async def get_campaign(self, campaign_id: int) -> Optional[dict[str, Any]]:
        """Retrieve campaign details (GET /api/campaigns/{id})."""
        try:
            response = await self._request("GET", f"/api/campaigns/{campaign_id}")
            res_json = response.json()
            return res_json.get("data")
        except ListmonkNotFoundError:
            return None

    async def update_campaign(
        self,
        campaign_id: int,
        name: Optional[str] = None,
        subject: Optional[str] = None,
        list_ids: Optional[list[int]] = None,
        body: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """Update an existing campaign in Listmonk (PUT /api/campaigns/{id})."""
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name.strip()
        if subject is not None:
            payload["subject"] = subject.strip()
        if list_ids is not None:
            payload["lists"] = list_ids
        if body is not None:
            payload["body"] = body
        if content_type is not None:
            payload["content_type"] = content_type

        response = await self._request("PUT", f"/api/campaigns/{campaign_id}", json_data=payload)
        try:
            res_json = response.json()
        except Exception as exc:
            raise ListmonkResponseError("Malformed JSON response updating campaign") from exc

        data = res_json.get("data")
        if isinstance(data, dict):
            return data
        raise ListmonkResponseError("Unexpected response updating campaign")

    async def update_campaign_status(self, campaign_id: int, status: str) -> bool:
        """Update campaign status (PUT /api/campaigns/{id}/status) - e.g. 'running', 'paused', 'cancelled'."""
        response = await self._request("PUT", f"/api/campaigns/{campaign_id}/status", json_data={"status": status})
        try:
            res_json = response.json()
            return res_json.get("data") is True
        except Exception:
            return True

    async def test_campaign(self, campaign_id: int, emails: list[str]) -> bool:
        """Send a campaign test preview to specified email addresses (POST /api/campaigns/{id}/test)."""
        response = await self._request("POST", f"/api/campaigns/{campaign_id}/test", json_data={"emails": emails})
        try:
            res_json = response.json()
            return res_json.get("data") is True
        except Exception:
            return True

    async def get_campaign_analytics(self, campaign_id: int) -> dict[str, Any]:
        """Retrieve campaign delivery and interaction analytics (GET /api/campaigns/{id}/analytics)."""
        try:
            response = await self._request("GET", f"/api/campaigns/{campaign_id}/analytics")
            res_json = response.json()
            data = res_json.get("data")
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    async def delete_campaign(self, campaign_id: int) -> bool:
        """Delete a campaign in Listmonk (DELETE /api/campaigns/{id})."""
        await self._request("DELETE", f"/api/campaigns/{campaign_id}")
        return True

    # ── Diagnostics & Health ──────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check Listmonk health endpoint (GET /api/health)."""
        async def _check(http_client: httpx.AsyncClient) -> bool:
            try:
                res = await http_client.get("/api/health")
                return res.status_code == 200
            except Exception:
                return False

        if self._external_client is not None:
            return await _check(self._external_client)

        try:
            async with self._create_client() as client:
                return await _check(client)
        except Exception:
            return False
