from typing import Any, Optional

import httpx

from liteads.common.config import get_settings
from liteads.common.logger import get_logger
from liteads.schemas.chat import ChatSession
from liteads.schemas.intent import IntentResult

logger = get_logger(__name__)


class GenerativeProviderError(Exception):
    """Base exception for generative provider errors."""


class GenerativeUnavailableError(GenerativeProviderError):
    """Raised when the provider is down, times out, or network fails."""


class GenerativeMalformedResponseError(GenerativeProviderError):
    """Raised when the provider returns invalid JSON or violates schema."""


class GenerativeIntentClient:
    """Extract campaign intent through OpenAI or Google Gemini."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.provider = self.settings.llm_provider
        if self.provider == "gemini":
            self.api_key = self.settings.gemini.api_key
            self.base_url = "https://generativelanguage.googleapis.com/v1beta"
            self.model = self.settings.gemini.model
        elif self.provider == "groq":
            self.api_key = self.settings.groq.api_key if hasattr(self.settings, "groq") else ""
            self.base_url = "https://api.groq.com/openai/v1"
            self.model = self.settings.groq.model if hasattr(self.settings, "groq") else "openai/gpt-oss-120b"
        else:
            self.api_key = self.settings.openai.api_key
            self.base_url = "https://api.openai.com/v1"
            self.model = "gpt-4o-mini"
        self.timeout = 15.0
        self._client = client

    def _build_system_prompt(self) -> str:
        return """You are a campaign builder assistant for RivinityAds.
Understand the user's message and guide them through the advertising hierarchy:
Campaign -> Ad Set -> Ads (Creatives).

Return only valid JSON with this shape:
{"intent":"UNKNOWN","slots":{"name":null,"campaign_id":null,"ad_set_id":null,"ad_set_name":null,
"budget_daily":null,"budget_total":null,"bid_amount":null,"bid_type":null,
"country":null,"state":null,"city":null,"sector":null,"is_exclude":null,"amount":null,
"title":null,"landing_url":null,"image_url":null,"video_url":null,"creative_type":null,"width":null,"height":null},
"confidence":null,"reply":null}

Allowed intents:
- CREATE_CAMPAIGN: create a new campaign
- SELECT_CAMPAIGN: select or switch to an existing campaign by ID
- UPDATE_CAMPAIGN: update campaign name or global budget
- CREATE_AD_SET: create a new ad set under current campaign
- SELECT_AD_SET: switch to or select an ad set by ID or name
- UPDATE_AD_SET: update an ad set's sub-budget, bid, or schedule
- SET_BUDGET: update daily or total budget for campaign or active ad set
- SET_GEO: set geo targeting for the active ad set
- SET_SECTOR: set sector targeting for the active ad set
- CREATE_AD: create an ad / creative for the active ad set
- REVIEW_CAMPAIGN: review campaign structure, ad sets, ads, and launch prerequisites
- REQUEST_PAYMENT: add funds / top up advertiser wallet
- LAUNCH_CAMPAIGN: activate the campaign
- UNKNOWN: fallback when request cannot be safely mapped

