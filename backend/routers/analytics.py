from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from backend.database.database import get_db
from backend.database.models import Observation
from backend.schemas.analytics import AnalyticsResponse, RateEstimate
from backend.core.dependencies import get_current_admin_user
import math

router = APIRouter()

# We'll create a helper function to calculate the M/M/c metrics
def calculate_mm_c_metrics(lambda_rate: float, mu_rate: float, c: int):
    # lambda_rate: arrival rate per minute
    # mu_rate: service rate per minute per counter
    # c: number of counters
    if lambda_rate <= 0 or mu_rate <= 0 or c <= 0:
        return None
    a = lambda_rate / mu_rate  # offered traffic
    rho = lambda_rate / (c * mu_rate)  # utilization
    if rho >= 1:
        # Unstable system
        return {
            "lambda_rate": lambda_rate,
            "mu_rate": mu_rate,
            "utilization": rho,
            "probability_wait": None,
            "expected_queue_length": None,
            "expected_waiting_time": None,
            "expected_total_time": None,
            "active_counters": c,
            "stable": False
        }
    # Calculate P0 (probability of zero customers in system)
    sum_terms = sum([(a**n) / math.factorial(n) for n in range(c)])
    last_term = (a**c) / (math.factorial(c) * (1 - rho))
    P0 = 1 / (sum_terms + last_term)
    # Probability of waiting (Erlang-C formula)
    Pw = (a**c) / (math.factorial(c) * (1 - rho)) * P0
    # Expected queue length
    Lq = (P0 * (a**c) * rho) / (math.factorial(c) * ((1 - rho)**2))
    # Expected waiting time in queue
    Wq = Lq / lambda_rate if lambda_rate > 0 else 0
    # Expected time in system
    W = Wq + (1 / mu_rate)
    # Expected number in system
    L = Lq + (lambda_rate / mu_rate)
    return {
        "lambda_rate": lambda_rate,
        "mu_rate": mu_rate,
        "utilization": rho,
        "probability_wait": Pw,
        "expected_queue_length": Lq,
        "expected_waiting_time": Wq,
        "expected_total_time": W,
        "active_counters": c,
        "stable": True
    }

@router.post("/analyze", response_model=AnalyticsResponse)
def analyze_queue(
    # We'll allow the user to specify an observation period or use the latest observation
    # For simplicity, we'll use the latest observation
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    # Get the latest observation
    latest_observation = db.query(Observation).order_by(Observation.created_at.desc()).first()
    if latest_observation is None:
        raise HTTPException(status_code=404, detail="No observations found")
    # Calculate arrival rate and service rate from the observation
    # We'll assume the observation has: arrivals, duration_minutes, mean_service_time_seconds, active_counters
    duration_minutes = latest_observation.duration_minutes
    if duration_minutes is None or duration_minutes <= 0:
        # Calculate from start and end time
        if latest_observation.start_time and latest_observation.end_time:
            duration_minutes = (latest_observation.end_time - latest_observation.start_time).total_seconds() / 60
        else:
            raise HTTPException(status_code=400, detail="Observation duration not available")
    lambda_rate = latest_observation.arrivals / duration_minutes  # arrivals per minute
    # Service rate per counter: we have mean_service_time_seconds per customer
    # If mean_service_time_seconds is provided, then mu_rate = 60 / mean_service_time_seconds (per minute)
    # If not, we can use number_served and duration to compute service rate
    if latest_observation.mean_service_time_seconds and latest_observation.mean_service_time_seconds > 0:
        mu_rate = 60 / latest_observation.mean_service_time_seconds  # per minute per counter
    else:
        # Fallback: if we have number_served and active_counters, we can estimate
        # But for now, we'll require mean_service_time_seconds
        raise HTTPException(status_code=400, detail="Mean service time not available in observation")
    c = latest_observation.active_counters
    # Calculate metrics
    metrics = calculate_mm_c_metrics(lambda_rate, mu_rate, c)
    if metrics is None:
        raise HTTPException(status_code=400, detail="Invalid input for queue analysis")
    # Prepare response
    rate_estimate = RateEstimate(
        lambda_rate=metrics["lambda_rate"],
        mu_rate=metrics["mu_rate"],
        utilization=metrics["utilization"],
        probability_wait=metrics["probability_wait"] or 0.0,
        expected_queue_length=metrics["expected_queue_length"] or 0.0,
        expected_waiting_time=metrics["expected_waiting_time"] or 0.0,
        expected_total_time=metrics["expected_total_time"] or 0.0,
        active_counters=metrics["active_counters"]
    )
    return AnalyticsResponse(
        observation_period=f"{latest_observation.start_time} to {latest_observation.end_time}",
        data_source=latest_observation.source.value,
        rate_estimate=rate_estimate,
        notes=latest_observation.notes
    )