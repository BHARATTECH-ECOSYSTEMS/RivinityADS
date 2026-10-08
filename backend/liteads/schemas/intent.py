from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel, Field

IntentType = Literal[
    "CREATE_CAMPAIGN",
    "SET_BUDGET",
    "SET_GEO",
    "SET_SECTOR",
    "REQUEST_PAYMENT",
    "LAUNCH_CAMPAIGN",
    "UNKNOWN"
]

class SlotData(BaseModel):
    """Extracted slot data for campaign modification."""
    name: Optional[str] = None
    budget_daily: Optional[Decimal] = Field(None, ge=0)
    budget_total: Optional[Decimal] = Field(None, ge=0)
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    sector: Optional[str] = None
    is_exclude: Optional[bool] = None
    amount: Optional[Decimal] = Field(None, ge=0)

class IntentResult(BaseModel):
    """The structured intent extracted from a user's conversational message."""
    intent: IntentType
    slots: SlotData = Field(default_factory=SlotData)
    confidence: Optional[float] = Field(None, ge=0, le=1)
    reply: Optional[str] = None
