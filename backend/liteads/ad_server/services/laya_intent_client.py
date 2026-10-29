import time
from typing import Optional
import httpx

from liteads.ad_server.middleware.metrics import (
    record_laya_duration,
    record_laya_error,
    record_laya_request,
)
from liteads.common.config import get_settings
from liteads.common.logger import get_logger
from liteads.ad_server.services.laya_client import LayaUnavailableError, LayaMalformedResponseError
from liteads.schemas.intent import IntentResult
from liteads.schemas.chat import ChatSession

logger = get_logger(__name__)

class LayaIntentClient:
    """
    Client for conversational intent extraction via Laya.
    
    This abstracts away the LLM logic from the backend service.
    """
    
    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        timeout: Optional[float] = None,
    ):
        self.settings = get_settings()
        self.base_url = self.settings.laya.base_url.rstrip("/")
        default_timeout = self.settings.laya.timeout_ms / 1000.0
        self.timeout = max(0.1, min(float(timeout if timeout is not None else default_timeout), 60.0))
        self._client = client

    async def extract_intent(self, message: str, session: ChatSession) -> IntentResult:
        """
        Extract structured intent and slots from the user message given context.
        
        Args:
            message: The raw natural language message from the user.
            session: The verified ChatSession context (history, campaign ID, etc).
            
        Returns:
            IntentResult structured data payload.
            
        Raises:
            LayaUnavailableError: If Laya is down or doesn't support the endpoint.
            LayaMalformedResponseError: If Laya returns invalid schema data.
        """
        url = f"{self.base_url}/v1/intent"
        
        # Serialize history for context (strictly limit context length)
        history_context = [{"role": msg.role, "content": msg.content} for msg in session.history[-10:]]
        
        payload = {
            "message": message,
            "context": {
                "current_campaign_id": session.current_campaign_id,
                "history": history_context
            }
        }
        
        start_time = time.perf_counter()
        try:
            client = self._client or httpx.AsyncClient(timeout=self.timeout)
            try:
                response = await client.post(url, json=payload)
            finally:
                if self._client is None:
                    await client.aclose()
        except httpx.TimeoutException as e:
            duration = time.perf_counter() - start_time
            record_laya_duration("intent", duration)
            record_laya_request("intent", "timeout")
            record_laya_error("intent", "timeout")
            logger.error("Laya intent request timed out", error=str(e), url=url, timeout=self.timeout)
            raise LayaUnavailableError("Laya request timed out") from e
        except httpx.RequestError as e:
            duration = time.perf_counter() - start_time
            record_laya_duration("intent", duration)
            record_laya_request("intent", "network_error")
            record_laya_error("intent", "network_error")
            logger.error("Laya intent request failed", error=str(e), url=url)
            raise LayaUnavailableError(f"Laya request failed: {e}") from e

        duration = time.perf_counter() - start_time
        record_laya_duration("intent", duration)

        if response.status_code == 404:
            record_laya_request("intent", "unsupported_endpoint")
            record_laya_error("intent", "unsupported_endpoint")
            raise LayaUnavailableError("Structured intent endpoint is not supported by the current Laya instance.")
            
        if response.status_code != 200:
            error_cat = f"http_{response.status_code}"
            record_laya_request("intent", error_cat)
            record_laya_error("intent", error_cat)
            logger.error("Laya returned non-200 status", status_code=response.status_code)
            raise LayaUnavailableError(f"Laya returned status {response.status_code}")

        try:
            data = response.json()
        except ValueError as e:
            record_laya_request("intent", "malformed_response")
            record_laya_error("intent", "malformed_response")
            logger.error("Laya response is not valid JSON")
            raise LayaMalformedResponseError("Invalid JSON from Laya") from e

        try:
            res = IntentResult.model_validate(data)
            record_laya_request("intent", "success")
            return res
        except Exception as e:
            record_laya_request("intent", "malformed_response")
            record_laya_error("intent", "malformed_response")
            logger.error("Laya intent response malformed", error=str(e))
            raise LayaMalformedResponseError(f"Malformed Laya intent response: {e}") from e
