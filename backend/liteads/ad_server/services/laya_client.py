"""
Laya Decision Client.

Communicates with the Laya sidecar to retrieve optimization decisions.
"""

from typing import Any, Literal
from dataclasses import dataclass

import httpx

from liteads.common.config import get_settings
from liteads.common.logger import get_logger
from liteads.common.exceptions import LiteAdsError

logger = get_logger(__name__)


class LayaClientError(LiteAdsError):
    """Base exception for Laya client errors."""
    pass


class LayaUnavailableError(LayaClientError):
    """Laya service is unreachable or timed out."""
    pass


class LayaMalformedResponseError(LayaClientError):
    """Laya returned an invalid or unparseable response."""
    pass


@dataclass
class LayaDecisionResult:
    choice: Literal[
        "increase_budget_20",
        "decrease_budget_20",
        "shift_to_new_geo",
        "shift_to_new_sector",
        "improve_creative",
        "no_change",
    ]
    confidence: float
    raw_response: dict[str, Any]
    target_payload: dict[str, Any] | None = None


class LayaDecisionClient:
    """Client for the Laya /v1/systemone API."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.settings = get_settings()
        self.base_url = self.settings.laya.base_url.rstrip("/")
        self.timeout = self.settings.laya.timeout_ms / 1000.0
        self._client = client

    async def decide(self, state: str) -> LayaDecisionResult:
        """
        Ask Laya for a campaign optimization decision.
        
        Args:
            state: The context/state string describing the campaign.
            
        Returns:
            LayaDecisionResult containing the choice and confidence.
            
        Raises:
            LayaUnavailableError: If the Laya service is unreachable.
            LayaMalformedResponseError: If the response is invalid.
        """
        payload = {
            "state": state,
            "questions": {
                "opt_decision": {
                    "type": "choice",
                    "instructions": "Determine the best optimization action for this campaign.",
                    "criteria": {
                        "increase_budget_20": "Increase daily budget by 20%",
                        "decrease_budget_20": "Decrease daily budget by 20%",
                        "shift_to_new_geo": "Shift targeting to a new geographic region",
                        "shift_to_new_sector": "Shift targeting to a new sector",
                        "improve_creative": "Recommend creative improvement",
                        "no_change": "No changes needed"
                    }
                }
            }
        }
        
        url = f"{self.base_url}/v1/systemone"
        
        try:
            client = self._client or httpx.AsyncClient(timeout=self.timeout)
            try:
                response = await client.post(url, json=payload)
            finally:
                if self._client is None:
                    await client.aclose()
        except httpx.TimeoutException as e:
            logger.error("Laya request timed out", error=str(e), url=url)
            raise LayaUnavailableError("Laya request timed out") from e
        except httpx.RequestError as e:
            logger.error("Laya request failed", error=str(e), url=url)
            raise LayaUnavailableError(f"Laya request failed: {e}") from e

        if response.status_code != 200:
            logger.error("Laya returned non-200 status", status_code=response.status_code, text=response.text)
            raise LayaUnavailableError(f"Laya returned status {response.status_code}")

        try:
            data = response.json()
        except ValueError as e:
            logger.error("Laya response is not valid JSON", text=response.text)
            raise LayaMalformedResponseError("Invalid JSON from Laya") from e

        # Extract choice and confidence safely
        try:
            answers = data.get("answers", {})
            opt_decision = answers.get("opt_decision", {})
            choice = opt_decision.get("choice")
            confidence = opt_decision.get("confidence")

            if choice is None:
                raise ValueError("Missing 'choice' in opt_decision")
            if confidence is None:
                raise ValueError("Missing 'confidence' in opt_decision")
                
            confidence = float(confidence)
            
            valid_choices = [
                "increase_budget_20",
                "decrease_budget_20",
                "shift_to_new_geo",
                "shift_to_new_sector",
                "improve_creative",
                "no_change",
            ]
            if choice not in valid_choices:
                raise ValueError(f"Invalid choice '{choice}' returned by Laya")
                
        except (ValueError, TypeError, AttributeError) as e:
            logger.error("Laya response malformed", error=str(e), data=data)
            raise LayaMalformedResponseError(f"Malformed Laya response: {e}") from e

        return LayaDecisionResult(
            choice=choice, # type: ignore
            confidence=confidence,
            raw_response=data
        )
