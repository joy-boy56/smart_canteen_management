from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from backend.database.database import get_db
from backend.database.models import Token, Order, User
from backend.schemas.tokens import TokenResponse, TokenVerify, TokenVerifyResponse
from backend.core.dependencies import get_current_active_user, get_current_admin_user
import uuid
import datetime

router = APIRouter()

# Note: Token generation is typically done when an order is placed.
# We'll create a helper function to generate a token and associate it with an order.
# This function can be called from the orders router.

def generate_opaque_token():
    # Generate a UUID and take a short part for the opaque token
    return str(uuid.uuid4()).replace("-", "")[:8].upper()

@router.post("/verify", response_model=TokenVerifyResponse)
def verify_token(
    token_verification: TokenVerify,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)  # Only admin can verify tokens
):
    # Find the token by opaque_token
    db_token = db.query(Token).filter(Token.opaque_token == token_verification.opaque_token).first()
    if db_token is None:
        return TokenVerifyResponse(
            valid=False,
            message="Invalid token"
        )
    # Check token status
    if db_token.status != "ACTIVE":
        return TokenVerifyResponse(
            valid=False,
            message=f"Token is {db_token.status}"
        )
    # Check expiration if set
    if db_token.expires_at and db_token.expires_at < datetime.datetime.utcnow():
        return TokenVerifyResponse(
            valid=False,
            message="Token has expired"
        )
    # Get the associated order
    order = db.query(Order).filter(Order.id == db_token.order_id).first()
    if order is None:
        return TokenVerifyResponse(
            valid=False,
            message="Associated order not found"
        )
    # Get the student who placed the order
    student = db.query(User).filter(User.id == order.student_id).first()
    # Return token verification response
    return TokenVerifyResponse(
        valid=True,
        order_id=order.id,
        student_name=student.full_name if student else None,
        order_status=order.status,
        total_amount=order.total_amount,
        items=[],  # We'll populate this later if needed
        message="Token is valid"
    )

@router.post("/{token_id}/redeem", response_model=TokenResponse)
def redeem_token(
    token_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)  # Only admin can redeem tokens
):
    db_token = db.query(Token).filter(Token.id == token_id).first()
    if db_token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    if db_token.status != "ACTIVE":
        raise HTTPException(status_code=400, detail=f"Token is {db_token.status}")
    # Redeem the token
    db_token.status = "REDEEMED"
    db_token.redeemed_at = datetime.datetime.utcnow()
    db_token.redeemed_by = current_user.id
    db.commit()
    db.refresh(db_token)
    return db_token

# We'll also create an endpoint to get a token by order ID (for the student to see their token)
@router.get("/order/{order_id}", response_model=TokenResponse)
def get_token_by_order_id(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # Find the token for the order
    db_token = db.query(Token).filter(Token.order_id == order_id).first()
    if db_token is None:
        raise HTTPException(status_code=404, detail="Token not found for this order")
    # Check if the current user is the student who placed the order or an admin
    order = db.query(Order).filter(Order.id == order_id).first()
    if order.student_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return db_token