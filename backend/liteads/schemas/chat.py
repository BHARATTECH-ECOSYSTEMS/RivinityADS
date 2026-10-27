from datetime import datetime
from typing import Any
import uuid
from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    """A single message in a conversation."""
    role: str  # 'user' or 'assistant'
    content: str

class ChatSession(BaseModel):
    """The state of a campaign builder conversation supporting Campaign -> Ad Set -> Ads."""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: int
    advertiser_id: int | None = None
    current_campaign_id: int | None = None
    current_ad_set_id: int | None = None
    current_step: str = "CAMPAIGN"  # "CAMPAIGN" | "AD_SET" | "CREATIVE" | "REVIEW"
    draft_data: dict[str, Any] = Field(default_factory=dict)
    pending_action: str | None = None
    history: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
