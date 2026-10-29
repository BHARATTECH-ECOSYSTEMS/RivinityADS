"""
Laya Decision Client.

Communicates with the Laya sidecar to retrieve optimization decisions.
"""

import math
import time
from typing import Any, Literal
from dataclasses import dataclass

import httpx

from liteads.ad_server.middleware.metrics import (
    record_laya_duration,
    record_laya_error,
    record_laya_request,
)
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
    """Client for the Laya /v1/systemone API with bounded timeouts and observability."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ):
        self.settings = get_settings()
        self.base_url = self.settings.laya.base_url.rstrip("/")
        default_timeout = self.settings.laya.timeout_ms / 1000.0
        # Enforce bounded timeout (between 0.1s and 60.0s)
        self.timeout = max(0.1, min(float(timeout if timeout is not None else default_timeout), 60.0))
        self._client = client

    async def decide(self, state: str) -> LayaDecisionResult:
        """
        Ask Laya for a campaign optimization decision.
        
        Args:
            state: The context/state string describing the campaign.
            
        Returns:
            LayaDecisionResult containing the choice and confidence.
            
        Raises:
            LayaUnavailableError: If the Laya service is unreachable or times out.
            LayaMalformedResponseError: If the response is invalid JSON or violates schema.
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
            record_laya_duration("decision", duration)
            record_laya_request("decision", "timeout")
            record_laya_error("decision", "timeout")
            logger.error("Laya decision request timed out", error=str(e), url=url, timeout=self.timeout)
            raise LayaUnavailableError("Laya request timed out") from e
        except httpx.RequestError as e:
            duration = time.perf_counter() - start_time
            record_laya_duration("decision", duration)
            record_laya_request("decision", "network_error")
            record_laya_error("decision", "network_error")
            logger.error("Laya decision request failed", error=str(e), url=url)
            raise LayaUnavailableError(f"Laya request failed: {e}") from e

        duration = time.perf_counter() - start_time
        record_laya_duration("decision", duration)

        if response.status_code != 200:
            error_cat = f"http_{response.status_code}"
            record_laya_request("decision", error_cat)
            record_laya_error("decision", error_cat)
            logger.error("Laya decision returned non-200 status", status_code=response.status_code)
            raise LayaUnavailableError(f"Laya returned status {response.status_code}")

        try:
            data = response.json()
        except ValueError as e:
            record_laya_request("decision", "malformed_response")
            record_laya_error("decision", "malformed_response")
            logger.error("Laya decision response is not valid JSON")
            raise LayaMalformedResponseError("Invalid JSON from Laya") from e

        # Extract choice, confidence, and target_payload safely
        try:
            answers = data.get("answers", {})
            opt_decision = answers.get("opt_decision", {})
            choice = opt_decision.get("choice")
            confidence_raw = opt_decision.get("confidence")

            if choice is None:
                raise ValueError("Missing 'choice' in opt_decision")
            if confidence_raw is None:
                raise ValueError("Missing 'confidence' in opt_decision")
                
            confidence = float(confidence_raw)
            if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
                raise ValueError(f"Confidence {confidence} must be a finite float between 0.0 and 1.0")
            
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
                
            # Extract target_payload if provided
            raw_target = opt_decision.get("target_payload")
            target_payload: dict[str, Any] | None = None
            if raw_target is not None:
                if not isinstance(raw_target, dict):
                    raise ValueError("target_payload must be a JSON object/dictionary")
                target_payload = raw_target
                
        except (ValueError, TypeError, AttributeError) as e:
            record_laya_request("decision", "malformed_response")
            record_laya_error("decision", "malformed_response")
            logger.error("Laya decision response malformed", error=str(e))
            raise LayaMalformedResponseError(f"Malformed Laya response: {e}") from e

        record_laya_request("decision", "success")
        return LayaDecisionResult(
            choice=choice,  # type: ignore
            confidence=confidence,
            raw_response=data,
            target_payload=target_payload,
        )
