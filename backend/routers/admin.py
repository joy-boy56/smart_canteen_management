from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from backend.database.database import get_db
from backend.database.models import User, Order, MenuItem, Observation
from backend.schemas.auth import UserResponse
from backend.schemas.orders import OrderResponse
from backend.schemas.menu import MenuItemResponse
from backend.schemas.observations import ObservationResponse
from backend.core.dependencies import get_current_admin_user

router = APIRouter()

@router.get("/dashboard")
def get_admin_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    # We'll compute some basic statistics for the dashboard
    total_users = db.query(User).count()
    total_students = db.query(User).filter(User.role == "student").count()
    total_admins = db.query(User).filter(User.role == "admin").count()
    total_orders = db.query(Order).count()
    # Orders by status
    orders_by_status = {}
    for status in ["PLACED", "CONFIRMED", "PREPARING", "READY", "COMPLETED", "CANCELLED"]:
        count = db.query(Order).filter(Order.status == status).count()
        orders_by_status[status] = count
    # Menu items
    total_menu_items = db.query(MenuItem).count()
    available_menu_items = db.query(MenuItem).filter(MenuItem.is_available == True).count()
    # Observations count
    total_observations = db.query(Observation).count()

    return {
        "total_users": total_users,
        "total_students": total_students,
        "total_admins": total_admins,
        "total_orders": total_orders,
        "orders_by_status": orders_by_status,
        "total_menu_items": total_menu_items,
        "available_menu_items": available_menu_items,
        "total_observations": total_observations
    }

@router.get("/orders", response_model=List[OrderResponse])
def read_all_orders(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    query = db.query(Order)
    if status:
        query = query.filter(Order.status == status)
    orders = query.offset(skip).limit(limit).all()
    return orders

@router.get("/students", response_model=List[UserResponse])
def read_all_students(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    students = db.query(User).filter(User.role == "student").offset(skip).limit(limit).all()
    return students

@router.get("/menu", response_model=List[MenuItemResponse])
def read_all_menu_items_admin(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    menu_items = db.query(MenuItem).offset(skip).limit(limit).all()
    return menu_items

@router.get("/observations", response_model=List[ObservationResponse])
def read_all_observations(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    observations = db.query(Observation).offset(skip).limit(limit).all()
    return observations

@router.get("/upi-id")
def get_upi_id(
    current_user: User = Depends(get_current_admin_user)
):
    return {"upi_id": current_user.upi_id}

@router.put("/upi-id")
def update_upi_id(
    upi_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    current_user.upi_id = upi_id
    db.commit()
    return {"upi_id": current_user.upi_id}