Do not execute tools, authorize users, or manipulate wallet balances.
Return UNKNOWN when the request does not safely map to an allowed intent."""

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": [
                    "CREATE_CAMPAIGN", "SELECT_CAMPAIGN", "UPDATE_CAMPAIGN",
                    "CREATE_AD_SET", "SELECT_AD_SET", "UPDATE_AD_SET",
                    "SET_BUDGET", "SET_GEO", "SET_SECTOR",
                    "CREATE_AD", "REVIEW_CAMPAIGN",
                    "REQUEST_PAYMENT", "LAUNCH_CAMPAIGN", "UNKNOWN"
                ]},
                "slots": {"type": "object", "properties": {
                    "name": {"type": ["string", "null"]},
                    "campaign_id": {"type": ["integer", "null"]},
                    "ad_set_id": {"type": ["integer", "null"]},
                    "ad_set_name": {"type": ["string", "null"]},
                    "budget_daily": {"type": ["number", "null"]},
                    "budget_total": {"type": ["number", "null"]},
                    "bid_amount": {"type": ["number", "null"]},
                    "bid_type": {"type": ["integer", "null"]},
                    "country": {"type": ["string", "null"]},
                    "state": {"type": ["string", "null"]},
                    "city": {"type": ["string", "null"]},
                    "sector": {"type": ["string", "null"]},
                    "is_exclude": {"type": ["boolean", "null"]},
                    "amount": {"type": ["number", "null"]},
                    "title": {"type": ["string", "null"]},
                    "landing_url": {"type": ["string", "null"]},
                    "image_url": {"type": ["string", "null"]},
                    "video_url": {"type": ["string", "null"]},
                    "creative_type": {"type": ["integer", "null"]},
                    "width": {"type": ["integer", "null"]},
                    "height": {"type": ["integer", "null"]},
                }},
                "confidence": {"type": ["number", "null"]},
                "reply": {"type": ["string", "null"]},
            },
            "required": ["intent", "slots", "confidence", "reply"],
        }

    async def extract_intent(self, message: str, session: ChatSession) -> IntentResult:
        if not self.api_key or self.api_key.startswith("<PASTE_"):
            raise GenerativeUnavailableError(f"{self.provider} API key is not configured")

        history = [{"role": msg.role, "content": msg.content}
                   for msg in session.history[-10:]]
        history.append({"role": "user", "content": message})

        if self.provider == "gemini":
            url = f"{self.base_url}/models/{self.model}:generateContent"
            payload = {
                "systemInstruction": {"parts": [{"text": self._build_system_prompt()}]},
                "contents": [
                    {
                        "role": "model" if item["role"] == "assistant" else "user",
                        "parts": [{"text": item["content"]}],
                    }
                    for item in history
                ],
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "application/json",
                },
            }
            headers = {"Content-Type": "application/json"}
            params = {"key": self.api_key}
        else:
            url = f"{self.base_url}/chat/completions"
            messages = [{"role": "system", "content": self._build_system_prompt()}]
            messages.extend(history)
            if self.provider == "groq":
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                }
            else:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "intent_result",
                            "strict": True,
                            "schema": self._schema(),
                        },
                    },
                    "temperature": 0,
                }
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            params = {}

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(url, json=payload, headers=headers, params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error("Generative AI request timed out", provider=self.provider)
            raise GenerativeUnavailableError("Generative API request timed out") from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Generative AI returned HTTP error",
                provider=self.provider,
                status_code=exc.response.status_code,
                text=exc.response.text,
            )
            raise GenerativeUnavailableError(
                f"Generative API returned status {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Generative AI request failed", provider=self.provider, error=str(exc))
            raise GenerativeUnavailableError(f"Generative API request failed: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

        try:
            data = response.json()
            if self.provider == "gemini":
                content = data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.error("Generative AI response is not valid format", provider=self.provider)
            raise GenerativeMalformedResponseError(
                "Invalid response format from Generative API"
            ) from exc

        try:
            result = IntentResult.model_validate_json(content)
            if not result.reply:
                result.reply = {
                    "CREATE_CAMPAIGN": "I can help create that campaign. Please provide the campaign name and budget.",
                    "SET_BUDGET": "I can update the campaign budget. Please tell me the daily or total budget.",
                    "SET_GEO": "I can update the campaign location targeting. Please provide the country, state, or city.",
                    "SET_SECTOR": "I can update the campaign sector targeting. Please tell me the sector.",
                    "REQUEST_PAYMENT": "I can help add funds to your campaign wallet. Please provide the amount.",
                    "LAUNCH_CAMPAIGN": "I can help launch the campaign after its required settings are complete.",
                    "UNKNOWN": "I can help create and configure your advertising campaign. Tell me what you would like to do.",
                }[result.intent]
            return result
        except Exception as exc:
            logger.error("Generative AI intent response malformed", error=str(exc))
            raise GenerativeMalformedResponseError(
                f"Malformed intent response: {exc}"
            ) from exc
