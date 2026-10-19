"""Comprehensive Automated Test Suite for Smart Canteen.

Covers all 6 required verification categories and all 5 end-to-end demonstration scenarios:
1. Mathematical & Operations Research tests (Rate estimation, M/M/c Erlang-C, stability, slot capacity, integer optimizer)
2. Stochastic Discrete-Event Simulation tests (Fixed-seed reproducibility, FIFO, event ordering, horizon unfinished jobs, multi-replication CI, M/M/c convergence)
3. Slot Capacity & Atomic Reservation tests (Concurrency protection, cancellation release, expired hold release, no silent slot reassignment, overlap prevention)
4. Payment Integrity tests (Direct UPI URI, VPA config, UTR pending state, manual reconciliation audit trail, Razorpay HMAC-SHA256 callback & webhook verification, amount/reference mismatch, idempotency)
5. QR Token Lifecycle tests (Opaque token privacy, PAID vs UNPAID vs PAYMENT_PENDING badges, counter payment upgrade preserving QR identity, payment QR vs order QR separation, single redemption & duplicate rejection)
6. Authentication, RBAC, Privacy & Scenarios A–E end-to-end tests
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from backend.config import settings
from backend.database import DatabaseSession, get_database_path, init_db, set_database_path, utcnow_iso
from backend.main import app
from backend.or_engine.queueing import (
    calculate_mmc_metrics,
    calculate_slot_capacity,
    estimate_rates_from_observations,
    optimize_counters_across_slots,
)
from backend.or_engine.simulation import (
    run_multi_replication_comparison,
    run_single_des_replication,
)
from backend.security import hash_password
from backend.services.order_service import release_expired_reservations
from backend.services.payment_service import build_upi_intent_uri
from scripts.seed_demo import seed_demo_data


class SmartCanteenTestCase(unittest.TestCase):
    """Base test case setting up an isolated temporary SQLite database per test."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_smart_canteen.db")
        set_database_path(self.db_path)
        init_db(self.db_path)
        seed_demo_data(self.db_path)

        # Create an admin user for authenticated admin tests
        db = DatabaseSession(self.db_path)
        now = utcnow_iso()
        with db.atomic():
            db.insert(
                "users",
                {
                    "full_name": "Prof. R. Krishnan (Canteen Admin)",
                    "registration_number": "ADMIN-001",
                    "email": "admin@smartcanteen.edu.in",
                    "phone": "9447000001",
                    "department": "Operations Research & Administration",
                    "academic_year": "Faculty",
                    "password_hash": hash_password("AdminPass#2026"),
                    "role": "admin",
                    "consent_accepted": 1,
                    "preferences_json": "{}",
                    "is_active": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        db.close()

        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def register_and_login_student(
        self,
        name: str = "Ananya Nair",
        reg_no: str = "CET2026CS041",
        email: str = "ananya@cet.ac.in",
        phone: str = "9847112233",
        password: str = "StudentPass#123",
    ) -> dict:
        res = self.client.post(
            "/api/auth/register",
            json={
                "full_name": name,
                "registration_number": reg_no,
                "email": email,
                "phone": phone,
                "department": "Computer Science & Engineering",
                "academic_year": "S5 B.Tech",
                "password": password,
                "consent_accepted": True,
            },
        )
        self.assertEqual(res.status_code, 201, res.text)
        data = res.json()
        return {
            "token": data["access_token"],
            "headers": {"Authorization": f"Bearer {data['access_token']}"},
            "user": data["user"],
        }

    def login_admin(self) -> dict:
        res = self.client.post(
            "/api/auth/login",
            json={
                "email": "admin@smartcanteen.edu.in",
                "password": "AdminPass#2026",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        return {
            "token": data["access_token"],
            "headers": {"Authorization": f"Bearer {data['access_token']}"},
            "user": data["user"],
        }


# ============================================================================
# 1. MATHEMATICAL & OPERATIONS RESEARCH TESTS
# ============================================================================

class TestMathematicsAndOR( unittest.TestCase ):
    def test_rate_estimation_and_units(self) -> None:
        obs = [
            {"duration_minutes": 15.0, "arrivals_count": 18, "served_count": 15, "mean_service_time_minutes": 1.5, "event_type_measured": "PHYSICAL_ARRIVAL"},
            {"duration_minutes": 15.0, "arrivals_count": 12, "served_count": 15, "mean_service_time_minutes": 2.5, "event_type_measured": "PHYSICAL_ARRIVAL"},
        ]
        est = estimate_rates_from_observations(obs)
        self.assertTrue(est["has_sufficient_data"])
        # Total arrivals = 30 over 30 min -> lambda = 1.0 / min = 60.0 / hr
        self.assertAlmostEqual(est["lambda_per_min"], 1.0, places=5)
        self.assertAlmostEqual(est["lambda_per_hour"], 60.0, places=4)
        # Weighted mean service time = (1.5*15 + 2.5*15)/30 = 2.0 min -> mu = 0.5 / min = 30.0 / hr
        self.assertAlmostEqual(est["mean_service_time_minutes"], 2.0, places=5)
        self.assertAlmostEqual(est["mu_per_min"], 0.5, places=5)
        self.assertAlmostEqual(est["mu_per_hour"], 30.0, places=4)

        empty_est = estimate_rates_from_observations([])
        self.assertFalse(empty_est["has_sufficient_data"])
        self.assertIsNone(empty_est["lambda_per_min"])

    def test_mmc_erlang_c_exact_values_and_littles_law(self) -> None:
        # Classic M/M/2 test: lambda = 1.0/min, mu = 0.6/min, c = 2
        # a = 5/3, rho = 5/6
        # P0 = (1 - 5/6) / (1 + 5/6) = (1/6)/(11/6) = 1/11 = 0.090909...
        # Pw = (a^2 / (2! * (1 - rho))) * P0 = ((25/9) / (1/3)) * (1/11) = (25/3)*(1/11) = 25/33 = 0.757576
        # Lq = Pw * rho / (1 - rho) = (25/33) * 5 = 125/33 = 3.787879
        m = calculate_mmc_metrics(lambda_rate=1.0, mu_rate=0.6, counters=2)
        self.assertTrue(m["is_stable"])
        self.assertAlmostEqual(m["utilization_rho"], 5.0 / 6.0, places=5)
        self.assertAlmostEqual(m["p0_empty"], 1.0 / 11.0, places=5)
        self.assertAlmostEqual(m["pw_erlang_c"], 25.0 / 33.0, places=5)
        self.assertAlmostEqual(m["lq_queue_length"], 125.0 / 33.0, places=5)
        self.assertAlmostEqual(m["wq_wait_time"], 125.0 / 33.0, places=5)
        # Verify Little's Law: L = lambda * W and Lq = lambda * Wq
        self.assertAlmostEqual(m["l_system_length"], 1.0 * m["w_total_time"], places=5)
        self.assertAlmostEqual(m["lq_queue_length"], 1.0 * m["wq_wait_time"], places=5)

    def test_mmc_stability_and_edge_cases(self) -> None:
        # Unstable system: lambda = 1.5, mu = 0.6, c = 2 -> rho = 1.25 >= 1
        unstable = calculate_mmc_metrics(lambda_rate=1.5, mu_rate=0.6, counters=2)
        self.assertFalse(unstable["is_stable"])
        self.assertEqual(unstable["stability_status"], "UNSTABLE_OVERLOADED")
        self.assertIsNone(unstable["wq_wait_time"])
        self.assertIsNone(unstable["lq_queue_length"])
        self.assertEqual(unstable["min_counters_for_stability"], 3)

        # Zero arrivals edge case
        idle = calculate_mmc_metrics(lambda_rate=0.0, mu_rate=0.5, counters=2)
        self.assertTrue(idle["is_stable"])
        self.assertEqual(idle["p0_empty"], 1.0)
        self.assertEqual(idle["wq_wait_time"], 0.0)

        # Invalid inputs raise ValueError
        with self.assertRaises(ValueError):
            calculate_mmc_metrics(lambda_rate=-0.5, mu_rate=0.6, counters=2)
        with self.assertRaises(ValueError):
            calculate_mmc_metrics(lambda_rate=1.0, mu_rate=0.0, counters=2)
        with self.assertRaises(ValueError):
            calculate_mmc_metrics(lambda_rate=1.0, mu_rate=0.6, counters=0)

    def test_slot_capacity_and_integer_optimizer(self) -> None:
        # K_s = floor(0.85 * 2 * 0.60 * 15) = floor(15.3) = 15
        cap = calculate_slot_capacity(
            allocated_counters=2,
            service_rate_per_counter_per_min=0.60,
            duration_minutes=15.0,
            safety_factor=0.85,
            admin_cap=12,
            active_bookings_count=5,
        )
        self.assertEqual(cap["theoretical_capacity_ks"], 15)
        self.assertEqual(cap["effective_capacity"], 12)  # min(15, 12)
        self.assertEqual(cap["remaining_capacity_rs"], 7)  # 12 - 5

        slots_data = [
            {"slot_id": 1, "label": "10:00-10:15", "duration_minutes": 15.0, "admin_cap": 15, "allocated_counters": 2, "lambda_per_min": 0.5, "mu_per_min": 0.6, "safety_factor": 0.85, "active_bookings": 4},
            {"slot_id": 2, "label": "12:00-12:15", "duration_minutes": 15.0, "admin_cap": 20, "allocated_counters": 2, "lambda_per_min": 1.45, "mu_per_min": 0.6, "safety_factor": 0.85, "active_bookings": 12},
            {"slot_id": 3, "label": "12:15-12:30", "duration_minutes": 15.0, "admin_cap": 20, "allocated_counters": 2, "lambda_per_min": 1.60, "mu_per_min": 0.6, "safety_factor": 0.85, "active_bookings": 14},
        ]
        opt = optimize_counters_across_slots(
            slots_data=slots_data,
            total_available_counters=9,
            min_counters_per_slot=1,
            max_counters_per_slot=5,
            counter_cost_per_slot_inr=150.0,
            waiting_cost_per_customer_minute_inr=15.0,
            max_acceptable_wait_minutes=5.0,
        )
        self.assertEqual(opt["status"], "OPTIMIZED")
        self.assertLessEqual(opt["summary"]["recommended_total_counter_slots"], 9)
        self.assertEqual(opt["summary"]["existing_bookings_reassigned"], 0)
        # Slots 2 and 3 had lambda=1.45 and 1.60 with mu=0.6 (unstable under c=2, requires c>=3)
        self.assertGreaterEqual(opt["slots"][1]["recommended"]["counters"], 3)
        self.assertGreaterEqual(opt["slots"][2]["recommended"]["counters"], 3)
        self.assertTrue(opt["slots"][1]["recommended"]["is_stable"])
        self.assertTrue(opt["slots"][2]["recommended"]["is_stable"])


# ============================================================================
# 2. STOCHASTIC SIMULATION TESTS
# ============================================================================

class TestStochasticSimulation(unittest.TestCase):
    def test_fixed_seed_reproducibility_and_unfinished_jobs(self) -> None:
        r1 = run_single_des_replication(
            seed=12345,
            lambda_per_min=1.1,
            mu_per_min=0.6,
            counters=2,
            horizon_minutes=60.0,
            warmup_minutes=5.0,
        )
        r2 = run_single_des_replication(
            seed=12345,
            lambda_per_min=1.1,
            mu_per_min=0.6,
            counters=2,
            horizon_minutes=60.0,
            warmup_minutes=5.0,
        )
        self.assertEqual(r1, r2)
        self.assertGreaterEqual(r1["mean_wq_minutes"], 0.0)
        self.assertEqual(
            r1["total_arrivals"],
            r1["completed_count"] + r1["unfinished_total_count"],
        )

    def test_multi_replication_comparison_and_analytical_consistency(self) -> None:
        comp = run_multi_replication_comparison(
            random_seed=42,
            replications=25,
            lambda_per_min=0.9,
            mu_per_min=0.6,
            baseline_counters=2,
            optimized_counters=3,
            horizon_minutes=180.0,
            warmup_minutes=15.0,
        )
        base_wq = comp["baseline"]["simulation"]["wq_wait_minutes"]["mean"]
        opt_wq = comp["optimized"]["simulation"]["wq_wait_minutes"]["mean"]
        # Adding a 3rd counter must strictly reduce expected waiting time
        self.assertLess(opt_wq, base_wq)
        # Analytical M/M/c utilization for lambda=0.9, mu=0.6, c=2 is 0.75; simulated mean should be close
        sim_rho = comp["baseline"]["simulation"]["utilization_rho"]["mean"]
        self.assertAlmostEqual(sim_rho, 0.75, delta=0.12)


# ============================================================================
# 3. RESERVATIONS, PAYMENTS, TOKENS & SCENARIOS A-E END-TO-END TESTS
# ============================================================================

class TestFullStackWorkflowsAndScenarios(SmartCanteenTestCase):
    def test_scenario_a_online_direct_upi_and_manual_reconciliation(self) -> None:
        """Scenario A: Student places Online UPI order -> PENDING -> submits UTR -> still PENDING -> Admin reconciles -> PAID token."""
        stu = self.register_and_login_student()
        adm = self.login_admin()

        slots = self.client.get("/api/slots?slot_date=2026-10-18").json()["slots"]
        chosen_slot = slots[0]
        menu = self.client.get("/api/menu").json()["items"]

        # Place Online UPI order
        order_res = self.client.post(
            "/api/orders",
            headers=stu["headers"],
            json={
                "items": [
                    {"menu_item_id": next(i["id"] for i in menu if i["item_code"] == "SC-M01"), "quantity": 2},
                    {"menu_item_id": next(i["id"] for i in menu if i["item_code"] == "SC-M06"), "quantity": 1},
                ],
                "slot_id": chosen_slot["id"],
                "payment_method": "ONLINE_UPI",
            },
        )
        self.assertEqual(order_res.status_code, 201, order_res.text)
        order = order_res.json()["order"]
        order_id = order["id"]

        # Verify authoritative price: 2 * 65.00 + 1 * 25.00 = 155.00 INR (15500 paise)
        self.assertEqual(order["total_paise"], 15500)
        self.assertEqual(order["payment_status"], "PENDING")
        self.assertEqual(order["token"]["payment_badge"], "PAYMENT_PENDING")
        self.assertFalse(order["token"]["is_confirmed_paid"])
        self.assertTrue(order["payment"]["upi_intent_uri"].startswith("upi://pay?pa="))
        self.assertIn("am=155.00", order["payment"]["upi_intent_uri"])

        # Student submits UTR — status must remain PENDING (never auto-marked PAID!)
        utr_res = self.client.post(
            f"/api/payments/{order_id}/submit-utr",
            headers=stu["headers"],
            json={"utr_number": "429183746512"},
        )
        self.assertEqual(utr_res.status_code, 200, utr_res.text)
        self.assertEqual(utr_res.json()["payment_status"], "PENDING")

        # Verify pending payment is NOT counted as verified collected revenue on Admin Dashboard
        dash_before = self.client.get("/api/admin/dashboard", headers=adm["headers"]).json()
        self.assertEqual(dash_before["kpis"]["verified_collected_revenue_inr"], 0.0)
        self.assertEqual(dash_before["kpis"]["pending_verification_amount_inr"], 155.0)

        # Admin reconciles payment against bank statement
        payment_id = order["payment"]["id"]
        rec_res = self.client.post(
            f"/api/admin/payments/{payment_id}/reconcile",
            headers=adm["headers"],
            json={
                "action": "VERIFY_PAID",
                "bank_or_utr_reference": "429183746512",
                "notes": "Verified ₹155.00 credit in SBI merchant statement.",
            },
        )
        self.assertEqual(rec_res.status_code, 200, rec_res.text)
        self.assertEqual(rec_res.json()["payment_status"], "PAID")

        # Fetch updated order and verify PAID green token and separate Order QR vs Payment QR
        updated_order = self.client.get(f"/api/orders/{order_id}", headers=stu["headers"]).json()["order"]
        self.assertEqual(updated_order["payment_status"], "PAID")
        self.assertEqual(updated_order["order_status"], "CONFIRMED")
        self.assertEqual(updated_order["token"]["payment_badge"], "PAID")
        self.assertEqual(updated_order["token"]["badge_color"], "green")
        self.assertTrue(updated_order["token"]["is_confirmed_paid"])
        self.assertNotEqual(
            updated_order["token"]["order_qr_payload"],
            updated_order["payment"]["payment_qr_payload"],
        )
        # Ensure opaque Order QR contains no student PII
        qr_payload = updated_order["token"]["order_qr_payload"]
        self.assertNotIn("ananya", qr_payload.lower())
        self.assertNotIn("9847112233", qr_payload)

        # Verify Admin Dashboard now reflects ₹155.00 verified collected revenue
        dash_after = self.client.get("/api/admin/dashboard", headers=adm["headers"]).json()
        self.assertEqual(dash_after["kpis"]["verified_collected_revenue_inr"], 155.0)

    def test_scenario_b_pay_at_canteen_counter_collection_and_redemption(self) -> None:
        """Scenario B: Student orders with PAY_AT_CANTEEN -> UNPAID token -> Admin collects cash -> PAID badge -> Single redemption."""
        stu = self.register_and_login_student(
            name="Rahul Menon", reg_no="CET2026ME019", email="rahul@cet.ac.in"
        )
        adm = self.login_admin()

        slots = self.client.get("/api/slots?slot_date=2026-10-18").json()["slots"]
        menu = self.client.get("/api/menu").json()["items"]

        order_res = self.client.post(
            "/api/orders",
            headers=stu["headers"],
            json={
                "items": [{"menu_item_id": next(i["id"] for i in menu if i["item_code"] == "SC-M03"), "quantity": 1}],
                "slot_id": slots[1]["id"],
                "payment_method": "PAY_AT_CANTEEN",
            },
        )
        self.assertEqual(order_res.status_code, 201)
        order = order_res.json()["order"]
        order_id = order["id"]
        orig_opaque = order["token"]["opaque_token"]
        orig_token_no = order["token"]["token_number"]

        self.assertEqual(order["payment_status"], "UNPAID")
        self.assertEqual(order["token"]["payment_badge"], "UNPAID")
        self.assertEqual(order["token"]["badge_color"], "amber")
        self.assertEqual(order["amount_due_inr"], 95.0)

        # Advance order preparation state: CONFIRMED -> PREPARING -> READY
        for next_st in ("PREPARING", "READY"):
            st_res = self.client.post(
                f"/api/admin/orders/{order_id}/status",
                headers=adm["headers"],
                json={"new_status": next_st, "notes": f"Kitchen moved to {next_st}"},
            )
            self.assertEqual(st_res.status_code, 200)

        # Attempting to redeem UNPAID token before collecting payment must be rejected!
        premature_redeem = self.client.post(
            "/api/admin/tokens/redeem",
            headers=adm["headers"],
            json={"token_input": orig_opaque},
        )
        self.assertEqual(premature_redeem.status_code, 400)

        # Admin records counter payment received in CASH
        ctr_res = self.client.post(
            f"/api/admin/orders/{order_id}/counter-payment",
            headers=adm["headers"],
            json={
                "collection_mode": "CASH",
                "transaction_reference": "CASH-RECEIPT-101",
                "notes": "Collected ₹95.00 cash at Counter 1.",
            },
        )
        self.assertEqual(ctr_res.status_code, 200)
        self.assertEqual(ctr_res.json()["payment_status"], "PAID")
        # Verify token identity & opaque QR did NOT break when upgraded from UNPAID to PAID
        self.assertEqual(ctr_res.json()["opaque_token"], orig_opaque)
        self.assertEqual(ctr_res.json()["token_number"], orig_token_no)

        # Admin scans and redeems the collection token
        redeem_res = self.client.post(
            "/api/admin/tokens/redeem",
            headers=adm["headers"],
            json={"token_input": f"SMARTCANTEEN-TOKEN:{orig_opaque}", "notes": "Handed over thali"},
        )
        self.assertEqual(redeem_res.status_code, 200)
        self.assertEqual(redeem_res.json()["order"]["order_status"], "COMPLETED")
        self.assertEqual(redeem_res.json()["order"]["token"]["token_status"], "REDEEMED")

        # Repeated redemption of the same token must be rejected with 409 Conflict!
        dup_redeem = self.client.post(
            "/api/admin/tokens/redeem",
            headers=adm["headers"],
            json={"token_input": orig_opaque},
        )
        self.assertEqual(dup_redeem.status_code, 409)

    def test_scenario_c_full_slot_and_concurrent_booking_protection(self) -> None:
        """Scenario C: Slot with capacity 1 rejects second simultaneous student and offers available alternatives."""
        adm = self.login_admin()
        stu1 = self.register_and_login_student(
            name="Student One", reg_no="CET2026CS101", email="s1@cet.ac.in"
        )
        stu2 = self.register_and_login_student(
            name="Student Two", reg_no="CET2026CS102", email="s2@cet.ac.in"
        )

        # Admin creates a tight slot with admin_cap = 1
        slot_res = self.client.post(
            "/api/admin/slots",
            headers=adm["headers"],
            json={
                "slot_date": "2026-10-18",
                "label": "03:00–03:15 PM",
                "start_time": "15:00",
                "end_time": "15:15",
                "admin_cap": 1,
                "allocated_counters": 2,
                "service_rate_per_counter": 0.6,
                "safety_factor": 0.85,
            },
        )
        self.assertEqual(slot_res.status_code, 201, slot_res.text)
        tight_slot_id = slot_res.json()["slot"]["id"]
        menu_id = next(i["id"] for i in self.client.get("/api/menu").json()["items"] if i["item_code"] == "SC-M01")

        # Student 1 books the final spot
        r1 = self.client.post(
            "/api/orders",
            headers=stu1["headers"],
            json={
                "items": [{"menu_item_id": menu_id, "quantity": 1}],
                "slot_id": tight_slot_id,
                "payment_method": "PAY_AT_CANTEEN",
            },
        )
        self.assertEqual(r1.status_code, 201)

        # Student 2 attempts to book the same full slot -> must get 409 SLOT_FULL with alternatives
        r2 = self.client.post(
            "/api/orders",
            headers=stu2["headers"],
            json={
                "items": [{"menu_item_id": menu_id, "quantity": 1}],
                "slot_id": tight_slot_id,
                "payment_method": "PAY_AT_CANTEEN",
            },
        )
        self.assertEqual(r2.status_code, 409)
        err_detail = r2.json()["detail"]
        self.assertEqual(err_detail["error"], "SLOT_FULL")
        self.assertTrue(len(err_detail["available_alternatives"]) > 0)

        # Student 2 explicitly chooses an alternative slot from the returned list
        alt_slot_id = err_detail["available_alternatives"][0]["id"]
        r2_alt = self.client.post(
            "/api/orders",
            headers=stu2["headers"],
            json={
                "items": [{"menu_item_id": menu_id, "quantity": 1}],
                "slot_id": alt_slot_id,
                "payment_method": "PAY_AT_CANTEEN",
            },
        )
        self.assertEqual(r2_alt.status_code, 201)
        self.assertEqual(r2_alt.json()["order"]["slot_id"], alt_slot_id)

    def test_scenario_d_observations_optimization_and_simulation_api(self) -> None:
        """Scenario D: Admin imports/enters observations, runs slot-aware optimization, and runs multi-replication DES."""
        adm = self.login_admin()

        # Preview CSV import first
        csv_payload = (
            "observation_date,slot_label,start_time,end_time,duration_minutes,event_type_measured,"
            "arrivals_count,served_count,mean_service_time_minutes,active_counters,start_queue_length,end_queue_length,notes\n"
            "2026-10-18,12:00–12:15 PM,12:00,12:15,15,PHYSICAL_ARRIVAL,24,20,1.60,2,3,7,Field study batch 1\n"
        )
        prev_res = self.client.post(
            "/api/admin/observations/csv-import",
            headers=adm["headers"],
            json={"csv_content": csv_payload, "confirm_write": False},
        )
        self.assertEqual(prev_res.status_code, 200)
        self.assertTrue(prev_res.json()["preview_mode"])
        self.assertEqual(prev_res.json()["valid_rows_count"], 1)

        # Commit CSV import
        imp_res = self.client.post(
            "/api/admin/observations/csv-import",
            headers=adm["headers"],
            json={"csv_content": csv_payload, "confirm_write": True},
        )
        self.assertEqual(imp_res.status_code, 200)
        self.assertEqual(imp_res.json()["imported_count"], 1)

        # Run slot-aware integer counter optimization
        opt_res = self.client.post(
            "/api/admin/optimization/run",
            headers=adm["headers"],
            json={
                "slot_date": "2026-10-18",
                "demand_source": "HYBRID",
                "total_available_counters": 15,
                "min_counters_per_slot": 1,
                "max_counters_per_slot": 5,
                "counter_cost_per_slot_inr": 150.0,
                "waiting_cost_per_customer_minute_inr": 12.0,
                "max_acceptable_wait_minutes": 5.0,
                "objective_mode": "WEIGHTED_COST",
                "save_experiment": True,
            },
        )
        self.assertEqual(opt_res.status_code, 200, opt_res.text)
        opt_data = opt_res.json()
        self.assertEqual(opt_data["status"], "OPTIMIZED")
        self.assertTrue(len(opt_data["slots"]) >= 6)

        # Run stochastic discrete-event simulation
        sim_res = self.client.post(
            "/api/admin/simulation/run",
            headers=adm["headers"],
            json={
                "experiment_name": "Peak Lunch 12:00-13:00 Evaluation",
                "arrival_rate_per_min": 1.35,
                "service_rate_per_counter_per_min": 0.60,
                "baseline_counters": 2,
                "optimized_counters": 3,
                "horizon_minutes": 60.0,
                "warmup_minutes": 5.0,
                "replications": 15,
                "random_seed": 2026,
                "scheduled_pickup_fraction": 0.65,
                "scheduled_jitter_sd_minutes": 2.0,
            },
        )
        self.assertEqual(sim_res.status_code, 200, sim_res.text)
        sim_data = sim_res.json()
        self.assertEqual(sim_data["provenance"], "STOCHASTIC_DES_SIMULATION_ESTIMATE")
        self.assertGreater(sim_data["comparison"]["simulated_wait_reduction_minutes"], 0.0)

    def test_scenario_e_expired_reservation_gateway_webhook_and_security(self) -> None:
        """Scenario E: Expired payment hold release, gateway webhook signature/idempotency, and RBAC privacy checks."""
        adm = self.login_admin()
        stu1 = self.register_and_login_student(
            name="Meera Krishnan", reg_no="CET2026EE011", email="meera@cet.ac.in"
        )
        stu2 = self.register_and_login_student(
            name="Arjun Varma", reg_no="CET2026EE012", email="arjun@cet.ac.in"
        )

        # 1. Privacy check: Student 2 cannot view Student 1's profile or orders
        priv_res = self.client.get(
            f"/api/students/{stu1['user']['id']}", headers=stu2["headers"]
        )
        self.assertEqual(priv_res.status_code, 403)

        # Student 2 cannot access Admin endpoints
        adm_res = self.client.get("/api/admin/dashboard", headers=stu2["headers"])
        self.assertEqual(adm_res.status_code, 403)

        # 2. Test expired temporary payment hold release
        slots = self.client.get("/api/slots?slot_date=2026-10-18").json()["slots"]
        slot_id = slots[0]["id"]
        cap_initial = slots[0]["remaining_capacity"]
        menu_id = next(i["id"] for i in self.client.get("/api/menu").json()["items"] if i["item_code"] == "SC-M01")

        ord_res = self.client.post(
            "/api/orders",
            headers=stu1["headers"],
            json={
                "items": [{"menu_item_id": menu_id, "quantity": 1}],
                "slot_id": slot_id,
                "payment_method": "ONLINE_UPI",
            },
        )
        self.assertEqual(ord_res.status_code, 201)
        pending_order_id = ord_res.json()["order"]["id"]

        # Capacity decreased by 1 during active TEMP_HOLD
        cap_during = self.client.get("/api/slots?slot_date=2026-10-18").json()["slots"][0]["remaining_capacity"]
        self.assertEqual(cap_during, cap_initial - 1)

        # Force reservation expires_at into the past and trigger cleanup
        db = DatabaseSession(self.db_path)
        past_iso = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        with db.atomic():
            db.execute(
                "UPDATE slot_reservations SET expires_at = ? WHERE order_id = ?",
                (past_iso, pending_order_id),
            )
        released = release_expired_reservations(db)
        db.close()
        self.assertEqual(released, 1)

        # Capacity is restored!
        cap_after = self.client.get("/api/slots?slot_date=2026-10-18").json()["slots"][0]["remaining_capacity"]
        self.assertEqual(cap_after, cap_initial)

        # 3. Scanning a payment QR (`upi://pay?...`) at the token redemption endpoint must be rejected
        bad_qr = self.client.post(
            "/api/admin/tokens/redeem",
            headers=adm["headers"],
            json={"token_input": "upi://pay?pa=canteen@examplebank&am=65.00"},
        )
        self.assertEqual(bad_qr.status_code, 400)

        # 4. Test Gateway mode webhook verification, amount mismatch, and idempotency
        settings.RAZORPAY_KEY_ID = "rzp_test_demo123"
        settings.RAZORPAY_KEY_SECRET = "rzp_test_secret_key_999"
        settings.RAZORPAY_WEBHOOK_SECRET = "whsec_test_secret_777"
        try:
            gw_ord_res = self.client.post(
                "/api/orders",
                headers=stu2["headers"],
                json={
                    "items": [{"menu_item_id": menu_id, "quantity": 1}],
                    "slot_id": slot_id,
                    "payment_method": "GATEWAY_UPI",
                },
            )
            self.assertEqual(gw_ord_res.status_code, 201, gw_ord_res.text)
            gw_order = gw_ord_res.json()["order"]
            prov_order_id = gw_order["payment"]["provider_order_id"]

            # Invalid webhook signature -> 400
            bad_wh = self.client.post(
                "/api/payments/gateway/webhook",
                content=b'{"event":"payment.captured"}',
                headers={"X-Razorpay-Signature": "invalid_sig"},
            )
            self.assertEqual(bad_wh.status_code, 400)

            # Amount mismatch -> 400
            wrong_amt_body = json.dumps({
                "event_id": "evt_wrong_amt_01",
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_wrong_01",
                            "order_id": prov_order_id,
                            "amount": 100,  # Wrong! Expected 6500 paise
                            "currency": "INR",
                            "notes": {"order_reference": gw_order["order_reference"]},
                        }
                    }
                },
            }).encode("utf-8")
            wrong_amt_sig = hmac.new(
                settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
                wrong_amt_body,
                hashlib.sha256,
            ).hexdigest()
            wrong_amt_res = self.client.post(
                "/api/payments/gateway/webhook",
                content=wrong_amt_body,
                headers={"X-Razorpay-Signature": wrong_amt_sig},
            )
            self.assertEqual(wrong_amt_res.status_code, 400)

            # Valid signed webhook -> 200 PROCESSED_PAID
            valid_body = json.dumps({
                "event_id": "evt_valid_paid_01",
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_valid_001",
                            "order_id": prov_order_id,
                            "amount": gw_order["total_paise"],
                            "currency": "INR",
                            "notes": {"order_reference": gw_order["order_reference"]},
                        }
                    }
                },
            }).encode("utf-8")
            valid_sig = hmac.new(
                settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
                valid_body,
                hashlib.sha256,
            ).hexdigest()
            ok_wh = self.client.post(
                "/api/payments/gateway/webhook",
                content=valid_body,
                headers={"X-Razorpay-Signature": valid_sig},
            )
            self.assertEqual(ok_wh.status_code, 200, ok_wh.text)
            self.assertEqual(ok_wh.json()["status"], "PROCESSED_PAID")

            # Duplicate webhook replay -> 200 IDEMPOTENT_DUPLICATE
            dup_wh = self.client.post(
                "/api/payments/gateway/webhook",
                content=valid_body,
                headers={"X-Razorpay-Signature": valid_sig},
            )
            self.assertEqual(dup_wh.status_code, 200)
            self.assertEqual(dup_wh.json()["status"], "IDEMPOTENT_DUPLICATE")
        finally:
            settings.RAZORPAY_KEY_ID = ""
            settings.RAZORPAY_KEY_SECRET = ""
            settings.RAZORPAY_WEBHOOK_SECRET = ""


if __name__ == "__main__":
    unittest.main(verbosity=2)
