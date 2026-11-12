"""
Exceptions for channel adapters and external platform connectors (Phase 5).
"""


class ChannelException(Exception):
    """Base exception for channel connector operations."""

    def __init__(self, message: str, provider: str = "", details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.details = details or {}


class UnsupportedChannelException(ChannelException):
    """Raised when an advertising connector is requested for an unsupported or ad-free channel."""
    pass


class UnapprovedPublisherException(ChannelException):
    """Raised when inventory delivery is attempted without an approved signed agreement."""
    pass


class ChannelAuthenticationException(ChannelException):
    """Raised when authentication against external provider fails or credentials are invalid."""
    pass


class ChannelRateLimitException(ChannelException):
    """Raised when external provider rate limit (HTTP 429) is exceeded."""

    def __init__(self, message: str, provider: str = "", retry_after: int = 60, details: dict | None = None):
        super().__init__(message, provider, details)
        self.retry_after = retry_after


class ChannelValidationException(ChannelException):
    """Raised when request payload or parameters violate provider constraints."""
    pass


class ChannelDuplicateException(ChannelException):
    """Raised when an operation is re-attempted with an idempotency collision."""
    pass

