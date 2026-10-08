from datetime import datetime
import uuid
from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    """A single message in a conversation."""
    role: str  # 'user' or 'assistant'
    content: str

class ChatSession(BaseModel):
    """The state of a campaign builder conversation."""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: int
    advertiser_id: int | None = None
    current_campaign_id: int | None = None
    history: list[ChatMessage] = Field(default_factory=list)
    pending_action: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
