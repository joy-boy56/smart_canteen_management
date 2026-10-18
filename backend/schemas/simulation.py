from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class SimulationRequest(BaseModel):
    arrival_rate: float = Field(..., example=2.5, description="Arrival rate per minute")
    service_rate: float = Field(..., example=3.0, description="Service rate per minute per counter")
    counter_count: int = Field(..., example=2, description="Number of counters")
    simulation_duration_minutes: int = Field(..., example=60, description="Duration of simulation in minutes")
    warm_up_period_minutes: int = Field(default=0, example=10, description="Warm-up period in minutes")
    number_of_replications: int = Field(default=1, example=5, description="Number of simulation replications")
    random_seed: Optional[int] = Field(None, example=42, description="Random seed for reproducibility")

class SimulationResult(BaseModel):
    mean_waiting_time: Optional[float] = Field(None, example=0.45, description="Mean waiting time in minutes")
    median_waiting_time: Optional[float] = Field(None, example=0.3, description="Median waiting time in minutes")
    p90_waiting_time: Optional[float] = Field(None, example=1.2, description="90th percentile waiting time in minutes")
    mean_queue_length: Optional[float] = Field(None, example=0.8, description="Mean queue length")
    max_queue_length: Optional[int] = Field(None, example=5, description="Maximum queue length observed")
    estimated_utilization: Optional[float] = Field(None, example=0.42, description="Estimated utilization")
    throughput: Optional[float] = Field(None, example=2.3, description="Throughput (customers per minute)")
    completed_customers: Optional[int] = Field(None, example=120, description="Number of completed customers")
    remaining_customers: Optional[int] = Field(None, example=2, description="Customers remaining in system at end")

class SimulationResponse(BaseModel):
    request: SimulationRequest
    results: List[SimulationResult]
    summary: SimulationResult
    created_at: datetime