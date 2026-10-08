"""
Razorpay payment client abstraction.
"""

from decimal import Decimal
from typing import Any

import razorpay

from liteads.common.config import get_settings
from liteads.common.logger import get_logger

logger = get_logger(__name__)


class RazorpayClientError(Exception):
    """Exception raised for Razorpay client errors."""
    pass


class RazorpayClient:
    """Razorpay client for payment processing."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.payment.key_id or not settings.payment.key_secret:
            raise ValueError("Razorpay credentials (RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET) are missing")
            
        self._client = razorpay.Client(
            auth=(settings.payment.key_id, settings.payment.key_secret)
        )

    def create_order(self, amount: Decimal, currency: str = "INR", receipt: str | None = None) -> dict[str, Any]:
        """
        Create a Razorpay order.
        
        Args:
            amount: Amount in primary currency unit (e.g., Decimal("100.00") for rupees).
            currency: Currency code (default: "INR").
            receipt: Optional receipt ID for internal tracking.
            
        Returns:
            Dictionary containing the Razorpay order details.
            
        Raises:
            RazorpayClientError: If order creation fails.
        """
        # Razorpay expects amount in smallest currency unit (e.g., paise for INR)
        amount_in_smallest_unit = int(amount * 100)
        
        data: dict[str, Any] = {
            "amount": amount_in_smallest_unit,
            "currency": currency,
        }
        if receipt:
            data["receipt"] = receipt
            
        try:
            order = self._client.order.create(data=data)
            return dict(order)
        except Exception as e:
            logger.error(f"Failed to create Razorpay order: {str(e)}")
            raise RazorpayClientError("Could not create payment request") from e

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """Verify the Razorpay webhook signature."""
        settings = get_settings()
        secret = settings.payment.webhook_secret
        if not secret:
            logger.error("Razorpay webhook secret is missing")
            return False
            
        try:
            self._client.utility.verify_webhook_signature(
                body.decode("utf-8"),
                signature,
                secret
            )
            return True
        except Exception as e:
            logger.error(f"Razorpay webhook signature verification failed: {e}")
            return False


_client_instance: RazorpayClient | None = None

def get_razorpay_client() -> RazorpayClient:
    """Dependency for providing a singleton Razorpay client."""
    global _client_instance
    if _client_instance is None:
        _client_instance = RazorpayClient()
    return _client_instance
