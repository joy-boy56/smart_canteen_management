from pydantic import BaseModel, Field
from typing import List, Optional

class OptimizationRequest(BaseModel):
    lambda_rate: float = Field(..., example=2.5, description="Arrival rate per minute")
    mu_rate: float = Field(..., example=3.0, description="Service rate per minute per counter")
    min_counters: int = Field(..., example=1, description="Minimum allowed counters")
    max_counters: int = Field(..., example=5, description="Maximum allowed counters")
    cost_per_counter_per_hour: float = Field(..., example=100.0, description="Cost per counter per hour in INR")
    max_waiting_time: float = Field(..., example=5.0, description="Maximum acceptable expected waiting time in minutes")

class OptimizationResult(BaseModel):
    recommended_counters: int = Field(..., example=2)
    utilization: float = Field(..., example=0.42, description="Utilization at recommended counters")
    probability_wait: float = Field(..., example=0.08, description="Probability of waiting")
    expected_queue_length: float = Field(..., example=0.1, description="Expected queue length")
    expected_waiting_time: float = Field(..., example=0.03, description="Expected waiting time in minutes")
    operating_cost_per_hour: float = Field(..., example=200.0, description="Operating cost per hour")
    feasible: bool = Field(..., example=True, description="Whether the configuration is feasible")
    message: str = Field(..., example="Optimal configuration found")

class OptimizationResponse(BaseModel):
    request: OptimizationRequest
    results: List[OptimizationResult]
    recommended: OptimizationResult


class TimeSeriesSlot(BaseModel):
    duration_minutes: int = Field(..., example=60, description="Length of the time slot in minutes")
    lambda_rate: float = Field(..., example=2.5, description="Arrival rate per minute during this slot")
    mu_rate: float = Field(..., example=3.0, description="Service rate per minute per counter during this slot")

class TimeSeriesOptimizationRequest(BaseModel):
    slots: List[TimeSeriesSlot] = Field(..., example=[{"duration_minutes": 60, "lambda_rate": 2.5, "mu_rate": 3.0}])
    min_counters: int = Field(..., example=1, description="Minimum allowed counters")
    max_counters: int = Field(..., example=5, description="Maximum allowed counters")
    cost_per_counter_per_hour: float = Field(..., example=100.0, description="Cost per counter per hour in INR")
    max_waiting_time: float = Field(..., example=5.0, description="Maximum acceptable expected waiting time in minutes")

class TimeSeriesOptimizationResult(BaseModel):
    slot_index: int = Field(..., example=0, description="Index of the time slot in the request")
    recommended_counters: int = Field(..., example=2)
    utilization: float = Field(..., example=0.42, description="Utilization at recommended counters")
    probability_wait: float = Field(..., example=0.08, description="Probability of waiting")
    expected_queue_length: float = Field(..., example=0.1, description="Expected queue length")
    expected_waiting_time: float = Field(..., example=0.03, description="Expected waiting time in minutes")
    operating_cost_per_hour: float = Field(..., example=200.0, description="Operating cost per hour")
    feasible: bool = Field(..., example=True, description="Whether the configuration is feasible")
    message: str = Field(..., example="Optimal configuration found for this slot")

class TimeSeriesOptimizationResponse(BaseModel):
    request: TimeSeriesOptimizationRequest
    results: List[TimeSeriesOptimizationResult]