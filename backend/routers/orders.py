from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from backend.database.database import get_db
from backend.database.models import Order, OrderItem, User, PickupSlot
from backend.schemas.orders import OrderCreate, OrderResponse, OrderItemCreate, OrderStatusUpdate
from backend.core.dependencies import get_current_active_user, get_current_admin_user
import uuid
from datetime import datetime, timedelta, timezone

router = APIRouter()

def generate_human_readable_id():
    # Generate a simple human-readable ID, e.g., A-001
    # In a real app, we might use a sequence or a more sophisticated method
    return f"A-{uuid.uuid4().hex[:6].upper()}"

@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def place_order(
    order: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # Validate menu items and calculate total
    total_amount = 0
    db_order_items = []
    for item in order.items:
        # Fetch the menu item and validate availability and price
        menu_item = db.query(MenuItem).filter(MenuItem.id == item.menu_item_id).first()
        if not menu_item:
            raise HTTPException(status_code=400, detail=f"Menu item with id {item.menu_item_id} not found")
        if not menu_item.is_available:
            raise HTTPException(status_code=400, detail=f"Menu item {menu_item.name} is not available")
        # Calculate subtotal
        subtotal = menu_item.price * item.quantity  # price is in paise
        total_amount += subtotal
        db_order_item = OrderItem(
            menu_item_id=item.menu_item_id,
            item_name_snapshot=menu_item.name,
            unit_price_snapshot=menu_item.price,  # in paise
            quantity=item.quantity,
            subtotal=subtotal
        )
        db_order_items.append(db_order_item)

    # Handle pickup slot selection if provided
    pickup_slot = None
    if order.pickup_slot_id:
        # Validate the pickup slot
        pickup_slot = db.query(PickupSlot).filter(PickupSlot.id == order.pickup_slot_id).first()
        if not pickup_slot:
            raise HTTPException(status_code=404, detail="Pickup slot not found")

        # Check if slot is active
        if pickup_slot.status != "active":
            raise HTTPException(status_code=400, detail="Pickup slot is not active")

        # Check booking cutoff if applicable
        now = datetime.now(timezone.utc)
        if pickup_slot.booking_cutoff and now > pickup_slot.booking_cutoff:
            raise HTTPException(status_code=400, detail="Booking cutoff for this slot has passed")

        # Check if slot has capacity
        effective_capacity = int(pickup_slot.max_orders * pickup_slot.safety_factor)
        if pickup_slot.current_orders >= effective_capacity:
            raise HTTPException(status_code=400, detail="Pickup slot is at full capacity")

        # Increment the slot's current_orders count
        pickup_slot.current_orders += 1

    # Create order object (not yet committed)
    # For online payments, we start with unpaid status and require payment confirmation
    # For cash payments, we use the provided payment_status
    final_payment_status = order.payment_status
    if order.payment_method == "online":
        # For online payments, always start as unpaid until payment is confirmed
        final_payment_status = "unpaid"

    db_order = Order(
        student_id=current_user.id,
        pickup_slot_id=order.pickup_slot_id if order.pickup_slot_id else None,
        human_readable_id=generate_human_readable_id(),
        total_amount=total_amount,  # in paise
        requested_pickup_time=order.requested_pickup_time,
        payment_method=order.payment_method,
        payment_status=final_payment_status
    )
    db.add(db_order)
    # Add order items to session
    for db_order_item in db_order_items:
        db_order_item.order = db_order  # set relationship
        db.add(db_order_item)

    # Create token
    db_token = Token(
        order=db_order,
        user_id=current_user.id,
        opaque_token=str(uuid.uuid4()).replace("-", "")[:8].upper(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    db.add(db_token)

    # Commit all changes
    db.commit()
    db.refresh(db_order)
    return db_order

@router.get("/{order_id}", response_model=OrderResponse)
def read_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    # Check if the order belongs to the current user or if the user is an admin
    if order.student_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return order

@router.get("/", response_model=List[OrderResponse])
def read_orders(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # If the user is a student, only return their own orders
    # If the user is an admin, return all orders
    if current_user.role == "admin":
        orders = db.query(Order).offset(skip).limit(limit).all()
    else:
        orders = db.query(Order).filter(Order.student_id == current_user.id).offset(skip).limit(limit).all()
    return orders

@router.patch("/{order_id}/status", response_model=OrderResponse)
def update_order_status(
    order_id: int,
    status_update: OrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)  # Only admin can update status
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    # Update order status
    # We'll also create a status history entry
    # For now, we'll just update the status field
    # We'll implement the status transition logic later
    order.status = status_update.status
    # Set the appropriate timestamp based on the new status
    if status_update.status == "CONFIRMED":
        order.confirmed_at = func.now()
    elif status_update.status == "PREPARING":
        order.preparation_start_at = func.now()
    elif status_update.status == "READY":
        order.ready_at = func.now()
    elif status_update.status == "COMPLETED":
        order.completed_at = func.now()
    elif status_update.status == "CANCELLED":
        order.cancelled_at = func.now()
    db.commit()
    db.refresh(order)
    return order

@router.post("/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    # Check if the order belongs to the current user
    if order.student_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    # Only allow cancellation if the order is in PLACED or CONFIRMED state
    if order.status not in ["PLACED", "CONFIRMED"]:
        raise HTTPException(status_code=400, detail="Order cannot be cancelled at this stage")
    # Update order status to CANCELLED
    order.status = "CANCELLED"
    order.cancelled_at = func.now()
    db.commit()
    db.refresh(order)
    return order