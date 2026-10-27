from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel, Field

IntentType = Literal[
    "CREATE_CAMPAIGN",
    "SELECT_CAMPAIGN",
    "UPDATE_CAMPAIGN",
    "CREATE_AD_SET",
    "SELECT_AD_SET",
    "UPDATE_AD_SET",
    "SET_BUDGET",
    "SET_GEO",
    "SET_SECTOR",
    "CREATE_AD",
    "REVIEW_CAMPAIGN",
    "REQUEST_PAYMENT",
    "LAUNCH_CAMPAIGN",
    "UNKNOWN"
]

class SlotData(BaseModel):
    """Extracted slot data for campaign, ad set, creative, targeting, and billing operations."""
    name: Optional[str] = None
    campaign_id: Optional[int] = None
    ad_set_id: Optional[int] = None
    ad_set_name: Optional[str] = None
    budget_daily: Optional[Decimal] = Field(None, ge=0)
    budget_total: Optional[Decimal] = Field(None, ge=0)
    bid_amount: Optional[Decimal] = Field(None, ge=0)
    bid_type: Optional[int] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    sector: Optional[str] = None
    is_exclude: Optional[bool] = None
    amount: Optional[Decimal] = Field(None, ge=0)
    # AdSet Frequency Capping & Schedule
    freq_cap_daily: Optional[int] = None
    freq_cap_hourly: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    # Ad / Creative fields
    title: Optional[str] = None
    landing_url: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    creative_type: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None

class IntentResult(BaseModel):
    """The structured intent extracted from a user's conversational message."""
    intent: IntentType
    slots: SlotData = Field(default_factory=SlotData)
    confidence: Optional[float] = Field(None, ge=0, le=1)
    reply: Optional[str] = None
