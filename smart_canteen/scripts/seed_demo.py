"""Seed labelled synthetic demonstration data (Menu, Pickup Slots, and SYNTHETIC_DEMO Observations)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import DatabaseSession, get_database_path, init_db, utcnow_iso


DEMO_MENU = [
    ("SC-M01", "Masala Dosa with Sambar & Chutney", "Crisp fermented crepe with spiced potato filling, sambar, and coconut chutney.", "South Indian", 6500, 6.0, 1.1, 40),
    ("SC-M02", "Idli Vada Combo (2 Idli + 1 Medu Vada)", "Steamed rice cakes and crispy lentil doughnut served hot with sambar.", "South Indian", 5000, 4.0, 0.9, 50),
    ("SC-M03", "Kerala Veg Meals Thali", "Matta rice, sambar, avial, thoran, rasam, pappadam, and spiced buttermilk.", "Meals & Combos", 9500, 5.0, 1.2, 35),
    ("SC-M04", "Paneer Tikka Roll", "Whole-wheat paratha wrap with charred paneer cubes and mint yogurt chutney.", "Snacks & Rolls", 7500, 7.0, 1.15, 25),
    ("SC-M05", "Veg Puff & Samosa Duo", "Flaky baked vegetable puff paired with a spiced potato-pea samosa.", "Snacks & Rolls", 3500, 2.5, 0.6, 60),
    ("SC-M06", "Filter Coffee (Kumbakonam Degree)", "Traditional South Indian brass-davara decoction coffee with frothed milk.", "Beverages", 2500, 2.0, 0.5, 100),
    ("SC-M07", "Fresh Lime Mint Cooler", "Chilled lime juice infused with crushed mint leaves and rock salt.", "Beverages", 3000, 2.5, 0.6, 80),
    ("SC-M08", "Hyderabadi Veg Dum Biryani", "Aromatic basmati rice slow-cooked with seasonal vegetables and raita.", "Meals & Combos", 11000, 6.5, 1.3, 30),
]

DEMO_SLOTS = [
    ("2026-10-18", "10:00–10:15 AM", "10:00", "10:15", 15, 12, 2, 0.60, 0.85),
    ("2026-10-18", "10:15–10:30 AM", "10:15", "10:30", 15, 12, 2, 0.60, 0.85),
    ("2026-10-18", "12:00–12:15 PM", "12:00", "12:15", 15, 15, 2, 0.60, 0.85),
    ("2026-10-18", "12:15–12:30 PM", "12:15", "12:30", 15, 15, 2, 0.60, 0.85),
    ("2026-10-18", "12:30–12:45 PM", "12:30", "12:45", 15, 18, 3, 0.60, 0.85),
    ("2026-10-18", "01:00–01:15 PM", "13:00", "13:15", 15, 14, 2, 0.60, 0.85),
]

DEMO_OBSERVATIONS = [
    ("2026-10-18", "10:00–10:15 AM", "10:00", "10:15", 15.0, "PHYSICAL_ARRIVAL", 11, 11, 1.55, 2, 0, 1, "SYNTHETIC_DEMO", "[SYNTHETIC DEMO] Morning break moderate arrival flow"),
    ("2026-10-18", "10:15–10:30 AM", "10:15", "10:30", 15.0, "PHYSICAL_ARRIVAL", 14, 13, 1.60, 2, 1, 2, "SYNTHETIC_DEMO", "[SYNTHETIC DEMO] Late morning break arrival flow"),
    ("2026-10-18", "12:00–12:15 PM", "12:00", "12:15", 15.0, "PHYSICAL_ARRIVAL", 22, 17, 1.65, 2, 2, 7, "SYNTHETIC_DEMO", "[SYNTHETIC DEMO] Peak lunch rush — baseline 2 counters overloaded"),
    ("2026-10-18", "12:15–12:30 PM", "12:15", "12:30", 15.0, "PHYSICAL_ARRIVAL", 25, 18, 1.68, 2, 7, 14, "SYNTHETIC_DEMO", "[SYNTHETIC DEMO] Peak lunch rush — queue buildup requiring optimization"),
    ("2026-10-18", "12:30–12:45 PM", "12:30", "12:45", 15.0, "PHYSICAL_ARRIVAL", 19, 21, 1.60, 3, 6, 4, "SYNTHETIC_DEMO", "[SYNTHETIC DEMO] High lunch demand with 3 active counters"),
    ("2026-10-18", "01:00–01:15 PM", "13:00", "13:15", 15.0, "PHYSICAL_ARRIVAL", 10, 12, 1.50, 2, 2, 0, "SYNTHETIC_DEMO", "[SYNTHETIC DEMO] Post-lunch taper period"),
]


def seed_demo_data(db_path: str = None) -> None:
    init_db(db_path)
    db = DatabaseSession(db_path or get_database_path())
    try:
        now = utcnow_iso()
        with db.atomic():
            for code, name, desc, cat, paise, prep, wf, stock in DEMO_MENU:
                db.execute(
                    """
                    INSERT OR IGNORE INTO menu_items
                    (item_code, name, description, category, price_paise, prep_time_minutes, workload_factor, is_available, is_archived, stock_quantity, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
                    """,
                    (code, name, desc, cat, paise, prep, wf, stock, now, now),
                )
            for sdate, label, st, et, dur, cap, c, mu, alpha in DEMO_SLOTS:
                db.execute(
                    """
                    INSERT OR IGNORE INTO pickup_slots
                    (slot_date, label, start_time, end_time, duration_minutes, admin_cap, allocated_counters, service_rate_per_counter, safety_factor, cutoff_minutes_before, hold_timeout_minutes, prep_buffer_minutes, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 5, 10, 5, 'OPEN', ?, ?)
                    """,
                    (sdate, label, st, et, dur, cap, c, mu, alpha, now, now),
                )
            existing_obs = db.fetch_one("SELECT COUNT(*) AS cnt FROM observations WHERE source_type = 'SYNTHETIC_DEMO'")
            if not existing_obs or int(existing_obs.cnt) == 0:
                for sdate, label, st, et, dur, ev, arr, srv, mst, cnt, sq, eq, src, notes in DEMO_OBSERVATIONS:
                    slot_row = db.fetch_one("SELECT id FROM pickup_slots WHERE slot_date = ? AND label = ?", (sdate, label))
                    db.insert(
                        "observations",
                        {
                            "observation_date": sdate,
                            "slot_id": slot_row.id if slot_row else None,
                            "slot_label": label,
                            "start_time": st,
                            "end_time": et,
                            "duration_minutes": dur,
                            "event_type_measured": ev,
                            "arrivals_count": arr,
                            "served_count": srv,
                            "mean_service_time_minutes": mst,
                            "active_counters": cnt,
                            "start_queue_length": sq,
                            "end_queue_length": eq,
                            "source_type": src,
                            "notes": notes,
                            "created_by_admin_id": None,
                            "created_at": now,
                        },
                    )
        print("[OK] Seeded Smart Canteen menu items, pickup slots, and labelled SYNTHETIC_DEMO observations.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo_data()
