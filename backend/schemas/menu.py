from pydantic import BaseModel, Field
from typing import Optional

class MenuItemBase(BaseModel):
    name: str = Field(..., example="Chicken Biryani")
    description: Optional[str] = Field(None, example="Spicy rice with chicken")
    category: str = Field(..., example="Meals")
    price: int = Field(..., example=15000)  # in paise, so 150.00 INR
    preparation_time_estimate: Optional[int] = Field(None, example=15)  # minutes
    image_url: Optional[str] = Field(None, example="https://example.com/image.jpg")
    is_available: bool = Field(default=True)
    stock_quantity: Optional[int] = Field(None, example=10)

class MenuItemCreate(MenuItemBase):
    pass

class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    price: Optional[int] = None
    preparation_time_estimate: Optional[int] = None
    image_url: Optional[str] = None
    is_available: Optional[bool] = None
    stock_quantity: Optional[int] = None

class MenuItemResponse(MenuItemBase):
    id: int
    created_at: str
    updated_at: Optional[str]

    class Config:
        orm_mode = True