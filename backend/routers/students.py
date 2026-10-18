from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from backend.database.database import get_db
from backend.database.models import User
from backend.schemas.auth import UserResponse
from backend.core.dependencies import get_current_active_user

router = APIRouter()

@router.get("/me", response_model=UserResponse)
def read_user_me(current_user: User = Depends(get_current_active_user)):
    return current_user

@router.patch("/me", response_model=UserResponse)
def update_user_me(
    user_update: dict,  # We'll create a proper schema later
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    # For now, we'll just update a few fields
    for key, value in user_update.items():
        if hasattr(current_user, key) and key not in ["id", "email", "hashed_password", "role", "created_at"]:
            setattr(current_user, key, value)
    db.commit()
    db.refresh(current_user)
    return current_user

@router.get("/me/orders")
def read_user_orders(current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    # We'll implement this later when we have the orders router
    return []