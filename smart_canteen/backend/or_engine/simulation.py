"""Discrete-Event Stochastic Simulation (DES) Engine for Smart Canteen.

Implements:
1. Reproducible fixed-seed M/M/c discrete-event simulation with shared FIFO queue.
2. Explicit tracking of arrivals, service starts, completions, time-weighted queue length,
   counter utilization, and unfinished jobs remaining in queue or in service at the horizon.
3. Slot-aware scheduled pickup + walk-in hybrid arrival generation.
4. Multi-replication statistical aggregation with 95% confidence intervals and
   Baseline vs. Optimized resource comparison.
"""

import heapq
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from backend.or_engine.queueing import calculate_mmc_metrics


def _generate_arrivals(
    rng: np.random.Generator,
    horizon_minutes: float,
    lambda_per_min: float,
    scheduled_fraction: float = 0.0,
    scheduled_jitter_sd_minutes: float = 2.0,
    slot_windows: Optional[List[Tuple[float, float, int]]] = None,
) -> List[Tuple[float, str]]:
    """Generate customer arrival timestamps within [0, horizon_minutes].

    Returns sorted list of (arrival_time_minutes, source_kind) where source_kind
    is 'WALK_IN_POISSON' or 'SCHEDULED_SLOT_PICKUP'.
    """
    arrivals: List[Tuple[float, str]] = []
    if lambda_per_min <= 0 and not slot_windows:
        return arrivals

    if slot_windows:
        # Explicit slot-based simulation with scheduled bookings + walk-in arrivals
        for start_m, end_m, booked_count in slot_windows:
            slot_len = max(1.0, end_m - start_m)
            for i in range(int(booked_count)):
                # Scheduled customer aims for a target point inside the slot window + Gaussian arrival jitter
                base_target = start_m + ((i + 0.5) / max(1, booked_count)) * slot_len
                jitter = float(rng.normal(0.0, scheduled_jitter_sd_minutes)) if scheduled_jitter_sd_minutes > 0 else 0.0
                arr_t = max(0.0, min(horizon_minutes - 1e-6, base_target + jitter))
                arrivals.append((arr_t, "SCHEDULED_SLOT_PICKUP"))

    walkin_lambda = lambda_per_min * (1.0 - max(0.0, min(1.0, scheduled_fraction if slot_windows else 0.0)))
    if walkin_lambda > 0:
        t = 0.0
        while True:
            inter = float(rng.exponential(1.0 / walkin_lambda))
            t += inter
            if t >= horizon_minutes:
                break
            arrivals.append((t, "WALK_IN_POISSON"))

    arrivals.sort(key=lambda x: x[0])
    return arrivals


