"""Operations Research & Queueing Theory Engine for Smart Canteen.

Implements:
1. Arrival rate (lambda) and service rate (mu) estimation from observations.
2. Multi-server M/M/c (Erlang-C) steady-state analytical model with strict stability checks.
3. Time-slot-aware effective capacity calculation (K_s and K_s^effective) and remaining capacity (R_s).
4. Explainable integer counter/resource optimization across time slots using Dynamic Programming /
   exact integer search over feasible allocations.
"""

import math
from typing import Any, Dict, List, Optional, Tuple


METRIC_TOOLTIPS: Dict[str, str] = {
    "utilization_rho": "Proportion of total modeled counter service capacity used (lambda / (c * mu)). Must be < 1 for steady-state stability.",
    "p0_empty": "Steady-state probability that zero customers are in the system (all counters idle).",
    "pw_erlang_c": "Erlang-C probability that an arriving customer finds all c counters busy and must wait in the queue.",
    "lq_queue_length": "Expected number of customers waiting in the queue, excluding those currently being served.",
    "wq_wait_time_minutes": "Expected waiting time in the queue (minutes), excluding counter service time.",
    "l_system_length": "Expected total number of customers in the system (waiting in queue plus in service).",
    "w_total_time_minutes": "Expected total time in the system (minutes), equal to queue waiting time plus service time.",
    "ks_theoretical_capacity": "Planning approximation of slot service capacity floor(alpha_s * c_s * mu_s * Delta_s).",
    "ks_effective_capacity": "Effective slot booking limit min(K_s, Admin Cap).",
}


