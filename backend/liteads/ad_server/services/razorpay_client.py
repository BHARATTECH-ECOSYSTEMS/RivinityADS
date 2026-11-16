"""
Razorpay payment client abstraction.
"""

from decimal import Decimal
import hashlib
import hmac
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
            settings = get_settings()
            if settings.env == "dev" and ("authentication failed" in str(e).lower() or "unauthorized" in str(e).lower() or "badrequest" in type(e).__name__.lower()):
                logger.warning("Razorpay authentication failed in dev mode; falling back to development mock order")
                import uuid
                mock_order_id = f"order_{uuid.uuid4().hex[:14]}"
                return {
                    "id": mock_order_id,
                    "entity": "order",
                    "amount": amount_in_smallest_unit,
                    "currency": currency,
                    "status": "created",
                    "receipt": receipt or f"receipt_{mock_order_id}",
                }
            raise RazorpayClientError("Could not create payment request") from e

    def create_qr_code(self, amount: Decimal, receipt: str, advertiser_id: int) -> dict[str, Any]:
        """
        Create a Razorpay UPI QR code.
        """
        amount_in_smallest_unit = int(amount * 100)
        data: dict[str, Any] = {
            "type": "upi_qr",
            "name": "OpenAdServer",
            "usage": "single_use",
            "fixed_amount": True,
            "payment_amount": amount_in_smallest_unit,
            "description": receipt,
            "notes": {
                "receipt": receipt,
                "advertiser_id": str(advertiser_id)
            }
        }
        try:
            qr = self._client.qrcode.create(data=data)
            return dict(qr)
        except Exception as e:
            logger.error(f"Failed to create Razorpay QR code: {str(e)}")
            settings = get_settings()
            if settings.env == "dev" and ("authentication failed" in str(e).lower() or "unauthorized" in str(e).lower() or "badrequest" in type(e).__name__.lower()):
                logger.warning("Razorpay authentication failed in dev mode; falling back to development mock QR code")
                import uuid
                mock_qr_id = f"qr_{uuid.uuid4().hex[:14]}"
                mock_image_url = f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=upi://pay?pa=dev@liteads&pn=RivinityADS&am={amount}&cu=INR"
                return {
                    "id": mock_qr_id,
                    "entity": "qr_code",
                    "amount": amount_in_smallest_unit,
                    "currency": "INR",
                    "status": "created",
                    "image_url": mock_image_url,
                }
            raise RazorpayClientError("Could not create QR code payment request") from e

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """Verify the Razorpay webhook signature against raw request bytes."""
        settings = get_settings()
        secret = settings.payment.webhook_secret
        if not secret:
            logger.error("Razorpay webhook secret is missing")
            return False
            
        try:
            if isinstance(body, str):
                body_bytes = body.encode("utf-8")
            else:
                body_bytes = body

            key_bytes = secret.encode("utf-8")
            dig = hmac.new(key=key_bytes, msg=body_bytes, digestmod=hashlib.sha256).hexdigest()
            return hmac.compare_digest(dig, signature)
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
