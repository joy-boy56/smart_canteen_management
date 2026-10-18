from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class TokenResponse(BaseModel):
    id: int
    order_id: int
    opaque_token: str
    status: str
    created_at: datetime
    expires_at: Optional[datetime]
    redeemed_at: Optional[datetime]

    class Config:
        orm_mode = True

class TokenVerify(BaseModel):
    opaque_token: str = Field(..., example="A-042")

class TokenVerifyResponse(BaseModel):
    valid: bool
    order_id: Optional[int] = None
    student_name: Optional[str] = None
    order_status: Optional[str] = None
    total_amount: Optional[int] = None  # in paise
    items: Optional[list] = None
    message: str