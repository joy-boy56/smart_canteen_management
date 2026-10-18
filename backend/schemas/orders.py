from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class OrderItemBase(BaseModel):
    menu_item_id: int = Field(..., example=1)
    quantity: int = Field(..., example=2)

class OrderItemCreate(OrderItemBase):
    pass

class OrderItemResponse(OrderItemBase):
    id: int
    item_name_snapshot: str
    unit_price_snapshot: int  # in paise
    subtotal: int  # in paise

    class Config:
        orm_mode = True

class OrderBase(BaseModel):
    pass

class OrderCreate(BaseModel):
    items: List[OrderItemCreate]
    requested_pickup_time: Optional[datetime] = None
    pickup_slot_id: Optional[int] = None  # New: explicit slot selection
    payment_method: str = Field(default='cash', example='cash')
    payment_status: str = Field(default='unpaid', example='unpaid')

class OrderResponse(BaseModel):
    id: int
    student_id: int
    human_readable_id: str
    status: str
    total_amount: int  # in paise
    created_at: datetime
    requested_pickup_time: Optional[datetime]
    pickup_slot_id: Optional[int] = None
    confirmed_at: Optional[datetime]
    preparation_start_at: Optional[datetime]
    ready_at: Optional[datetime]
    completed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    items: List[OrderItemResponse]
    token: Optional[str] = None
    payment_method: str
    payment_status: str

    class Config:
        orm_mode = True

class OrderStatusUpdate(BaseModel):
    status: str
    note: Optional[str] = None