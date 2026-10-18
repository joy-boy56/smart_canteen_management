from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum

class SlotStatus(str, Enum):
    active = "active"
    inactive = "inactive"
    closed = "closed"

class PickupSlotBase(BaseModel):
    start_time: datetime = Field(..., example="2026-10-14T12:00:00+05:30")
    end_time: datetime = Field(..., example="2026-10-14T12:15:00+05:30")
    booking_cutoff: Optional[datetime] = Field(None, example="2026-10-14T11:30:00+05:30")
    max_orders: int = Field(..., example=20, gt=0)
    safety_factor: float = Field(default=0.8, ge=0.0, le=1.0)
    status: SlotStatus = Field(default=SlotStatus.active)
    description: Optional[str] = Field(None, example="Lunch slot")

class PickupSlotCreate(PickupSlotBase):
    pass

class PickupSlotUpdate(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    booking_cutoff: Optional[datetime] = None
    max_orders: Optional[int] = Field(None, gt=0)
    current_orders: Optional[int] = Field(None, ge=0)
    safety_factor: Optional[float] = Field(None, ge=0.0, le=1.0)
    status: Optional[SlotStatus] = None
    description: Optional[str] = None

class PickupSlotResponse(PickupSlotBase):
    id: int
    current_orders: int = Field(..., example=15)
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True

class PickupSlotWithStats(PickupSlotResponse):
    utilization_rate: float = Field(..., example=0.75)
    remaining_capacity: int = Field(..., example=5)
    is_full: bool = Field(..., example=False)