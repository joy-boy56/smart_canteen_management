from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from backend.database.database import get_db
from backend.schemas.optimization import OptimizationRequest, OptimizationResult, OptimizationResponse, TimeSeriesOptimizationRequest, TimeSeriesOptimizationResult, TimeSeriesOptimizationResponse
from backend.core.dependencies import get_current_admin_user
import math
from datetime import datetime, timedelta

router = APIRouter()

# Reuse the MM/c calculation function from analytics (we'll define it here again or import, but for simplicity we'll define)
def calculate_mm_c_metrics(lambda_rate: float, mu_rate: float, c: int):
    if lambda_rate <= 0 or mu_rate <= 0 or c <= 0:
        return None
    a = lambda_rate / mu_rate
    rho = lambda_rate / (c * mu_rate)
    if rho >= 1:
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
    sum_terms = sum([(a**n) / math.factorial(n) for n in range(c)])
    last_term = (a**c) / (math.factorial(c) * (1 - rho))
    P0 = 1 / (sum_terms + last_term)
    Pw = (a**c) / (math.factorial(c) * (1 - rho)) * P0
    Lq = (P0 * (a**c) * rho) / (math.factorial(c) * ((1 - rho)**2))
    Wq = Lq / lambda_rate if lambda_rate > 0 else 0
    W = Wq + (1 / mu_rate)
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

@router.post("/run", response_model=OptimizationResponse)
def run_optimization(
    request: OptimizationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    lambda_rate = request.lambda_rate
    mu_rate = request.mu_rate
    min_counters = request.min_counters
    max_counters = request.max_counters
    cost_per_counter = request.cost_per_counter_per_hour
    max_waiting_time = request.max_waiting_time

    results = []
    feasible_results = []

    for c in range(min_counters, max_counters + 1):
        metrics = calculate_mm_c_metrics(lambda_rate, mu_rate, c)
        if metrics is None:
            continue
        stable = metrics.get("stable", False)
        utilization = metrics["utilization"]
        prob_wait = metrics["probability_wait"] or 0.0
        exp_queue_len = metrics["expected_queue_length"] or 0.0
        exp_wait_time = metrics["expected_waiting_time"] or 0.0
        operating_cost = cost_per_counter * c
        feasible = stable and (exp_wait_time <= max_waiting_time) and (utilization < 1)
        result = OptimizationResult(
            recommended_counters=c,
            utilization=utilization,
            probability_wait=prob_wait,
            expected_queue_length=exp_queue_len,
            expected_waiting_time=exp_wait_time,
            operating_cost_per_hour=operating_cost,
            feasible=feasible,
            message="Feasible" if feasible else "Not feasible due to waiting time or stability"
        )
        results.append(result)
        if feasible:
            feasible_results.append(result)

    if not feasible_results:
        # If no feasible configuration, we still return the results but mark the recommended as None?
        # We'll set the recommended to the one with the lowest waiting time that is stable?
        # But the requirement is to return an explicit result stating no feasible configuration.
        # We'll return the results and set recommended to None, but we need to return an OptimizationResponse.
        # We'll create a dummy recommended result with a message.
        recommended = OptimizationResult(
            recommended_counters=min_counters,  # just to fill
            utilization=0,
            probability_wait=0,
            expected_queue_length=0,
            expected_waiting_time=0,
            operating_cost_per_hour=0,
            feasible=False,
            message="No feasible configuration found under the given constraints"
        )
    else:
        # Sort feasible results by operating cost (ascending) and pick the first
        feasible_results.sort(key=lambda x: x.operating_cost_per_hour)
        recommended = feasible_results[0]

    return OptimizationResponse(
        request=request,
        results=results,
        recommended=recommended
    )

@router.post("/run-timeseries", response_model=TimeSeriesOptimizationResponse)
def run_time_series_optimization(
    request: TimeSeriesOptimizationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    results = []
    for idx, slot in enumerate(request.slots):
        lambda_rate = slot.lambda_rate
        mu_rate = slot.mu_rate
        min_c = request.min_counters
        max_c = request.max_counters
        cost_per_counter = request.cost_per_counter_per_hour
        max_waiting_time = request.max_waiting_time

        slot_results = []
        slot_feasible_results = []

        for c in range(min_c, max_c + 1):
            metrics = calculate_mm_c_metrics(lambda_rate, mu_rate, c)
            if metrics is None:
                continue
            stable = metrics.get("stable", False)
            utilization = metrics["utilization"]
            prob_wait = metrics["probability_wait"] or 0.0
            exp_queue_len = metrics["expected_queue_length"] or 0.0
            exp_wait_time = metrics["expected_waiting_time"] or 0.0
            operating_cost = cost_per_counter * c
            feasible = stable and (exp_wait_time <= max_waiting_time) and (utilization < 1)
            result = TimeSeriesOptimizationResult(
                slot_index=idx,
                recommended_counters=c,
                utilization=utilization,
                probability_wait=prob_wait,
                expected_queue_length=exp_queue_len,
                expected_waiting_time=exp_wait_time,
                operating_cost_per_hour=operating_cost,
                feasible=feasible,
                message="Feasible" if feasible else "Not feasible due to waiting time or stability"
            )
            slot_results.append(result)
            if feasible:
                slot_feasible_results.append(result)

        if not slot_feasible_results:
            # If no feasible configuration for this slot, we still return the results but mark the recommended as None?
            # We'll create a dummy recommended result for this slot.
            recommended_result = TimeSeriesOptimizationResult(
                slot_index=idx,
                recommended_counters=min_c,
                utilization=0,
                probability_wait=0,
                expected_queue_length=0,
                expected_waiting_time=0,
                operating_cost_per_hour=0,
                feasible=False,
                message="No feasible configuration found for this slot under the given constraints"
            )
        else:
            # Sort feasible results by operating cost (ascending) and pick the first
            slot_feasible_results.sort(key=lambda x: x.operating_cost_per_hour)
            recommended_result = slot_feasible_results[0]

        results.append(recommended_result)

    return TimeSeriesOptimizationResponse(
        request=request,
        results=results
    )