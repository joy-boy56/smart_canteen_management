from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class UserBase(BaseModel):
    full_name: str = Field(..., example="John Doe")
    email: EmailStr = Field(..., example="john.doe@example.com")
    phone_number: Optional[str] = Field(None, example="+1234567890")
    student_reg_number: Optional[str] = Field(None, example="STU12345")
    department: Optional[str] = Field(None, example="Computer Science")
    year_semester: Optional[str] = Field(None, example="Year 2, Semester 1")

class UserCreate(UserBase):
    password: str = Field(..., min_length=8, example="securepassword123")

class UserLogin(BaseModel):
    email: EmailStr = Field(..., example="john.doe@example.com")
    password: str = Field(..., example="securepassword123")

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: str
    updated_at: Optional[str]

    class Config:
        orm_mode = True

class TokenData(BaseModel):
    email: Optional[str] = None