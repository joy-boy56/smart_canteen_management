from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ObservationBase(BaseModel):
    observation_date: datetime = Field(..., example="2026-10-13T09:00:00Z")
    start_time: datetime = Field(..., example="2026-10-13T09:00:00Z")
    end_time: Optional[datetime] = Field(None, example="2026-10-13T10:00:00Z")
    duration_minutes: Optional[int] = Field(None, example=60)
    arrivals: int = Field(..., example=20)
    number_served: Optional[int] = Field(None, example=18)
    mean_service_time_seconds: Optional[float] = Field(None, example=120.0)
    active_counters: int = Field(..., example=2)
    waiting_start: Optional[int] = Field(None, example=0)
    waiting_end: Optional[int] = Field(None, example=2)
    notes: Optional[str] = Field(None, example="Lunch rush")
    source: str = Field(default="manual", example="manual")

class ObservationCreate(ObservationBase):
    pass

class ObservationResponse(ObservationBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True