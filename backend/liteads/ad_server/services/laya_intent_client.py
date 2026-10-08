from typing import Optional
import httpx

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
    
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.base_url = self.settings.laya.base_url.rstrip("/")
        self.timeout = self.settings.laya.timeout_ms / 1000.0
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
        # ARCHITECTURAL BLOCKER:
        # The current local Laya container based on NandhaKishorM/laya only exposes
        # deterministic classification via /v1/systemone. There is no documented
        # structured generative endpoint available yet for Phase 3E-3.
        # We establish the interface but will fail safely here until it's supported.
        url = f"{self.base_url}/v1/intent"
        
        # Serialize history for context
        history_context = [{"role": msg.role, "content": msg.content} for msg in session.history[-10:]]
        
        payload = {
            "message": message,
            "context": {
                "current_campaign_id": session.current_campaign_id,
                "history": history_context
            }
        }
        
        try:
            client = self._client or httpx.AsyncClient(timeout=self.timeout)
            try:
                response = await client.post(url, json=payload)
            finally:
                if self._client is None:
                    await client.aclose()
        except httpx.TimeoutException as e:
            logger.error("Laya intent request timed out", error=str(e), url=url)
            raise LayaUnavailableError("Laya request timed out") from e
        except httpx.RequestError as e:
            logger.error("Laya intent request failed", error=str(e), url=url)
            raise LayaUnavailableError(f"Laya request failed: {e}") from e

        if response.status_code == 404:
            raise LayaUnavailableError("Structured intent endpoint is not supported by the current Laya instance.")
            
        if response.status_code != 200:
            logger.error("Laya returned non-200 status", status_code=response.status_code, text=response.text)
            raise LayaUnavailableError(f"Laya returned status {response.status_code}")

        try:
            data = response.json()
        except ValueError as e:
            logger.error("Laya response is not valid JSON", text=response.text)
            raise LayaMalformedResponseError("Invalid JSON from Laya") from e

        try:
            return IntentResult.model_validate(data)
        except Exception as e:
            logger.error("Laya intent response malformed", error=str(e), data=data)
            raise LayaMalformedResponseError(f"Malformed Laya intent response: {e}") from e
