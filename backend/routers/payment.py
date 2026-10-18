from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from backend.database.database import get_db
from backend.database.models import Order, User
from backend.core.dependencies import get_current_active_user
import uuid
from datetime import datetime, timedelta

router = APIRouter()

# Mock payment gateway implementation
# In a real application, this would integrate with actual payment gateways like Razorpay, Stripe, etc.

@router.post("/create-intent/{order_id}")
def create_payment_intent(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a payment intent for an order (for online payments)"""
    # Get the order
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Check if the order belongs to the current user
    if order.student_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    # Check if order is already paid
    if order.payment_status == "paid":
        raise HTTPException(status_code=400, detail="Order is already paid")

    # Check if order is in a valid state for payment
    if order.status not in ["PLACED", "CONFIRMED"]:
        raise HTTPException(status_code=400, detail="Order cannot be paid at this stage")

    # Generate a mock payment intent ID
    payment_intent_id = f"pi_{uuid.uuid4().hex[:24]}"

    # In a real implementation, we would create an actual payment intent with a payment gateway
    # For now, we'll simulate it by storing the intent ID in a temporary way
    # We could add a payment_intent_id field to the Order model, but for simplicity
    # we'll return it in the response and expect it to be used in the confirm endpoint

    return {
        "payment_intent_id": payment_intent_id,
        "client_secret": f"{payment_intent_id}_secret_{uuid.uuid4().hex[:16]}",  # Mock client secret
        "amount": order.total_amount,
        "currency": "INR",
        "status": "requires_payment_method"
    }

@router.post("/confirm/{order_id}")
def confirm_payment(
    order_id: int,
    payment_intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Confirm payment for an order"""
    # Get the order
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Check if the order belongs to the current user
    if order.student_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    # Check if order is already paid
    if order.payment_status == "paid":
        raise HTTPException(status_code=400, detail="Order is already paid")

    # In a real implementation, we would verify the payment intent with the payment gateway
    # For now, we'll simulate a successful payment

    # Update order payment status
    order.payment_status = "paid"
    # In a real system, we might also update payment_method if it was specifically online
    # but we'll keep the existing payment_method field as is

    db.commit()
    db.refresh(order)

    return {
        "message": "Payment confirmed successfully",
        "order_id": order.id,
        "payment_status": order.payment_status,
        "payment_intent_id": payment_intent_id
    }

@router.get("/status/{order_id}")
def get_payment_status(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get payment status for an order"""
    # Get the order
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Check if the order belongs to the current user or if user is admin
    if order.student_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")

    return {
        "order_id": order.id,
        "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "amount": order.total_amount
    }

# Simulate webhook endpoint for payment gateway callbacks
@router.post("/webhook/{gateway_name}")
async def payment_webhook(
    gateway_name: str,
    # In a real implementation, we would verify the webhook signature
    # and parse the payload according to the gateway's format
    db: Session = Depends(get_db)
):
    """Handle webhook callbacks from payment gateways"""
    # This is a simplified simulation
    # In reality, each gateway (Razorpay, Stripe, PayPal, etc.) has its own webhook format

    # For simulation purposes, we'll assume the webhook contains:
    # {
    #   "event": "payment.completed",
    #   "data": {
    #     "payment_intent_id": "pi_xxx",
    #     "order_id": 123
    #   }
    # }

    # Since we don't have the actual payload in this simulation,
    # we'll just return a success response
    # In a real implementation, we would:
    # 1. Verify the webhook signature
    # 2. Parse the event
    # 3. Find the order by payment_intent_id or order_id
    # 4. Update the order's payment status

    return {"status": "success"}
