from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from liteads.models.domain import VerificationStatus, VerificationMethod


class DomainBase(BaseModel):
    domain: str = Field(..., description="The domain name, e.g., example.com")


class DomainCreate(DomainBase):
    pass


class DomainResponse(DomainBase):
    id: int
    advertiser_id: int
    status: VerificationStatus
    verification_method: VerificationMethod
    verification_token: str
    verified_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DomainVerificationInstructions(BaseModel):
    domain: str
    record_type: str = "TXT"
    record_name: str
    record_value: str
    instructions: str