def run_single_des_replication(
    seed: int,
    lambda_per_min: float,
    mu_per_min: float,
    counters: int,
    horizon_minutes: float = 60.0,
    warmup_minutes: float = 0.0,
    scheduled_fraction: float = 0.0,
    scheduled_jitter_sd_minutes: float = 2.0,
    slot_windows: Optional[List[Tuple[float, float, int]]] = None,
) -> Dict[str, Any]:
    """Run one discrete-event simulation replication with FIFO shared queue.

    All jobs arriving before `horizon_minutes` are tracked. Jobs still in service
    or waiting in the queue at `horizon_minutes` are explicitly accounted for
    (never silently discarded).
    """
    if counters < 1:
        raise ValueError("counters must be >= 1")
    if mu_per_min <= 0:
        raise ValueError("mu_per_min must be > 0")
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be > 0")

    rng = np.random.default_rng(int(seed))
    arrivals = _generate_arrivals(
        rng=rng,
        horizon_minutes=horizon_minutes,
        lambda_per_min=lambda_per_min,
        scheduled_fraction=scheduled_fraction,
        scheduled_jitter_sd_minutes=scheduled_jitter_sd_minutes,
        slot_windows=slot_windows,
    )

    n_arrivals = len(arrivals)
    service_durations = [
        float(rng.exponential(1.0 / mu_per_min)) for _ in range(n_arrivals)
    ]

    # Event-driven FIFO queue with c parallel servers
    # Each server tracks when it becomes free (up to horizon_minutes for area integration)
    server_free_time = [0.0] * counters
    heapq.heapify(server_free_time)

    job_records: List[Dict[str, Any]] = []
    # Events for time-weighted queue length L_q(t) and busy servers B(t) over [warmup, horizon]
    # Delta changes: (timestamp, delta_queue, delta_busy)
    state_deltas: List[Tuple[float, int, int]] = []

    for idx, (arr_t, source_kind) in enumerate(arrivals):
        srv_dur = service_durations[idx]
        earliest_free = heapq.heappop(server_free_time)
        start_t = max(arr_t, earliest_free)
        comp_t = start_t + srv_dur
        wait_t = start_t - arr_t
        heapq.heappush(server_free_time, comp_t)

        if wait_t > 0:
            state_deltas.append((arr_t, +1, 0))
            state_deltas.append((start_t, -1, +1))
        else:
            state_deltas.append((arr_t, 0, +1))
        state_deltas.append((comp_t, 0, -1))

        # Status at the simulation horizon
        if comp_t <= horizon_minutes:
            horizon_state = "COMPLETED_WITHIN_HORIZON"
            wait_by_horizon = wait_t
            busy_within_horizon = max(0.0, comp_t - max(start_t, warmup_minutes)) if comp_t > warmup_minutes else 0.0
        elif start_t <= horizon_minutes < comp_t:
            horizon_state = "IN_SERVICE_AT_HORIZON"
            wait_by_horizon = wait_t
            busy_within_horizon = max(0.0, horizon_minutes - max(start_t, warmup_minutes)) if horizon_minutes > warmup_minutes else 0.0
        else:
            horizon_state = "WAITING_IN_QUEUE_AT_HORIZON"
            wait_by_horizon = horizon_minutes - arr_t
            busy_within_horizon = 0.0

        job_records.append({
            "job_id": idx + 1,
            "source_kind": source_kind,
            "arrival_time": arr_t,
            "service_start_time": start_t,
            "service_completion_time": comp_t,
            "service_duration": srv_dur,
            "wait_duration_full": wait_t,
            "wait_duration_by_horizon": wait_by_horizon,
            "total_time_by_horizon": (min(comp_t, horizon_minutes) - arr_t),
            "horizon_state": horizon_state,
            "busy_within_horizon": busy_within_horizon,
            "post_warmup": arr_t >= warmup_minutes,
        })

    # Integrate exact time-weighted queue length L_q and busy servers over [warmup_minutes, horizon_minutes]
    eff_start = min(max(0.0, warmup_minutes), horizon_minutes)
    eff_duration = max(1e-9, horizon_minutes - eff_start)

    state_deltas.sort(key=lambda x: x[0])
    curr_q = 0
    curr_busy = 0
    last_t = 0.0
    area_q = 0.0
    area_busy = 0.0
    max_q_observed = 0

    # Sample timeline series (20 buckets across horizon)
    n_buckets = 20
    bucket_step = horizon_minutes / n_buckets
    bucket_points = [round((i + 1) * bucket_step, 2) for i in range(n_buckets)]
    bucket_q_values = [0] * n_buckets
    bucket_idx = 0

    for ev_t, dq, db in state_deltas:
        if ev_t > horizon_minutes:
            break
        while bucket_idx < n_buckets and bucket_points[bucket_idx] <= ev_t:
            bucket_q_values[bucket_idx] = curr_q
            bucket_idx += 1
        seg_start = max(last_t, eff_start)
        seg_end = max(ev_t, eff_start)
        if seg_end > seg_start:
            dt = seg_end - seg_start
            area_q += curr_q * dt
            area_busy += curr_busy * dt
        curr_q = max(0, curr_q + dq)
        curr_busy = max(0, min(counters, curr_busy + db))
        if curr_q > max_q_observed:
            max_q_observed = curr_q
        last_t = ev_t

    if horizon_minutes > max(last_t, eff_start):
        dt = horizon_minutes - max(last_t, eff_start)
        area_q += curr_q * dt
        area_busy += curr_busy * dt

    while bucket_idx < n_buckets:
        bucket_q_values[bucket_idx] = curr_q
        bucket_idx += 1

    # Filter post-warmup jobs for waiting time statistics (including unfinished jobs at horizon!)
    eval_jobs = [j for j in job_records if j["post_warmup"]]
    completed_jobs = [j for j in eval_jobs if j["horizon_state"] == "COMPLETED_WITHIN_HORIZON"]
    in_service_jobs = [j for j in eval_jobs if j["horizon_state"] == "IN_SERVICE_AT_HORIZON"]
    waiting_jobs = [j for j in eval_jobs if j["horizon_state"] == "WAITING_IN_QUEUE_AT_HORIZON"]

    all_waits_by_horizon = [j["wait_duration_by_horizon"] for j in eval_jobs]
    all_waits_full_schedule = [j["wait_duration_full"] for j in eval_jobs]
    all_totals_by_horizon = [j["total_time_by_horizon"] for j in eval_jobs]

    mean_wq_horizon = float(np.mean(all_waits_by_horizon)) if all_waits_by_horizon else 0.0
    mean_wq_full = float(np.mean(all_waits_full_schedule)) if all_waits_full_schedule else 0.0
    p95_wq = float(np.percentile(all_waits_by_horizon, 95)) if all_waits_by_horizon else 0.0
    prob_wait = (
        sum(1 for w in all_waits_by_horizon if w > 1e-9) / len(all_waits_by_horizon)
        if all_waits_by_horizon
        else 0.0
    )
    mean_w_total = float(np.mean(all_totals_by_horizon)) if all_totals_by_horizon else 0.0
    time_avg_lq = area_q / eff_duration
    utilization = area_busy / (counters * eff_duration)

    return {
        "seed": seed,
        "counters": counters,
        "total_arrivals": len(eval_jobs),
        "completed_count": len(completed_jobs),
        "in_service_at_horizon_count": len(in_service_jobs),
        "waiting_at_horizon_count": len(waiting_jobs),
        "unfinished_total_count": len(in_service_jobs) + len(waiting_jobs),
        "mean_wq_minutes": round(mean_wq_horizon, 5),
        "mean_wq_including_post_horizon_service_minutes": round(mean_wq_full, 5),
        "p95_wq_minutes": round(p95_wq, 5),
        "mean_w_total_minutes": round(mean_w_total, 5),
        "time_weighted_lq": round(time_avg_lq, 5),
        "max_queue_length": max_q_observed,
        "utilization_rho_sim": round(utilization, 5),
        "prob_waiting_pw_sim": round(prob_wait, 5),
        "timeline_minutes": bucket_points,
        "timeline_queue_length": bucket_q_values,
    }


