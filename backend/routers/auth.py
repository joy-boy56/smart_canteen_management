from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import jwt
from backend.core.config import settings
from backend.database.database import get_db
from backend.database.models import User
from backend.schemas.auth import UserCreate, UserLogin, Token, UserResponse, TokenData
from backend.core.security import get_password_hash, verify_password, create_access_token
from backend.core.dependencies import get_current_active_user

router = APIRouter()

@router.post("/register", response_model=UserResponse)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    # Check if user already exists
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    if user.student_reg_number:
        db_user = db.query(User).filter(User.student_reg_number == user.student_reg_number).first()
        if db_user:
            raise HTTPException(status_code=400, detail="Student registration number already registered")

    # Create new user
    hashed_password = get_password_hash(user.password)
    db_user = User(
        full_name=user.full_name,
        email=user.email,
        phone_number=user.phone_number,
        student_reg_number=user.student_reg_number,
        department=user.department,
        year_semester=user.year_semester,
        hashed_password=hashed_password,
        role="student"  # default role, admin creation is separate
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@router.post("/login", response_model=Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user