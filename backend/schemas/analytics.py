from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class RateEstimate(BaseModel):
    lambda_rate: float = Field(..., example=2.5, description="Arrival rate per minute")
    mu_rate: float = Field(..., example=3.0, description="Service rate per minute per counter")
    utilization: float = Field(..., example=0.83, description="Utilization ratio (lambda/(c*mu))")
    probability_wait: float = Field(..., example=0.6, description="Probability an arriving customer waits (Erlang-C)")
    expected_queue_length: float = Field(..., example=1.2, description="Expected number of customers in queue")
    expected_waiting_time: float = Field(..., example=0.48, description="Expected waiting time in queue (minutes)")
    expected_total_time: float = Field(..., example=0.81, description="Expected total time in system (minutes)")
    active_counters: int = Field(..., example=2, description="Number of active counters")

class AnalyticsResponse(BaseModel):
    observation_period: str = Field(..., example="2026-10-13 09:00 to 10:00")
    data_source: str = Field(..., example="manual")
    rate_estimate: RateEstimate
    notes: Optional[str] = None