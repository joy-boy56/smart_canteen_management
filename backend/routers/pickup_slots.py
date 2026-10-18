from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from backend.database.database import get_db
from backend.database.models import PickupSlot, User
from backend.schemas.pickup_slots import PickupSlotCreate, PickupSlotResponse, PickupSlotUpdate, PickupSlotWithStats
from backend.core.dependencies import get_current_admin_user
from datetime import datetime, timezone
import math

router = APIRouter()

@router.get("/", response_model=List[PickupSlotResponse])
def read_pickup_slots(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    slots = db.query(PickupSlot).offset(skip).limit(limit).all()
    return slots

@router.get("/{slot_id}", response_model=PickupSlotResponse)
def read_pickup_slot(
    slot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    slot = db.query(PickupSlot).filter(PickupSlot.id == slot_id).first()
    if slot is None:
        raise HTTPException(status_code=404, detail="Pickup slot not found")
    return slot

@router.post("/", response_model=PickupSlotResponse, status_code=status.HTTP_201_CREATED)
def create_pickup_slot(
    slot: PickupSlotCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    # Validate that end_time is after start_time
    if slot.end_time <= slot.start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    # Validate booking_cutoff if provided
    if slot.booking_cutoff and slot.booking_cutoff >= slot.start_time:
        raise HTTPException(status_code=400, detail="Booking cutoff must be before slot start time")

    db_slot = PickupSlot(**slot.dict())
    db.add(db_slot)
    db.commit()
    db.refresh(db_slot)
    return db_slot

@router.patch("/{slot_id}", response_model=PickupSlotResponse)
def update_pickup_slot(
    slot_id: int,
    slot_update: PickupSlotUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_slot = db.query(PickupSlot).filter(PickupSlot.id == slot_id).first()
    if db_slot is None:
        raise HTTPException(status_code=404, detail="Pickup slot not found")

    # Prepare update data
    update_data = slot_update.dict(exclude_unset=True)

    # Validate time constraints if provided
    if 'start_time' in update_data or 'end_time' in update_data:
        start_time = update_data.get('start_time', db_slot.start_time)
        end_time = update_data.get('end_time', db_slot.end_time)
        if end_time <= start_time:
            raise HTTPException(status_code=400, detail="End time must be after start time")

    if 'booking_cutoff' in update_data and update_data['booking_cutoff']:
        start_time = update_data.get('start_time', db_slot.start_time)
        if update_data['booking_cutoff'] >= start_time:
            raise HTTPException(status_code=400, detail="Booking cutoff must be before slot start time")

    # Apply updates
    for key, value in update_data.items():
        setattr(db_slot, key, value)

    db_slot.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_slot)
    return db_slot

@router.delete("/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pickup_slot(
    slot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_slot = db.query(PickupSlot).filter(PickupSlot.id == slot_id).first()
    if db_slot is None:
        raise HTTPException(status_code=404, detail="Pickup slot not found")

    # Check if slot has any orders
    if db_slot.orders:
        raise HTTPException(status_code=400, detail="Cannot delete slot with existing orders")

    db.delete(db_slot)
    db.commit()
    return None

@router.get("/{slot_id}/stats", response_model=PickupSlotWithStats)
def get_slot_stats(
    slot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    slot = db.query(PickupSlot).filter(PickupSlot.id == slot_id).first()
    if slot is None:
        raise HTTPException(status_code=404, detail="Pickup slot not found")

    # Calculate effective capacity
    effective_capacity = math.floor(slot.max_orders * slot.safety_factor)
    utilization_rate = slot.current_orders / slot.max_orders if slot.max_orders > 0 else 0
    remaining_capacity = max(0, effective_capacity - slot.current_orders)
    is_full = slot.current_orders >= effective_capacity

    return PickupSlotWithStats(
        **slot.__dict__,
        utilization_rate=utilization_rate,
        remaining_capacity=remaining_capacity,
        is_full=is_full
    )

@router.get("/active/", response_model=List[PickupSlotResponse])
def get_active_slots(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    now = datetime.now(timezone.utc)
    slots = db.query(PickupSlot).filter(
        PickupSlot.status == "active",
        PickupSlot.end_time > now
    ).all()
    return slots