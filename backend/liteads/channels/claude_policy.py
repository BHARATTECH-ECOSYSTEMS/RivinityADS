"""
Claude / Anthropic Advertising Policy Enforcement (Phase 5).

MANDATORY REGULATORY POLICY:
Anthropic's public policy states that Claude will remain strictly ad-free as of 4 February 2026.
No advertising connector, inventory adapter, or sponsored injection connector may exist
or be registered for Claude.
"""

from liteads.channels.exceptions import UnsupportedChannelException


class ClaudePolicyGuard:
    """Policy guard enforcing that Claude remains completely ad-free."""

    PROHIBITED_CHANNEL = "claude"
    POLICY_EFFECTIVE_DATE = "2026-02-04"
    POLICY_STATEMENT = (
        "Anthropic's public policy states Claude will remain ad-free as of 4 February 2026. "
        "Rivinity strictly prohibits advertising connectors, sponsored insertions, or placement "
        "adapters targeting Claude or Anthropic surfaces."
    )

    @classmethod
    def enforce_ad_free_policy(cls, channel_name: str) -> None:
        """
        Check channel name and reject any Claude placement attempts.
        
        Raises UnsupportedChannelException if channel references Claude/Anthropic.
        """
        normalized = channel_name.lower().strip()
        if "claude" in normalized or "anthropic" in normalized:
            raise UnsupportedChannelException(
                message=cls.POLICY_STATEMENT,
                provider="claude",
                details={
                    "policy_effective_date": cls.POLICY_EFFECTIVE_DATE,
                    "reason": "ad_free_policy_mandate",
                },
            )