def _aggregate_replications(rep_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute sample mean, SD, and 95% confidence intervals across replications."""
    n = len(rep_results)
    # 95% t-critical approximation (exact for common df, 1.96 for large n)
    t_crit_table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 9: 2.262, 14: 2.145, 19: 2.093, 29: 2.045}
    df = max(1, n - 1)
    t_val = t_crit_table.get(df, 2.086 if df <= 20 else 1.96)

    def summarize_metric(key: str) -> Dict[str, float]:
        vals = [float(r[key]) for r in rep_results]
        mean = float(np.mean(vals))
        sd = float(np.std(vals, ddof=1)) if n > 1 else 0.0
        half_width = (t_val * sd / math.sqrt(n)) if n > 1 else 0.0
        return {
            "mean": round(mean, 4),
            "std_dev": round(sd, 4),
            "ci95_low": round(max(0.0, mean - half_width), 4),
            "ci95_high": round(mean + half_width, 4),
        }

    avg_timeline_q = np.mean([r["timeline_queue_length"] for r in rep_results], axis=0).tolist()

    return {
        "replications": n,
        "wq_wait_minutes": summarize_metric("mean_wq_minutes"),
        "wq_full_schedule_minutes": summarize_metric("mean_wq_including_post_horizon_service_minutes"),
        "p95_wq_minutes": summarize_metric("p95_wq_minutes"),
        "w_total_minutes": summarize_metric("mean_w_total_minutes"),
        "lq_queue_length": summarize_metric("time_weighted_lq"),
        "utilization_rho": summarize_metric("utilization_rho_sim"),
        "prob_waiting_pw": summarize_metric("prob_waiting_pw_sim"),
        "arrivals_count": summarize_metric("total_arrivals"),
        "completed_count": summarize_metric("completed_count"),
        "unfinished_at_horizon_count": summarize_metric("unfinished_total_count"),
        "in_service_at_horizon_count": summarize_metric("in_service_at_horizon_count"),
        "waiting_at_horizon_count": summarize_metric("waiting_at_horizon_count"),
        "timeline_minutes": rep_results[0]["timeline_minutes"] if rep_results else [],
        "avg_timeline_queue_length": [round(float(x), 3) for x in avg_timeline_q],
    }


def run_multi_replication_comparison(
    random_seed: int,
    replications: int,
    lambda_per_min: float,
    mu_per_min: float,
    baseline_counters: int,
    optimized_counters: int,
    horizon_minutes: float = 60.0,
    warmup_minutes: float = 5.0,
    scheduled_pickup_fraction: float = 0.0,
    scheduled_jitter_sd_minutes: float = 2.0,
    slot_configs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run multiple independent DES replications comparing Baseline vs Optimized counters."""
    slot_windows: Optional[List[Tuple[float, float, int]]] = None
    if slot_configs:
        slot_windows = []
        cursor = 0.0
        for sc in slot_configs:
            dur = float(sc.get("duration_minutes", 15.0))
            b_cnt = int(sc.get("booked_orders", round(lambda_per_min * dur * scheduled_pickup_fraction)))
            if cursor < horizon_minutes:
                slot_windows.append((cursor, min(horizon_minutes, cursor + dur), b_cnt))
            cursor += dur

    base_reps = []
    opt_reps = []
    for r in range(int(replications)):
        rep_seed = int(random_seed) + r * 10007
        base_reps.append(
            run_single_des_replication(
                seed=rep_seed,
                lambda_per_min=lambda_per_min,
                mu_per_min=mu_per_min,
                counters=baseline_counters,
                horizon_minutes=horizon_minutes,
                warmup_minutes=warmup_minutes,
                scheduled_fraction=scheduled_pickup_fraction,
                scheduled_jitter_sd_minutes=scheduled_jitter_sd_minutes,
                slot_windows=slot_windows,
            )
        )
        opt_reps.append(
            run_single_des_replication(
                seed=rep_seed,
                lambda_per_min=lambda_per_min,
                mu_per_min=mu_per_min,
                counters=optimized_counters,
                horizon_minutes=horizon_minutes,
                warmup_minutes=warmup_minutes,
                scheduled_fraction=scheduled_pickup_fraction,
                scheduled_jitter_sd_minutes=scheduled_jitter_sd_minutes,
                slot_windows=slot_windows,
            )
        )

    base_summary = _aggregate_replications(base_reps)
    opt_summary = _aggregate_replications(opt_reps)

    base_analytical = calculate_mmc_metrics(lambda_per_min, mu_per_min, baseline_counters)
    opt_analytical = calculate_mmc_metrics(lambda_per_min, mu_per_min, optimized_counters)

    return {
        "provenance": "STOCHASTIC_DES_SIMULATION_ESTIMATE",
        "parameters": {
            "random_seed": int(random_seed),
            "replications": int(replications),
            "lambda_per_min": lambda_per_min,
            "mu_per_min": mu_per_min,
            "baseline_counters": baseline_counters,
            "optimized_counters": optimized_counters,
            "horizon_minutes": horizon_minutes,
            "warmup_minutes": warmup_minutes,
            "scheduled_pickup_fraction": scheduled_pickup_fraction,
            "scheduled_jitter_sd_minutes": scheduled_jitter_sd_minutes,
        },
        "baseline": {
            "counters": baseline_counters,
            "simulation": base_summary,
            "analytical_mmc": base_analytical,
        },
        "optimized": {
            "counters": optimized_counters,
            "simulation": opt_summary,
            "analytical_mmc": opt_analytical,
        },
        "comparison": {
            "simulated_wait_reduction_minutes": round(
                base_summary["wq_wait_minutes"]["mean"] - opt_summary["wq_wait_minutes"]["mean"], 4
            ),
            "simulated_queue_length_reduction": round(
                base_summary["lq_queue_length"]["mean"] - opt_summary["lq_queue_length"]["mean"], 4
            ),
            "simulated_unfinished_jobs_reduction": round(
                base_summary["unfinished_at_horizon_count"]["mean"]
                - opt_summary["unfinished_at_horizon_count"]["mean"],
                4,
            ),
        },
        "assumptions": [
            "FIFO shared queue across active counters.",
            "Exponential service durations with rate mu per counter.",
            (
                f"Hybrid arrival process: {scheduled_pickup_fraction*100:.0f}% scheduled slot pickups "
                f"(Gaussian jitter SD={scheduled_jitter_sd_minutes}m) + {(1-scheduled_pickup_fraction)*100:.0f}% Poisson walk-ins."
                if slot_windows
                else "Poisson arrival process with exponential inter-arrival times (rate lambda)."
            ),
            "Unfinished jobs (waiting or in service at the horizon) are explicitly counted and included in horizon-bounded wait statistics.",
        ],
    }
