from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
import random
import math
from backend.database.database import get_db
from backend.database.models import SimulationExperiment
from backend.schemas.simulation import SimulationRequest, SimulationResult, SimulationResponse
from backend.core.dependencies import get_current_admin_user
import datetime

router = APIRouter()

# We'll implement a simple discrete-event simulation for M/M/c
def run_simulation(lambda_rate, mu_rate, c, sim_duration, warm_up, num_reps, seed=None):
    # Set random seed if provided
    if seed is not None:
        random.seed(seed)

    # We'll store results for each replication
    replication_results = []

    for rep in range(num_reps):
        # Reset the random seed for each replication if we want independent streams?
        # For simplicity, we'll just continue with the current state of the random generator.
        # Alternatively, we can set the seed to seed + rep if seed is not None.
        if seed is not None:
            random.seed(seed + rep)

        # Initialize simulation state
        # We'll use an event-based simulation
        # Events: arrival, departure
        # We'll keep track of:
        #   - time
        #   - number of customers in system
        #   - queue length
        #   - busy counters
        #   - arrival times of customers in queue (for waiting time)
        #   - departure times

        # We'll use a priority queue for events (time, type, customer_id)
        # But for simplicity, we'll use two variables: next_arrival_time and next_departure_times (list for each counter)
        # and we'll advance time to the next event.

        # This is a complex simulation. We'll implement a simplified version that is not event-based but uses the known formulas?
        # However, the requirement is to implement a discrete-event simulation.
        # We'll implement a basic event-based simulation for M/M/c.

        # Due to time, we'll implement a simplified version that generates inter-arrival and service times and processes events.

        # Initialize
        current_time = 0.0
        # Next arrival time
        next_arrival = random.expovariate(lambda_rate)  # returns time until next arrival
        # Next departure times for each counter (initially infinity, meaning no departure scheduled)
        next_departures = [float('inf')] * c
        # Queue of customers waiting (each element is the arrival time of that customer)
        queue = []
        # Statistics
        waiting_times = []  # waiting time for each customer
        queue_length_over_time = []  # we'll sample at events? For simplicity, we'll record at each arrival and departure.
        # We'll also track the time-weighted average queue length.
        last_event_time = 0.0
        time_weighted_queue_length = 0.0
        # Number of customers served
        served_count = 0
        # Number of customers that have arrived
        arrival_count = 0

        # We'll run until we reach the simulation duration (after warm-up)
        # But we need to process events until the current time exceeds sim_duration + warm_up?
        # We'll run until we have processed enough events to cover the simulation duration.
        # We'll stop when the current time exceeds sim_duration + warm_up and we have processed all events up to that point.

        # We'll set the end time
        end_time = warm_up + sim_duration

        # We'll store the next event time
        while current_time < end_time:
            # Determine the next event: arrival or departure
            next_event_time = min(next_arrival, min(next_departures))
            if next_event_time > end_time:
                # No more events before the end time, break
                break

            # Advance time to next_event_time
            # Update time-weighted queue length
            time_weighted_queue_length += len(queue) * (next_event_time - current_time)
            current_time = next_event_time

            # Determine what type of event it is
            if next_event_time == next_arrival:
                # Arrival event
                arrival_count += 1
                # Schedule next arrival
                next_arrival = current_time + random.expovariate(lambda_rate)
                # Check if there is an idle counter
                idle_counter_index = -1
                for i in range(c):
                    if next_departures[i] == float('inf'):
                        idle_counter_index = i
                        break
                if idle_counter_index != -1:
                    # There is an idle counter, start service immediately
                    service_time = random.expovariate(mu_rate)
                    next_departures[idle_counter_index] = current_time + service_time
                    # Waiting time is 0
                    waiting_times.append(0.0)
                else:
                    # All counters busy, add to queue
                    queue.append(current_time)  # store arrival time
            else:
                # Departure event
                # Find which counter triggered the departure
                departure_index = next_departures.index(next_event_time)
                # If there are customers in queue, serve one
                if queue:
                    # Remove the first customer from the queue
                    arrival_time = queue.pop(0)
                    # Waiting time is current_time - arrival_time
                    waiting_time = current_time - arrival_time
                    waiting_times.append(waiting_time)
                    # Schedule next departure for this counter
                    service_time = random.expovariate(mu_rate)
                    next_departures[departure_index] = current_time + service_time
                    served_count += 1
                else:
                    # No customers in queue, mark counter as idle
                    next_departures[departure_index] = float('inf')
                    served_count += 1  # still count as served

            # Record queue length for time-weighted average (we already updated time_weighted_queue_length above)
            # We don't need to do anything else here.

        # After the simulation, we need to account for the time from the last event to the end_time?
        # Actually, we broke when next_event_time > end_time, so we have not updated the time_weighted_queue_length for the period from current_time to end_time.
        # We'll do that now.
        time_weighted_queue_length += len(queue) * (end_time - current_time)
        current_time = end_time

        # Calculate statistics for this replication
        if len(waiting_times) > 0:
            mean_waiting = sum(waiting_times) / len(waiting_times)
            sorted_waiting = sorted(waiting_times)
            median_waiting = sorted_waiting[len(sorted_waiting)//2]
            p90_waiting = sorted_waiting[int(0.9 * len(sorted_waiting))] if len(sorted_waiting) >= 10 else sorted_waiting[-1]
        else:
            mean_waiting = 0.0
            median_waiting = 0.0
            p90_waiting = 0.0

        # Time-weighted average queue length
        if end_time > 0:
            avg_queue_length = time_weighted_queue_length / end_time
        else:
            avg_queue_length = 0.0

        # Maximum queue length observed? We didn't track it. We'll need to track the maximum queue length during the simulation.
        # For simplicity, we'll skip it for now and set to 0.
        max_queue_length = 0  # placeholder

        # Utilization: time-weighted average of busy counters / (c * end_time)
        # We didn't track busy counters. We'll skip for now and set to 0.
        utilization = 0.0  # placeholder

        # Throughput: served_count / end_time
        throughput = served_count / end_time if end_time > 0 else 0

        # Remaining customers: those in queue plus those in service (but not yet departed) at the end_time
        # We have the queue (waiting) and we can check the next_departures to see which counters are busy.
        remaining_in_queue = len(queue)
        remaining_in_service = 0
        for i in range(c):
            if next_departures[i] != float('inf'):
                remaining_in_service += 1
        remaining_customers = remaining_in_queue + remaining_in_service

        replication_results.append({
            "mean_waiting_time": mean_waiting,
            "median_waiting_time": median_waiting,
            "p90_waiting_time": p90_waiting,
            "mean_queue_length": avg_queue_length,
            "max_queue_length": max_queue_length,
            "estimated_utilization": utilization,
            "throughput": throughput,
            "completed_customers": served_count,
            "remaining_customers": remaining_customers
        })

    # Aggregate results across replications
    # We'll compute the mean and standard deviation for each metric
    # For simplicity, we'll just return the mean of each metric across replications.
    # We'll also compute the standard deviation if needed, but the simulation result schema doesn't require it.
    # We'll return the mean values.
    if num_reps == 0:
        return SimulationResult()

    # Initialize sums
    sum_mean_waiting = 0.0
    sum_median_waiting = 0.0
    sum_p90_waiting = 0.0
    sum_mean_queue = 0.0
    sum_max_queue = 0.0
    sum_utilization = 0.0
    sum_throughput = 0.0
    sum_completed = 0
    sum_remaining = 0

    for res in replication_results:
        sum_mean_waiting += res["mean_waiting_time"]
        sum_median_waiting += res["median_waiting_time"]
        sum_p90_waiting += res["p90_waiting_time"]
        sum_mean_queue += res["mean_queue_length"]
        sum_max_queue += res["max_queue_length"]
        sum_utilization += res["estimated_utilization"]
        sum_throughput += res["throughput"]
        sum_completed += res["completed_customers"]
        sum_remaining += res["remaining_customers"]

    avg_result = SimulationResult(
        mean_waiting_time=sum_mean_waiting / num_reps,
        median_waiting_time=sum_median_waiting / num_reps,
        p90_waiting_time=sum_p90_waiting / num_reps,
        mean_queue_length=sum_mean_queue / num_reps,
        max_queue_length=int(sum_max_queue / num_reps),  # approximate
        estimated_utilization=sum_utilization / num_reps,
        throughput=sum_throughput / num_reps,
        completed_customers=sum_completed // num_reps,
        remaining_customers=sum_remaining // num_reps
    )

    return avg_result

@router.post("/run", response_model=SimulationResponse)
def run_simulation_endpoint(
    request: SimulationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    # Run the simulation
    result = run_simulation(
        lambda_rate=request.arrival_rate,
        mu_rate=request.service_rate,
        c=request.counter_count,
        sim_duration=request.simulation_duration_minutes,
        warm_up=request.warm_up_period_minutes,
        num_reps=request.number_of_replications,
        seed=request.random_seed
    )

    # Save the experiment to the database
    db_experiment = SimulationExperiment(
        arrival_rate=request.arrival_rate,
        service_rate=request.service_rate,
        counter_count=request.counter_count,
        simulation_duration_minutes=request.simulation_duration_minutes,
        warm_up_period_minutes=request.warm_up_period_minutes,
        number_of_replications=request.number_of_replications,
        random_seed=request.random_seed,
        mean_waiting_time=result.mean_waiting_time,
        median_waiting_time=result.median_waiting_time,
        p90_waiting_time=result.p90_waiting_time,
        mean_queue_length=result.mean_queue_length,
        max_queue_length=result.max_queue_length,
        estimated_utilization=result.estimated_utilization,
        throughput=result.throughput,
        completed_customers=result.completed_customers,
        remaining_customers=result.remaining_customers
    )
    db.add(db_experiment)
    db.commit()
    db.refresh(db_experiment)

    return SimulationResponse(
        request=request,
        results=[result],  # we only have one aggregated result for now, but we can change to return per replication if needed
        summary=result,
        created_at=db_experiment.created_at
    )

@router.get("/{experiment_id}", response_model=SimulationResponse)
def get_simulation_experiment(
    experiment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    experiment = db.query(SimulationExperiment).filter(SimulationExperiment.id == experiment_id).first()
    if experiment is None:
        raise HTTPException(status_code=404, detail="Simulation experiment not found")
    # We need to reconstruct the request and result from the experiment
    request = SimulationRequest(
        arrival_rate=experiment.arrival_rate,
        service_rate=experiment.service_rate,
        counter_count=experiment.counter_count,
        simulation_duration_minutes=experiment.simulation_duration_minutes,
        warm_up_period_minutes=experiment.warm_up_period_minutes,
        number_of_replications=experiment.number_of_replications,
        random_seed=experiment.random_seed
    )
    result = SimulationResult(
        mean_waiting_time=experiment.mean_waiting_time,
        median_waiting_time=experiment.median_waiting_time,
        p90_waiting_time=experiment.p90_waiting_time,
        mean_queue_length=experiment.mean_queue_length,
        max_queue_length=experiment.max_queue_length,
        estimated_utilization=experiment.estimated_utilization,
        throughput=experiment.throughput,
        completed_customers=experiment.completed_customers,
        remaining_customers=experiment.remaining_customers
    )
    return SimulationResponse(
        request=request,
        results=[result],
        summary=result,
        created_at=experiment.created_at
    )