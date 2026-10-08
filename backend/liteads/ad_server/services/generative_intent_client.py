from typing import Optional, Any
import httpx

from liteads.common.config import get_settings
from liteads.common.logger import get_logger
from liteads.schemas.intent import IntentResult
from liteads.schemas.chat import ChatSession

logger = get_logger(__name__)

class GenerativeProviderError(Exception):
    """Base exception for generative provider errors."""
    pass

class GenerativeUnavailableError(GenerativeProviderError):
    """Raised when the provider is down, times out, or network fails."""
    pass

class GenerativeMalformedResponseError(GenerativeProviderError):
    """Raised when the provider returns invalid JSON or violates schema."""
    pass


class GenerativeIntentClient:
    """
    Client for conversational intent extraction via OpenAI API.
    
    This abstracts away the LLM logic from the backend service.
    """
    
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.api_key = self.settings.openai.api_key
        self.base_url = "https://api.openai.com/v1"
        self.timeout = 15.0
        self._client = client

    def _build_system_prompt(self) -> str:
        return """You are a campaign builder assistant for RivinityAds.
Your ONLY responsibility is to:
1. Understand the user's message.
2. Determine ONE of the allowed intents:
   CREATE_CAMPAIGN, SET_BUDGET, SET_GEO, SET_SECTOR, REQUEST_PAYMENT, LAUNCH_CAMPAIGN, UNKNOWN.
3. Extract available slots from the message into the slots object.
4. Generate a short conversational reply.

DO NOT execute tools.
DO NOT authorize users or manipulate wallet balances.
DO NOT determine advertiser ownership.
Return UNKNOWN if the user's request does not map safely to the supported intents."""

    async def extract_intent(self, message: str, session: ChatSession) -> IntentResult:
        """
        Extract structured intent and slots from the user message given context.
        """
        if not self.api_key:
            raise GenerativeUnavailableError("OpenAI API key is not configured")

        url = f"{self.base_url}/chat/completions"
        
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        
        # Add bounded history (last 10 messages)
        for msg in session.history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
            
        messages.append({"role": "user", "content": message})
        
        # Explicit Strict JSON Schema for OpenAI
        # We manually map IntentResult to ensure strict compatibility.
        schema = {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": [
                        "CREATE_CAMPAIGN", "SET_BUDGET", "SET_GEO", 
                        "SET_SECTOR", "REQUEST_PAYMENT", "LAUNCH_CAMPAIGN", "UNKNOWN"
                    ]
                },
                "slots": {
                    "type": "object",
                    "properties": {
                        "name": {"type": ["string", "null"]},
                        "budget_daily": {"type": ["number", "null"]},
                        "budget_total": {"type": ["number", "null"]},
                        "country": {"type": ["string", "null"]},
                        "state": {"type": ["string", "null"]},
                        "city": {"type": ["string", "null"]},
                        "sector": {"type": ["string", "null"]},
                        "is_exclude": {"type": ["boolean", "null"]},
                        "amount": {"type": ["number", "null"]}
                    },
                    "required": [
                        "name", "budget_daily", "budget_total", "country",
                        "state", "city", "sector", "is_exclude", "amount"
                    ],
                    "additionalProperties": False
                },
                "confidence": {"type": ["number", "null"]},
                "reply": {"type": ["string", "null"]}
            },
            "required": ["intent", "slots", "confidence", "reply"],
            "additionalProperties": False
        }
        
        payload = {
            "model": "gpt-4o-mini",
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "intent_result",
                    "strict": True,
                    "schema": schema
                }
            },
            "temperature": 0.0
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.TimeoutException as e:
            logger.error("Generative AI request timed out", error=str(e))
            raise GenerativeUnavailableError("Generative API request timed out") from e
        except httpx.HTTPStatusError as e:
            logger.error("Generative AI returned HTTP error", status_code=e.response.status_code, text=e.response.text)
            raise GenerativeUnavailableError(f"Generative API returned status {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("Generative AI request failed", error=str(e))
            raise GenerativeUnavailableError(f"Generative API request failed: {e}") from e
        finally:
            if self._client is None:
                await client.aclose()

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as e:
            logger.error("Generative AI response is not valid format", text=response.text)
            raise GenerativeMalformedResponseError("Invalid response format from Generative API") from e

        try:
            return IntentResult.model_validate_json(content)
        except Exception as e:
            logger.error("Generative AI intent response malformed", error=str(e), content=content)
            raise GenerativeMalformedResponseError(f"Malformed intent response: {e}") from e