def estimate_rates_from_observations(
    observations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Estimate arrival rate (lambda) and per-counter service rate (mu) from observations.

    Formulae:
        lambda = (total observed arrivals) / (total observation duration in minutes)
        mu = 1.0 / (weighted mean service time in minutes)
    """
    if not observations:
        return {
            "has_sufficient_data": False,
            "sample_count": 0,
            "lambda_per_min": None,
            "lambda_per_hour": None,
            "mu_per_min": None,
            "mu_per_hour": None,
            "mean_service_time_minutes": None,
            "notes": "No observation records available. Enter or import observations to estimate empirical rates.",
        }

    total_arrivals = 0
    total_duration_min = 0.0
    weighted_service_sum = 0.0
    total_served_weight = 0
    event_types = set()

    for obs in observations:
        dur = float(obs.get("duration_minutes") or 0.0)
        arr = int(obs.get("arrivals_count") or 0)
        srv = int(obs.get("served_count") or 0)
        mst = float(obs.get("mean_service_time_minutes") or 0.0)
        if dur <= 0 or mst <= 0 or arr < 0 or srv < 0:
            raise ValueError("Invalid observation values: duration and mean_service_time must be > 0, counts >= 0.")
        total_arrivals += arr
        total_duration_min += dur
        weight = max(1, srv)
        weighted_service_sum += mst * weight
        total_served_weight += weight
        if obs.get("event_type_measured"):
            event_types.add(str(obs["event_type_measured"]))

    if total_duration_min <= 0 or total_served_weight <= 0:
        return {
            "has_sufficient_data": False,
            "sample_count": len(observations),
            "lambda_per_min": None,
            "lambda_per_hour": None,
            "mu_per_min": None,
            "mu_per_hour": None,
            "mean_service_time_minutes": None,
            "notes": "Insufficient duration or service weight in observations.",
        }

    lambda_per_min = total_arrivals / total_duration_min
    mean_service_time = weighted_service_sum / total_served_weight
    mu_per_min = 1.0 / mean_service_time

    return {
        "has_sufficient_data": True,
        "sample_count": len(observations),
        "total_arrivals": total_arrivals,
        "total_duration_minutes": round(total_duration_min, 2),
        "lambda_per_min": round(lambda_per_min, 6),
        "lambda_per_hour": round(lambda_per_min * 60.0, 4),
        "mean_service_time_minutes": round(mean_service_time, 4),
        "mu_per_min": round(mu_per_min, 6),
        "mu_per_hour": round(mu_per_min * 60.0, 4),
        "event_types_included": sorted(event_types),
        "provenance": "EMPIRICAL_OBSERVATION_ESTIMATE",
        "notes": "Rates estimated from recorded operational observations (time unit: minutes and hours).",
    }


def calculate_mmc_metrics(
    lambda_rate: float,
    mu_rate: float,
    counters: int,
) -> Dict[str, Any]:
    """Calculate exact analytical M/M/c queueing metrics (Erlang-C model).

    Args:
        lambda_rate: Arrival rate per time unit (e.g. customers/minute). Must be >= 0.
        mu_rate: Service rate per counter per same time unit (e.g. customers/minute). Must be > 0.
        counters: Number of active equivalent parallel servers c. Must be integer >= 1.

    Returns:
        Dictionary of analytical predictions and stability status. Never reports finite
        steady-state queue waits when rho >= 1.
    """
    if lambda_rate is None or mu_rate is None or counters is None:
        raise ValueError("lambda_rate, mu_rate, and counters must not be None.")
    if lambda_rate < 0:
        raise ValueError("Arrival rate lambda must be non-negative (>= 0).")
    if mu_rate <= 0:
        raise ValueError("Service rate mu must be strictly positive (> 0).")
    if not isinstance(counters, int) or counters < 1:
        raise ValueError("Number of counters c must be an integer >= 1.")

    c = int(counters)
    a = float(lambda_rate) / float(mu_rate)
    rho = float(lambda_rate) / (c * float(mu_rate))

    if lambda_rate == 0:
        return {
            "lambda_rate": 0.0,
            "mu_rate": round(float(mu_rate), 6),
            "counters": c,
            "offered_load_a": 0.0,
            "utilization_rho": 0.0,
            "is_stable": True,
            "stability_status": "STABLE_IDLE",
            "p0_empty": 1.0,
            "pw_erlang_c": 0.0,
            "lq_queue_length": 0.0,
            "wq_wait_time": 0.0,
            "l_system_length": 0.0,
            "w_total_time": round(1.0 / float(mu_rate), 6),
            "provenance": "ANALYTICAL_MMC_PREDICTION",
            "tooltips": METRIC_TOOLTIPS,
            "disclaimer": "Analytical M/M/c model estimate assuming Poisson arrivals and exponential service times.",
        }

    if rho >= 1.0:
        min_stable_c = int(math.floor(a) + 1)
        return {
            "lambda_rate": round(float(lambda_rate), 6),
            "mu_rate": round(float(mu_rate), 6),
            "counters": c,
            "offered_load_a": round(a, 6),
            "utilization_rho": round(rho, 6),
            "is_stable": False,
            "stability_status": "UNSTABLE_OVERLOADED",
            "min_counters_for_stability": min_stable_c,
            "p0_empty": None,
            "pw_erlang_c": None,
            "lq_queue_length": None,
            "wq_wait_time": None,
            "l_system_length": None,
            "w_total_time": None,
            "provenance": "ANALYTICAL_MMC_PREDICTION",
            "tooltips": METRIC_TOOLTIPS,
            "disclaimer": (
                f"System is UNSTABLE (rho = {rho:.3f} >= 1.0). Arrival rate exceeds total service capacity; "
                f"queue grows without bound in steady state. At least {min_stable_c} counters are required for stability."
            ),
        }

    # Stable case: 0 < rho < 1
    sum_terms = 0.0
    for n in range(c):
        sum_terms += (a ** n) / math.factorial(n)
    tail_term = ((a ** c) / (math.factorial(c) * (1.0 - rho)))
    p0 = 1.0 / (sum_terms + tail_term)
    pw = tail_term * p0
    lq = (p0 * (a ** c) * rho) / (math.factorial(c) * ((1.0 - rho) ** 2))
    wq = lq / float(lambda_rate)
    l_sys = lq + a
    w_sys = wq + (1.0 / float(mu_rate))

    return {
        "lambda_rate": round(float(lambda_rate), 6),
        "mu_rate": round(float(mu_rate), 6),
        "counters": c,
        "offered_load_a": round(a, 6),
        "utilization_rho": round(rho, 6),
        "is_stable": True,
        "stability_status": "STABLE",
        "min_counters_for_stability": int(math.floor(a) + 1),
        "p0_empty": round(p0, 6),
        "pw_erlang_c": round(pw, 6),
        "lq_queue_length": round(lq, 6),
        "wq_wait_time": round(wq, 6),
        "l_system_length": round(l_sys, 6),
        "w_total_time": round(w_sys, 6),
        "provenance": "ANALYTICAL_MMC_PREDICTION",
        "tooltips": METRIC_TOOLTIPS,
        "disclaimer": "Analytical M/M/c model estimate (not a direct field measurement or guaranteed preparation time).",
    }


def calculate_slot_capacity(
    allocated_counters: int,
    service_rate_per_counter_per_min: float,
    duration_minutes: float,
    safety_factor: float = 0.85,
    admin_cap: Optional[int] = None,
    active_bookings_count: int = 0,
    active_workload_units: Optional[float] = None,
) -> Dict[str, Any]:
    """Calculate time-slot-specific planning capacity K_s, effective capacity, and remaining availability R_s.

    Formulae:
        K_s = floor(alpha_s * c_s * mu_s * Delta_s)
        K_s^effective = min(K_s, K_s^admin_cap)
        R_s = max(0, K_s^effective - B_s)
    """
    if allocated_counters < 1:
        raise ValueError("allocated_counters must be >= 1")
    if service_rate_per_counter_per_min <= 0:
        raise ValueError("service_rate_per_counter_per_min must be > 0")
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be > 0")
    if safety_factor <= 0 or safety_factor > 1.0:
        raise ValueError("safety_factor alpha_s must be in (0, 1.0]")

    raw_capacity = safety_factor * allocated_counters * service_rate_per_counter_per_min * duration_minutes
    k_s = int(math.floor(raw_capacity + 1e-9))

    if admin_cap is not None and admin_cap >= 0:
        k_effective = min(k_s, int(admin_cap))
    else:
        k_effective = k_s

    b_s = int(active_bookings_count)
    workload_used = float(active_workload_units) if active_workload_units is not None else float(b_s)
    # Effective consumed slots accounts for higher-workload items if workload_units > b_s
    consumed_capacity = max(b_s, int(math.ceil(workload_used - 1e-9)))
    remaining = max(0, k_effective - consumed_capacity)

    if remaining == 0:
        availability_state = "FULL"
    elif remaining <= max(2, int(math.ceil(0.2 * k_effective))):
        availability_state = "LIMITED"
    else:
        availability_state = "AVAILABLE"

    return {
        "theoretical_capacity_ks": k_s,
        "admin_cap": admin_cap,
        "effective_capacity": k_effective,
        "active_bookings_bs": b_s,
        "active_workload_units": round(workload_used, 2),
        "consumed_capacity": consumed_capacity,
        "remaining_capacity_rs": remaining,
        "availability_state": availability_state,
        "safety_factor_alpha": safety_factor,
        "allocated_counters_cs": allocated_counters,
        "service_rate_mu_per_min": service_rate_per_counter_per_min,
        "duration_minutes_delta": duration_minutes,
        "note": (
            "K_s = floor(alpha_s * c_s * mu_s * Delta_s) is a planning approximation; "
            "effective capacity respects both K_s and the administrator cap."
        ),
    }


def optimize_counters_across_slots(
    slots_data: List[Dict[str, Any]],
    total_available_counters: int,
    min_counters_per_slot: int = 1,
    max_counters_per_slot: int = 6,
    counter_cost_per_slot_inr: float = 150.0,
    waiting_cost_per_customer_minute_inr: float = 12.0,
    max_acceptable_wait_minutes: float = 6.0,
    objective_mode: str = "WEIGHTED_COST",
) -> Dict[str, Any]:
    """Optimize integer counter allocations c_s across time slots.

    Subject to:
        sum_s c_s <= C_available
        c_min <= c_s <= c_max  (c_s integer)

    Evaluates each slot's expected arrival demand D_s = lambda_s * Delta_s,
    M/M/c stability (rho_s < 1), waiting time constraint (W_{q,s} <= W_max),
    and slot booking capacity requirement (K_s >= current_bookings).
    Uses exact dynamic programming over integer allocations.
    """
    if not slots_data:
        return {
            "status": "NO_SLOTS",
            "slots": [],
            "summary": {},
        }
    n_slots = len(slots_data)
    c_min = max(1, int(min_counters_per_slot))
    c_max = max(c_min, int(max_counters_per_slot))
    budget = int(total_available_counters)

    if budget < n_slots * c_min:
        raise ValueError(
            f"Total counter budget ({budget}) is less than minimum required ({n_slots * c_min}) "
            f"for {n_slots} active slots with min_counters_per_slot={c_min}."
        )

    # Precompute candidate evaluation for each slot s and counter count c in [c_min, c_max]
    slot_candidates: List[Dict[int, Dict[str, Any]]] = []
    for s_idx, slot in enumerate(slots_data):
        lam = float(slot.get("lambda_per_min", 0.0))
        mu = float(slot.get("mu_per_min", 0.60))
        delta = float(slot.get("duration_minutes", 15.0))
        alpha = float(slot.get("safety_factor", 0.85))
        admin_cap = int(slot.get("admin_cap", 20))
        current_bookings = int(slot.get("active_bookings", 0))
        demand_customers = lam * delta

        candidates_for_s: Dict[int, Dict[str, Any]] = {}
        for c in range(c_min, c_max + 1):
            mmc = calculate_mmc_metrics(lam, mu, c)
            cap = calculate_slot_capacity(
                allocated_counters=c,
                service_rate_per_counter_per_min=mu,
                duration_minutes=delta,
                safety_factor=alpha,
                admin_cap=admin_cap,
                active_bookings_count=current_bookings,
            )
            staff_cost = counter_cost_per_slot_inr * c
            is_stable = bool(mmc["is_stable"])
            wq = mmc["wq_wait_time"] if is_stable and mmc["wq_wait_time"] is not None else 999.0
            wait_ok = is_stable and (wq <= max_acceptable_wait_minutes)
            cap_ok = cap["theoretical_capacity_ks"] >= current_bookings

            waiting_cost = demand_customers * waiting_cost_per_customer_minute_inr * (wq if is_stable else 60.0)
            # Penalty for violating stability, wait target, or existing bookings capacity
            penalty = 0.0
            if not is_stable:
                penalty += 1e6 + (mmc["utilization_rho"] - 1.0) * 5e5
            if not wait_ok and is_stable:
                penalty += 5e4 * (wq - max_acceptable_wait_minutes)
            if not cap_ok:
                penalty += 1e5 * (current_bookings - cap["theoretical_capacity_ks"])

            if objective_mode == "MIN_STAFF_UNDER_WAIT_TARGET":
                score = staff_cost + penalty + 0.01 * waiting_cost
            else:
                score = waiting_cost + staff_cost + penalty

            candidates_for_s[c] = {
                "c": c,
                "mmc": mmc,
                "cap": cap,
                "demand_customers": round(demand_customers, 2),
                "staff_cost_inr": round(staff_cost, 2),
                "waiting_cost_inr": round(waiting_cost, 2) if is_stable else None,
                "total_economic_cost_inr": round(staff_cost + waiting_cost, 2) if is_stable else None,
                "is_stable": is_stable,
                "wait_target_met": wait_ok,
                "capacity_covers_bookings": cap_ok,
                "all_constraints_met": bool(is_stable and wait_ok and cap_ok),
                "objective_score": score,
            }
        slot_candidates.append(candidates_for_s)

    # Dynamic Programming over slots (0..n_slots-1) and used counters (0..budget)
    # dp[s][used] = (best_score, chosen_c, prev_used)
    INF = float("inf")
    dp: List[Dict[int, Tuple[float, int, int]]] = [{} for _ in range(n_slots)]

    for c in range(c_min, min(c_max, budget) + 1):
        dp[0][c] = (slot_candidates[0][c]["objective_score"], c, 0)

    for s in range(1, n_slots):
        for prev_used, (prev_score, _, _) in dp[s - 1].items():
            for c in range(c_min, c_max + 1):
                new_used = prev_used + c
                if new_used > budget:
                    continue
                cand_score = prev_score + slot_candidates[s][c]["objective_score"]
                if new_used not in dp[s] or cand_score < dp[s][new_used][0]:
                    dp[s][new_used] = (cand_score, c, prev_used)

    # Find best total allocation at last slot
    best_total_used = min(dp[n_slots - 1].keys(), key=lambda u: dp[n_slots - 1][u][0])
    chosen_allocations = [0] * n_slots
    curr_u = best_total_used
    for s in range(n_slots - 1, -1, -1):
        _, chosen_c, prev_u = dp[s][curr_u]
        chosen_allocations[s] = chosen_c
        curr_u = prev_u

    # Build detailed slot comparison rows
    slot_results: List[Dict[str, Any]] = []
    baseline_total_counters = 0
    optimized_total_counters = 0
    baseline_total_cost = 0.0
    optimized_total_cost = 0.0
    baseline_weighted_wait = 0.0
    optimized_weighted_wait = 0.0
    total_demand_customers = 0.0

    for s_idx, slot in enumerate(slots_data):
        base_c = max(1, int(slot.get("allocated_counters", 2)))
        opt_c = chosen_allocations[s_idx]
        lam = float(slot.get("lambda_per_min", 0.0))
        mu = float(slot.get("mu_per_min", 0.60))
        delta = float(slot.get("duration_minutes", 15.0))
        alpha = float(slot.get("safety_factor", 0.85))
        admin_cap = int(slot.get("admin_cap", 20))
        current_bookings = int(slot.get("active_bookings", 0))
        pending_holds = int(slot.get("pending_holds", 0))

        # Evaluate baseline c (even if outside [c_min, c_max])
        base_mmc = calculate_mmc_metrics(lam, mu, base_c)
        base_cap = calculate_slot_capacity(base_c, mu, delta, alpha, admin_cap, current_bookings)
        base_staff_cost = counter_cost_per_slot_inr * base_c
        base_wq = base_mmc["wq_wait_time"] if base_mmc["is_stable"] and base_mmc["wq_wait_time"] is not None else None
        base_wait_cost = (lam * delta * waiting_cost_per_customer_minute_inr * base_wq) if base_wq is not None else None

        opt_eval = slot_candidates[s_idx][opt_c]
        opt_mmc = opt_eval["mmc"]
        opt_cap = opt_eval["cap"]
        opt_wq = opt_mmc["wq_wait_time"]

        # Explainable recommendation rationale
        reasons = []
        if not base_mmc["is_stable"] and opt_mmc["is_stable"]:
            reasons.append(
                f"Increased counters from {base_c} to {opt_c} to resolve queue instability "
                f"(baseline rho={base_mmc['utilization_rho']:.2f} >= 1.0 -> optimized rho={opt_mmc['utilization_rho']:.2f})."
            )
        elif base_wq is not None and opt_wq is not None and base_wq > max_acceptable_wait_minutes >= opt_wq:
            reasons.append(
                f"Increased counters from {base_c} to {opt_c} to bring expected queue wait ({base_wq:.2f}m -> {opt_wq:.2f}m) "
                f"within the {max_acceptable_wait_minutes:.1f}m target."
            )
        elif opt_c > base_c:
            reasons.append(
                f"Allocated +{opt_c - base_c} counter(s) because waiting-cost reduction outweighed staffing cost "
                f"under {lam * delta:.1f} expected arrivals."
            )
        elif opt_c < base_c:
            reasons.append(
                f"Reduced counters from {base_c} to {opt_c} to save ₹{(base_c - opt_c) * counter_cost_per_slot_inr:.0f} "
                f"while keeping wait at {opt_wq:.2f}m (<= {max_acceptable_wait_minutes:.1f}m target)."
            )
        else:
            reasons.append(
                f"Retained {opt_c} counter(s); current allocation already balances wait ({opt_wq if opt_wq is not None else 'N/A'}m) and operating cost."
            )

        if not opt_eval["all_constraints_met"]:
            reasons.append(
                "WARNING: Constrained by counter budget/bounds; consider raising total counter budget or slot cap."
            )

        baseline_total_counters += base_c
        optimized_total_counters += opt_c
        baseline_total_cost += base_staff_cost + (base_wait_cost or 0.0)
        optimized_total_cost += opt_eval["total_economic_cost_inr"] or opt_eval["staff_cost_inr"]
        demand_cust = lam * delta
        total_demand_customers += demand_cust
        if base_wq is not None:
            baseline_weighted_wait += demand_cust * base_wq
        if opt_wq is not None:
            optimized_weighted_wait += demand_cust * opt_wq

        slot_results.append({
            "slot_id": slot.get("slot_id"),
            "slot_label": slot.get("label"),
            "start_time": slot.get("start_time"),
            "end_time": slot.get("end_time"),
            "duration_minutes": delta,
            "demand_source_used": slot.get("demand_source_used", "HYBRID"),
            "observed_arrivals_rate_per_min": slot.get("observed_lambda_per_min"),
            "forecast_lambda_per_min": round(lam, 4),
            "forecast_demand_customers": round(demand_cust, 2),
            "service_rate_mu_per_min": round(mu, 4),
            "current_bookings": current_bookings,
            "pending_reservations": pending_holds,
            "baseline": {
                "counters": base_c,
                "effective_capacity": base_cap["effective_capacity"],
                "remaining_capacity": base_cap["remaining_capacity_rs"],
                "utilization_rho": base_mmc["utilization_rho"],
                "is_stable": base_mmc["is_stable"],
                "wq_wait_minutes": base_wq,
                "lq_queue_length": base_mmc["lq_queue_length"],
                "staff_cost_inr": round(base_staff_cost, 2),
                "waiting_cost_inr": round(base_wait_cost, 2) if base_wait_cost is not None else None,
            },
            "recommended": {
                "counters": opt_c,
                "theoretical_capacity_ks": opt_cap["theoretical_capacity_ks"],
                "effective_capacity": opt_cap["effective_capacity"],
                "remaining_capacity": opt_cap["remaining_capacity_rs"],
                "utilization_rho": opt_mmc["utilization_rho"],
                "is_stable": opt_mmc["is_stable"],
                "wq_wait_minutes": opt_wq,
                "lq_queue_length": opt_mmc["lq_queue_length"],
                "staff_cost_inr": opt_eval["staff_cost_inr"],
                "waiting_cost_inr": opt_eval["waiting_cost_inr"],
                "total_economic_cost_inr": opt_eval["total_economic_cost_inr"],
                "constraint_status": "FEASIBLE" if opt_eval["all_constraints_met"] else "CONSTRAINT_WARNING",
            },
            "explanation": " ".join(reasons),
        })

    avg_base_wait = (baseline_weighted_wait / total_demand_customers) if total_demand_customers > 0 else 0.0
    avg_opt_wait = (optimized_weighted_wait / total_demand_customers) if total_demand_customers > 0 else 0.0

    return {
        "status": "OPTIMIZED",
        "method": "EXACT_INTEGER_DYNAMIC_PROGRAMMING",
        "objective_mode": objective_mode,
        "constraints": {
            "total_available_counters_budget": budget,
            "min_counters_per_slot": c_min,
            "max_counters_per_slot": c_max,
            "max_acceptable_wait_minutes": max_acceptable_wait_minutes,
            "counter_cost_per_slot_inr": counter_cost_per_slot_inr,
            "waiting_cost_per_customer_minute_inr": waiting_cost_per_customer_minute_inr,
        },
        "summary": {
            "slots_evaluated": n_slots,
            "baseline_total_counter_slots": baseline_total_counters,
            "recommended_total_counter_slots": optimized_total_counters,
            "counter_budget_available": budget,
            "total_forecast_demand_customers": round(total_demand_customers, 2),
            "baseline_weighted_avg_wait_minutes": round(avg_base_wait, 3),
            "recommended_weighted_avg_wait_minutes": round(avg_opt_wait, 3),
            "baseline_total_economic_cost_inr": round(baseline_total_cost, 2),
            "recommended_total_economic_cost_inr": round(optimized_total_cost, 2),
            "estimated_net_cost_savings_inr": round(baseline_total_cost - optimized_total_cost, 2),
            "existing_bookings_reassigned": 0,
            "policy_note": (
                "Existing confirmed student bookings are strictly preserved and never automatically reassigned. "
                "Recommendations adjust counter staffing and effective slot capacity."
            ),
        },
        "slots": slot_results,
    }
