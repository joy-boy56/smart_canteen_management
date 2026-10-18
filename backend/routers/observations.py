from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from backend.database.database import get_db
from backend.database.models import Observation
from backend.schemas.observations import ObservationCreate, ObservationResponse
from backend.core.dependencies import get_current_admin_user

router = APIRouter()

@router.post("/", response_model=ObservationResponse, status_code=status.HTTP_201_CREATED)
def create_observation(
    observation: ObservationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_observation = Observation(**observation.dict(), user_id=current_user.id)
    db.add(db_observation)
    db.commit()
    db.refresh(db_observation)
    return db_observation

@router.get("/", response_model=List[ObservationResponse])
def read_observations(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    observations = db.query(Observation).offset(skip).limit(limit).all()
    return observations

@router.get("/{observation_id}", response_model=ObservationResponse)
def read_observation(
    observation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    observation = db.query(Observation).filter(Observation.id == observation_id).first()
    if observation is None:
        raise HTTPException(status_code=404, detail="Observation not found")
    return observation

# We'll add CSV import and export endpoints later