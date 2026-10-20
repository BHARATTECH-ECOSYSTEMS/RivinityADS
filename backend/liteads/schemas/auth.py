"""Authentication request and response contracts."""

from pydantic import BaseModel, EmailStr, Field


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str
    advertiser_id: int | None = None
    publisher_id: int | None = None
    email_verified: bool = False


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(default="advertiser", pattern="^(advertiser|publisher)$")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


# Convenience alias for password reset request
ForgotPasswordRequest = RequestPasswordResetRequest


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    pass


class LogoutRequest(BaseModel):
    pass


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=12, max_length=128)


class AuthResponse(BaseModel):
    user: UserResponse
    access_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    message: str


class VerifyEmailResponse(MessageResponse):
    email: str | None = None