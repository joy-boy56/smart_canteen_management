from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from backend.database.database import get_db
from backend.database.models import MenuItem
from backend.schemas.menu import MenuItemCreate, MenuItemResponse, MenuItemUpdate
from backend.core.dependencies import get_current_active_user, get_current_admin_user

router = APIRouter()

@router.get("/", response_model=List[MenuItemResponse])
def read_menu_items(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    menu_items = db.query(MenuItem).filter(MenuItem.is_available == True).offset(skip).limit(limit).all()
    return menu_items

@router.get("/{item_id}", response_model=MenuItemResponse)
def read_menu_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    menu_item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if menu_item is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return menu_item

@router.post("/", response_model=MenuItemResponse, status_code=status.HTTP_201_CREATED)
def create_menu_item(
    menu_item: MenuItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_menu_item = MenuItem(**menu_item.dict())
    db.add(db_menu_item)
    db.commit()
    db.refresh(db_menu_item)
    return db_menu_item

@router.patch("/{item_id}", response_model=MenuItemResponse)
def update_menu_item(
    item_id: int,
    menu_item: MenuItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_menu_item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if db_menu_item is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    for key, value in menu_item.dict(exclude_unset=True).items():
        setattr(db_menu_item, key, value)
    db.commit()
    db.refresh(db_menu_item)
    return db_menu_item

@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_menu_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_menu_item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if db_menu_item is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    db.delete(db_menu_item)
    db.commit()
    return None