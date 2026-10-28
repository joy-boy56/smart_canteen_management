"""FastAPI Main Application for Smart Canteen — Queue Optimization & Intelligent Capacity Planning."""

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.config import BASE_DIR, settings
from backend.database import DatabaseSession, RowObject, get_db, init_db, utcnow_iso
from backend.models import (
    ObservationSourceType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    SlotStatus,
    UserRole,
    paise_to_rupees,
    rupees_to_paise,
)
from backend.or_engine.queueing import (
    METRIC_TOOLTIPS,
    calculate_mmc_metrics,
    estimate_rates_from_observations,
    optimize_counters_across_slots,
)
from backend.or_engine.simulation import run_multi_replication_comparison
from backend.schemas import (
    AdminCounterPaymentRequest,
    AdminOrderStatusUpdateRequest,
    AdminPaymentReconcileRequest,
    CartValidateRequest,
    DirectUpiUtrSubmitRequest,
    GatewayVerifyCallbackRequest,
    LoginRequest,
    MenuItemCreateRequest,
    MenuItemUpdateRequest,
    ObservationCreateRequest,
    OptimizationRequest,
    OrderCreateRequest,
    ProfileUpdateRequest,
    SimulationRunRequest,
    SlotCreateRequest,
    SlotUpdateRequest,
    StudentRegisterRequest,
    TokenRedeemRequest,
    UpiSettingsUpdateRequest,
)
from backend.security import (
    create_session_token,
    get_current_user,
    hash_password,
    require_admin,
    sanitize_user_dict,
    verify_password,
)
from backend.services.order_service import (
    compute_slot_metrics,
    create_order_atomic,
    format_order_details,
    list_slots_for_date,
    redeem_token_atomic,
    release_expired_reservations,
    update_order_status_atomic,
)
from backend.services.payment_service import (
    admin_reconcile_payment,
    get_app_setting,
    get_public_payment_settings,
    log_audit,
    process_gateway_webhook,
    record_counter_payment,
    set_app_setting,
    submit_student_utr,
    verify_gateway_callback,
)

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Academic Operations Research & Smart Canteen Web Application featuring "
        "time-slot-aware capacity planning, M/M/c queueing analytics, integer counter "
        "optimization, stochastic DES simulation, UPI payments, and paid/unpaid QR tokens."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    seed_default_users()
    seed_default_slots()
    seed_default_menu()


def seed_default_users() -> None:
    """Seed default users on first run if database is empty."""
    from backend.database import get_database_path
    from backend.security import hash_password
    import sqlite3

    db_path = get_database_path()
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM users")
            if cursor.fetchone()[0] == 0:
                now = utcnow_iso()
                default_users = [
                    ("Admin", "ADMIN-001", "admin@smartcanteen.edu.in", "9447000001",
                     "Operations Research & Admin", "Canteen Administrator", "admin", "AdminPass#2026"),
                    ("Ananya Nair", "CET2026CS041", "ananya@cet.ac.in", "9847112233",
                     "Computer Science & Engineering", "S5 B.Tech", "student", "StudentPass#2026"),
                    ("Meera Nair", "STAFF-001", "meera.nair@cet.ac.in", "9447000002",
                     "Canteen Operations", "Faculty / Staff", "student", "StaffPass#2026"),
                ]
                for name, reg, email, phone, dept, year, role, pwd in default_users:
                    conn.execute("""
                        INSERT INTO users (full_name, registration_number, email, phone, department, academic_year,
                                         password_hash, role, consent_accepted, preferences_json, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (name, reg, email, phone, dept, year, hash_password(pwd), role, 1, "{}", 1, now, now))
                conn.commit()
                print("✅ Default users seeded on startup")
    except Exception as e:
        print(f"⚠️ Seed check skipped: {e}")


def seed_default_slots() -> None:
    """Seed default pickup slots for the next 7 days if empty."""
    from backend.database import get_database_path
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db_path = get_database_path()
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM pickup_slots")
            if cursor.fetchone()[0] == 0:
                now = datetime.now(timezone.utc)
                for day in range(7):
                    slot_date = (now + timedelta(days=day)).strftime('%Y-%m-%d')
                    # 4 slots per day: 10:00-10:15, 12:00-12:15, 14:00-14:15, 16:00-16:15
                    # duration_minutes = 15 for all
                    slots = [
                        (slot_date, '10:00-10:15 AM', '10:00', '10:15', 15, 10, 2),
                        (slot_date, '12:00-12:15 PM', '12:00', '12:15', 15, 15, 3),
                        (slot_date, '14:00-14:15 PM', '14:00', '14:15', 15, 10, 2),
                        (slot_date, '16:00-16:15 PM', '16:00', '16:15', 15, 8, 2),
                    ]
                    for sd, label, st, et, dur, cap, counters in slots:
                        conn.execute("""
                            INSERT INTO pickup_slots (slot_date, label, start_time, end_time, duration_minutes, admin_cap, allocated_counters, status, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (sd, label, st, et, dur, cap, counters, 'OPEN', utcnow_iso(), utcnow_iso()))
                conn.commit()
                print("✅ Default pickup slots seeded on startup")
    except Exception as e:
        print(f"⚠️ Slot seed check skipped: {e}")


def seed_default_menu() -> None:
    """Seed default menu items if empty."""
    from backend.database import get_database_path
    import sqlite3

    db_path = get_database_path()
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM menu_items")
            if cursor.fetchone()[0] == 0:
                now = utcnow_iso()
                default_menu = [
                    # Breakfast
                    ("BRK-001", "Masala Dosa with Sambar & Chutney", "Crispy rice crepe with potato filling, served with sambar and coconut chutney", "Breakfast", 6500, 8.0, 1.2),
                    ("BRK-002", "Idli (2 pcs) with Sambar & Chutney", "Steamed rice cakes served with sambar and chutney", "Breakfast", 4000, 6.0, 1.0),
                    ("BRK-003", "Medu Vada (2 pcs) with Sambar", "Crispy lentil donuts served with sambar", "Breakfast", 4500, 7.0, 1.1),
                    ("BRK-004", "Upma with Coconut Chutney", "Savory semolina porridge with vegetables", "Breakfast", 3500, 5.0, 0.9),
                    ("BRK-005", "Pongal with Chutney & Vada", "Rice and lentil porridge with spices, served with chutney and vada", "Breakfast", 5000, 8.0, 1.0),
                    # Main Course
                    ("MAIN-001", "Kerala Veg Meals Thali", "Rice, sambar, rasam, avial, thoran, pappadam, pickle, curd", "Main Course", 9500, 12.0, 1.5),
                    ("MAIN-002", "Hyderabadi Veg Dum Biryani", "Basmati rice cooked with vegetables and aromatic spices", "Main Course", 11000, 15.0, 1.8),
                    ("MAIN-003", "Veg Pulao with Raita", "Fragrant rice with mixed vegetables, served with raita", "Main Course", 8500, 10.0, 1.3),
                    ("MAIN-004", "Chole Bhature", "Spicy chickpeas with deep-fried bread", "Main Course", 9000, 12.0, 1.4),
                    # Snacks
                    ("SNK-001", "Samosa (2 pcs) with Chutney", "Crispy triangular pastries with spiced potato filling", "Snacks", 3000, 5.0, 0.8),
                    ("SNK-002", "Bread Pakora (2 pcs)", "Bread slices dipped in gram flour batter and fried", "Snacks", 3500, 6.0, 1.0),
                    ("SNK-003", "Bajji (3 pcs) - Onion/Potato/Mirchi", "Assorted vegetable fritters", "Snacks", 4000, 7.0, 1.0),
                    # Beverages
                    ("BEV-001", "Kumbakonam Degree Filter Coffee", "Traditional South Indian filter coffee", "Beverages", 2500, 3.0, 0.5),
                    ("BEV-002", "Masala Chai", "Spiced Indian tea with milk", "Beverages", 1500, 2.0, 0.4),
                    ("BEV-003", "Fresh Lime Soda", "Refreshing lime soda with mint", "Beverages", 3000, 2.0, 0.3),
                    ("BEV-004", "Buttermilk (Chaas)", "Spiced buttermilk with curry leaves", "Beverages", 2000, 2.0, 0.3),
                    # Desserts
                    ("DES-001", "Gulab Jamun (2 pcs)", "Deep-fried milk solids soaked in sugar syrup", "Desserts", 4000, 3.0, 0.6),
                    ("DES-002", "Payasam", "Traditional rice pudding with jaggery and coconut", "Desserts", 3500, 4.0, 0.5),
                ]
                for code, name, desc, cat, price, prep, workload in default_menu:
                    conn.execute("""
                        INSERT INTO menu_items (item_code, name, description, category, price_paise, prep_time_minutes, workload_factor, is_available, is_archived, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (code, name, desc, cat, price, prep, workload, 1, 0, now, now))
                conn.commit()
                print("✅ Default menu items seeded on startup")
    except Exception as e:
        print(f"⚠️ Menu seed check skipped: {e}")


@app.get("/api/health", tags=["Health"])
def health_check(db: DatabaseSession = Depends(get_db)) -> Dict[str, Any]:
    row = db.fetch_one("SELECT COUNT(*) AS cnt FROM users")
    pay_cfg = get_public_payment_settings(db)
    return {
        "status": "ok",
        "application": settings.APP_NAME,
        "version": "1.0.0",
        "timezone": pay_cfg["timezone"],
        "effective_online_payment_mode": pay_cfg["effective_online_mode"],
        "gateway_configured": pay_cfg["gateway_configured"],
        "users_registered": int(row.cnt) if row else 0,
        "timestamp": utcnow_iso(),
    }


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED, tags=["Auth"])
def register_student(
    payload: StudentRegisterRequest,
    response: Response,
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    req_reg = bool(
        get_app_setting(db, "require_registration_number", settings.REQUIRE_REGISTRATION_NUMBER)
    )
    if req_reg and not payload.registration_number:
        raise HTTPException(
            status_code=400,
            detail="Student registration number is required by canteen configuration.",
        )

    with db.atomic():
        existing_email = db.fetch_one(
            "SELECT id FROM users WHERE email = ?", (payload.email,)
        )
        if existing_email:
            raise HTTPException(
                status_code=409,
                detail="An account with this email address already exists.",
            )
        if payload.registration_number:
            existing_reg = db.fetch_one(
                "SELECT id FROM users WHERE registration_number = ?",
                (payload.registration_number,),
            )
            if existing_reg:
                raise HTTPException(
                    status_code=409,
                    detail="An account with this student registration number already exists.",
                )

        now = utcnow_iso()
        pw_hash = hash_password(payload.password)
        user_id = db.insert(
            "users",
            {
                "full_name": payload.full_name.strip(),
                "registration_number": payload.registration_number,
                "email": payload.email,
                "phone": payload.phone.strip(),
                "department": payload.department.strip() if payload.department else None,
                "academic_year": payload.academic_year.strip() if payload.academic_year else None,
                "password_hash": pw_hash,
                "role": UserRole.STUDENT.value,
                "consent_accepted": 1 if payload.consent_accepted else 0,
                "preferences_json": "{}",
                "is_active": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        user = db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

    token = create_session_token(user.id, user.role, user.email)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.APP_ENV == "production",
        max_age=settings.SESSION_EXPIRE_MINUTES * 60,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": sanitize_user_dict(user),
    }


@app.post("/api/auth/login", tags=["Auth"])
def login_user(
    payload: LoginRequest,
    response: Response,
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    user = db.fetch_one(
        "SELECT * FROM users WHERE email = ? AND is_active = 1", (payload.email,)
    )
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    token = create_session_token(user.id, user.role, user.email)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.APP_ENV == "production",
        max_age=settings.SESSION_EXPIRE_MINUTES * 60,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": sanitize_user_dict(user),
    }


@app.post("/api/auth/logout", tags=["Auth"])
def logout_user(response: Response) -> Dict[str, Any]:
    response.delete_cookie(
        settings.SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.APP_ENV == "production",
    )
    return {"status": "logged_out"}


@app.get("/api/auth/me", tags=["Auth"])
def get_me(user: RowObject = Depends(get_current_user)) -> Dict[str, Any]:
    return {"user": sanitize_user_dict(user)}


@app.get("/api/students/profile", tags=["Students"])
def get_student_profile(
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    release_expired_reservations(db)
    order_rows = db.fetch_all(
        "SELECT * FROM orders WHERE student_id = ? ORDER BY id DESC", (user.id,)
    )
    orders_formatted = [format_order_details(db, r) for r in order_rows]
    active_orders = [
        o
        for o in orders_formatted
        if o["order_status"] not in (OrderStatus.COMPLETED.value, OrderStatus.CANCELLED.value)
    ]
    previous_orders = [
        o
        for o in orders_formatted
        if o["order_status"] in (OrderStatus.COMPLETED.value, OrderStatus.CANCELLED.value)
    ]
    payment_history = [o["payment"] for o in orders_formatted if o.get("payment")]

    return {
        "profile": sanitize_user_dict(user),
        "active_order": active_orders[0] if active_orders else None,
        "active_orders": active_orders,
        "previous_orders": previous_orders,
        "payment_history": payment_history,
    }


@app.put("/api/students/profile", tags=["Students"])
def update_student_profile(
    payload: ProfileUpdateRequest,
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    updates: Dict[str, Any] = {"updated_at": utcnow_iso()}
    if payload.full_name is not None:
        updates["full_name"] = payload.full_name.strip()
    if payload.phone is not None:
        updates["phone"] = payload.phone.strip()
    if payload.department is not None:
        updates["department"] = payload.department.strip()
    if payload.academic_year is not None:
        updates["academic_year"] = payload.academic_year.strip()
    if payload.preferences is not None:
        updates["preferences_json"] = json.dumps(payload.preferences)

    with db.atomic():
        db.update_by_id("users", user.id, updates)
        updated = db.fetch_one("SELECT * FROM users WHERE id = ?", (user.id,))
    return {"profile": sanitize_user_dict(updated)}


@app.get("/api/students/{student_id}", tags=["Students"])
def get_student_by_id(
    student_id: int,
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    if user.role != UserRole.ADMIN.value and int(user.id) != int(student_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You cannot view another student's profile or orders.",
        )
    target = db.fetch_one("SELECT * FROM users WHERE id = ?", (student_id,))
    if not target:
        raise HTTPException(status_code=404, detail="Student not found.")
    return {"profile": sanitize_user_dict(target)}


def _format_menu_item(row: RowObject) -> Dict[str, Any]:
    return {
        "id": row.id,
        "item_code": row.item_code,
        "name": row.name,
        "description": row.description,
        "category": row.category,
        "price_paise": row.price_paise,
        "price_inr": paise_to_rupees(row.price_paise),
        "image_url": row.image_url,
        "prep_time_minutes": row.prep_time_minutes,
        "workload_factor": row.workload_factor,
        "is_available": bool(row.is_available)
        and (row.stock_quantity is None or int(row.stock_quantity) > 0),
        "is_archived": bool(row.is_archived),
        "stock_quantity": row.stock_quantity,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@app.get("/api/menu", tags=["Menu"])
def list_menu_items(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    available_only: bool = Query(False),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    sql = "SELECT * FROM menu_items WHERE is_archived = 0"
    params: List[Any] = []
    if category and category.upper() != "ALL":
        sql += " AND LOWER(category) = LOWER(?)"
        params.append(category.strip())
    if search:
        sql += " AND (LOWER(name) LIKE LOWER(?) OR LOWER(description) LIKE LOWER(?))"
        q = f"%{search.strip()}%"
        params.extend([q, q])
    sql += " ORDER BY category ASC, name ASC"
    rows = db.fetch_all(sql, params)
    items = [_format_menu_item(r) for r in rows]
    if available_only:
        items = [i for i in items if i["is_available"]]
    categories = sorted({i["category"] for i in items})
    return {"items": items, "categories": categories}


@app.get("/api/slots", tags=["Slots"])
def get_available_slots(
    slot_date: Optional[str] = Query(None),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    slots = list_slots_for_date(db, slot_date=slot_date, include_disabled=False)
    return {
        "slot_date": slot_date,
        "slots": slots,
        "timezone": get_app_setting(db, "timezone", settings.CANTEEN_TIMEZONE),
    }


@app.post("/api/orders/validate-cart", tags=["Orders"])
def validate_cart(
    payload: CartValidateRequest,
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    release_expired_reservations(db)
    line_items = []
    total_paise = 0
    total_workload = 0.0
    max_prep = 0.0

    for entry in payload.items:
        row = db.fetch_one(
            "SELECT * FROM menu_items WHERE id = ? AND is_archived = 0",
            (entry.menu_item_id,),
        )
        if not row or not row.is_available:
            raise HTTPException(
                status_code=400, detail=f"Menu item {entry.menu_item_id} is unavailable."
            )
        if row.stock_quantity is not None and int(row.stock_quantity) < entry.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for '{row.name}' (remaining: {row.stock_quantity}).",
            )
        sub_paise = int(row.price_paise) * int(entry.quantity)
        total_paise += sub_paise
        total_workload += float(row.workload_factor) * (entry.quantity / 3.0)
        max_prep = max(max_prep, float(row.prep_time_minutes))
        line_items.append({
            "menu_item_id": row.id,
            "name": row.name,
            "unit_price_paise": row.price_paise,
            "unit_price_inr": paise_to_rupees(row.price_paise),
            "quantity": entry.quantity,
            "subtotal_paise": sub_paise,
            "subtotal_inr": paise_to_rupees(sub_paise),
        })

    slot_info = None
    if payload.slot_id is not None:
        s_row = db.fetch_one("SELECT * FROM pickup_slots WHERE id = ?", (payload.slot_id,))
        if not s_row:
            raise HTTPException(status_code=404, detail="Selected pickup slot not found.")
        slot_info = compute_slot_metrics(db, s_row)

    return {
        "valid": True,
        "items": line_items,
        "total_paise": total_paise,
        "total_inr": paise_to_rupees(total_paise),
        "estimated_prep_minutes": max_prep,
        "workload_units": max(1.0, round(total_workload, 2)),
        "slot": slot_info,
    }


@app.post("/api/orders", status_code=status.HTTP_201_CREATED, tags=["Orders"])
def place_order(
    payload: OrderCreateRequest,
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    order_dict = create_order_atomic(
        db=db,
        student=user,
        items_input=[{"menu_item_id": i.menu_item_id, "quantity": i.quantity} for i in payload.items],
        slot_id=payload.slot_id,
        payment_method=payload.payment_method.value,
        notes=payload.notes,
    )
    return {"order": order_dict}


@app.get("/api/orders", tags=["Orders"])
def list_my_orders(
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    release_expired_reservations(db)
    rows = db.fetch_all(
        "SELECT * FROM orders WHERE student_id = ? ORDER BY id DESC", (user.id,)
    )
    return {"orders": [format_order_details(db, r) for r in rows]}


@app.get("/api/orders/{order_id}", tags=["Orders"])
def get_order_by_id(
    order_id: int,
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    release_expired_reservations(db)
    order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order_id,))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if user.role != UserRole.ADMIN.value and int(order.student_id) != int(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You may only view your own orders.",
        )
    return {"order": format_order_details(db, order)}


@app.post("/api/orders/{order_id}/cancel", tags=["Orders"])
def cancel_order(
    order_id: int,
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order_id,))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if user.role != UserRole.ADMIN.value and int(order.student_id) != int(user.id):
        raise HTTPException(status_code=403, detail="Access denied.")
    if user.role != UserRole.ADMIN.value and order.order_status not in (
        OrderStatus.PLACED.value,
        OrderStatus.CONFIRMED.value,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Students cannot cancel an order once it is in '{order.order_status}' state.",
        )
    updated = update_order_status_atomic(
        db=db,
        actor=user,
        order_id=order_id,
        new_status=OrderStatus.CANCELLED,
        notes=f"Cancelled by {user.role}.",
    )
    return {"order": updated}


@app.get("/api/tokens/lookup/{token_str}", tags=["Tokens"])
def lookup_token(
    token_str: str,
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    cleaned = token_str.strip()
    if cleaned.startswith("SMARTCANTEEN-TOKEN:"):
        cleaned = cleaned[len("SMARTCANTEEN-TOKEN:") :].strip()
    tok = db.fetch_one(
        "SELECT * FROM tokens WHERE opaque_token = ? OR UPPER(token_number) = UPPER(?)",
        (cleaned, cleaned),
    )
    if not tok:
        raise HTTPException(status_code=404, detail="Token not found.")
    order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (tok.order_id,))
    if not order:
        raise HTTPException(status_code=404, detail="Associated order not found.")
    if user.role != UserRole.ADMIN.value and int(order.student_id) != int(user.id):
        raise HTTPException(status_code=403, detail="Access denied.")
    return {"order": format_order_details(db, order)}


@app.get("/api/payments/config", tags=["Payments"])
def get_payment_config(db: DatabaseSession = Depends(get_db)) -> Dict[str, Any]:
    return get_public_payment_settings(db)


@app.post("/api/payments/{order_id}/submit-utr", tags=["Payments"])
def submit_utr_endpoint(
    order_id: int,
    payload: DirectUpiUtrSubmitRequest,
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    return submit_student_utr(db, user.id, order_id, payload.utr_number)


@app.post("/api/payments/gateway/callback", tags=["Payments"])
def gateway_callback_endpoint(
    payload: GatewayVerifyCallbackRequest,
    user: RowObject = Depends(get_current_user),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    return verify_gateway_callback(
        db=db,
        order_id=payload.order_id,
        razorpay_order_id=payload.razorpay_order_id,
        razorpay_payment_id=payload.razorpay_payment_id,
        razorpay_signature=payload.razorpay_signature,
    )


@app.post("/api/payments/gateway/webhook", tags=["Payments"])
async def gateway_webhook_endpoint(
    request: Request,
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    raw_body = await request.body()
    sig = request.headers.get("X-Razorpay-Signature") or request.headers.get("x-razorpay-signature") or ""
    return process_gateway_webhook(db, raw_body, sig)


@app.get("/api/admin/dashboard", tags=["Admin"])
def get_admin_dashboard(
    slot_date: Optional[str] = Query(None),
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    release_expired_reservations(db)
    all_orders = db.fetch_all("SELECT * FROM orders ORDER BY id DESC")
    if slot_date:
        slot_ids = {
            r.id
            for r in db.fetch_all(
                "SELECT id FROM pickup_slots WHERE slot_date = ?", (slot_date,)
            )
        }
        orders = [o for o in all_orders if o.slot_id in slot_ids]
    else:
        orders = all_orders

    total_orders = len(orders)
    active_orders = [
        o
        for o in orders
        if o.order_status not in (OrderStatus.COMPLETED.value, OrderStatus.CANCELLED.value)
    ]
    pending_payments = [o for o in orders if o.payment_status == PaymentStatus.PENDING.value]
    confirmed_paid = [o for o in orders if o.payment_status == PaymentStatus.PAID.value]
    unpaid_orders = [
        o
        for o in orders
        if o.payment_status == PaymentStatus.UNPAID.value
        and o.order_status != OrderStatus.CANCELLED.value
    ]
    in_preparation = [o for o in orders if o.order_status == OrderStatus.PREPARING.value]
    ready_awaiting = [o for o in orders if o.order_status == OrderStatus.READY.value]
    completed_orders = [o for o in orders if o.order_status == OrderStatus.COMPLETED.value]
    cancelled_orders = [o for o in orders if o.order_status == OrderStatus.CANCELLED.value]

    gross_ordered_paise = sum(
        int(o.total_paise) for o in orders if o.order_status != OrderStatus.CANCELLED.value
    )
    verified_collected_revenue_paise = sum(
        int(o.total_paise) for o in orders if o.payment_status == PaymentStatus.PAID.value
    )
    pending_online_paise = sum(
        int(o.total_paise)
        for o in orders
        if o.payment_status == PaymentStatus.PENDING.value
        and o.order_status != OrderStatus.CANCELLED.value
    )
    unpaid_counter_due_paise = sum(
        int(o.total_paise)
        for o in orders
        if o.payment_status == PaymentStatus.UNPAID.value
        and o.order_status != OrderStatus.CANCELLED.value
    )
    refunded_paise = sum(
        int(o.total_paise) for o in orders if o.payment_status == PaymentStatus.REFUNDED.value
    )

    slots_summary = list_slots_for_date(db, slot_date=slot_date, include_disabled=True)
    obs_rows = [r.to_dict() for r in db.fetch_all("SELECT * FROM observations ORDER BY id DESC")]
    rate_est = estimate_rates_from_observations(obs_rows)

    avg_util = None
    if slots_summary:
        rhos = [s["utilization_rho"] for s in slots_summary if s.get("utilization_rho") is not None]
        if rhos:
            avg_util = round(sum(rhos) / len(rhos), 4)

    return {
        "has_orders": total_orders > 0,
        "has_observations": len(obs_rows) > 0,
        "kpis": {
            "orders_total": total_orders,
            "active_orders": len(active_orders),
            "pending_online_payments": len(pending_payments),
            "confirmed_paid_orders": len(confirmed_paid),
            "unpaid_orders": len(unpaid_orders),
            "orders_in_preparation": len(in_preparation),
            "ready_awaiting_collection": len(ready_awaiting),
            "completed_orders": len(completed_orders),
            "cancelled_orders": len(cancelled_orders),
            "gross_ordered_value_inr": paise_to_rupees(gross_ordered_paise),
            "verified_collected_revenue_inr": paise_to_rupees(verified_collected_revenue_paise),
            "pending_verification_amount_inr": paise_to_rupees(pending_online_paise),
            "unpaid_counter_due_inr": paise_to_rupees(unpaid_counter_due_paise),
            "refunded_amount_inr": paise_to_rupees(refunded_paise),
            "average_counter_utilization": avg_util,
        },
        "empirical_rates": rate_est,
        "slots": slots_summary,
        "payment_settings": get_public_payment_settings(db),
    }


@app.get("/api/admin/orders", tags=["Admin"])
def admin_list_orders(
    slot_id: Optional[int] = Query(None),
    slot_date: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    order_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    release_expired_reservations(db)
    rows = db.fetch_all("SELECT * FROM orders ORDER BY id DESC")
    detailed = [format_order_details(db, r) for r in rows]

    if slot_id is not None:
        detailed = [o for o in detailed if o["slot_id"] == slot_id]
    if slot_date:
        detailed = [
            o for o in detailed if o.get("slot") and o["slot"].get("slot_date") == slot_date
        ]
    if payment_status and payment_status.upper() != "ALL":
        detailed = [o for o in detailed if o["payment_status"] == payment_status.upper()]
    if order_status and order_status.upper() != "ALL":
        detailed = [o for o in detailed if o["order_status"] == order_status.upper()]
    if search:
        q = search.strip().lower()
        detailed = [
            o
            for o in detailed
            if q in o["order_reference"].lower()
            or (o.get("token") and q in o["token"]["token_number"].lower())
            or (o.get("student") and q in o["student"]["full_name"].lower())
            or (
                o.get("student")
                and o["student"].get("registration_number")
                and q in o["student"]["registration_number"].lower()
            )
        ]

    return {"orders": detailed}


@app.post("/api/admin/orders/{order_id}/status", tags=["Admin"])
def admin_update_order_status(
    order_id: int,
    payload: AdminOrderStatusUpdateRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    updated = update_order_status_atomic(
        db=db,
        actor=admin,
        order_id=order_id,
        new_status=payload.new_status,
        notes=payload.notes,
        authorized_unpaid_exception=payload.authorized_unpaid_exception,
    )
    return {"order": updated}


@app.get("/api/admin/payments", tags=["Admin"])
def admin_list_payments(
    payment_status: Optional[str] = Query(None),
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    sql = """
    SELECT p.*, o.order_reference, o.order_status, o.student_id,
           t.token_number, u.full_name AS student_name, u.registration_number
    FROM payments p
    JOIN orders o ON o.id = p.order_id
    LEFT JOIN tokens t ON t.order_id = o.id
    LEFT JOIN users u ON u.id = o.student_id
    """
    params: List[Any] = []
    if payment_status and payment_status.upper() != "ALL":
        sql += " WHERE p.payment_status = ?"
        params.append(payment_status.upper())
    sql += " ORDER BY p.id DESC"
    rows = db.fetch_all(sql, params)
    items = []
    for r in rows:
        d = r.to_dict()
        d["expected_amount_inr"] = paise_to_rupees(r.expected_amount_paise)
        d["verified_amount_inr"] = paise_to_rupees(r.verified_amount_paise)
        items.append(d)
    return {"payments": items}


@app.post("/api/admin/payments/{payment_id}/reconcile", tags=["Admin"])
def admin_reconcile_payment_endpoint(
    payment_id: int,
    payload: AdminPaymentReconcileRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    return admin_reconcile_payment(
        db=db,
        admin_user=admin,
        payment_id=payment_id,
        action=payload.action,
        bank_or_utr_reference=payload.bank_or_utr_reference,
        notes=payload.notes,
    )


@app.post("/api/admin/orders/{order_id}/counter-payment", tags=["Admin"])
def admin_record_counter_payment_endpoint(
    order_id: int,
    payload: AdminCounterPaymentRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    return record_counter_payment(
        db=db,
        admin_user=admin,
        order_id=order_id,
        collection_mode=payload.collection_mode,
        transaction_reference=payload.transaction_reference,
        notes=payload.notes,
    )


@app.post("/api/admin/tokens/redeem", tags=["Admin"])
def admin_redeem_token_endpoint(
    payload: TokenRedeemRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    redeemed_order = redeem_token_atomic(
        db=db,
        admin_user=admin,
        token_input=payload.token_input,
        notes=payload.notes,
        authorized_unpaid_exception=payload.authorized_unpaid_exception,
    )
    return {"status": "REDEEMED", "order": redeemed_order}


@app.get("/api/admin/settings/payments", tags=["Admin"])
def admin_get_payment_settings(
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    return get_public_payment_settings(db)


@app.put("/api/admin/settings/payments", tags=["Admin"])
def admin_update_payment_settings(
    payload: UpiSettingsUpdateRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    mode = payload.payment_mode.strip().upper()
    if mode not in ("DIRECT_UPI", "GATEWAY_UPI"):
        raise HTTPException(
            status_code=400, detail="payment_mode must be DIRECT_UPI or GATEWAY_UPI."
        )
    if mode == "GATEWAY_UPI" and not settings.is_gateway_configured:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot activate GATEWAY_UPI mode because RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, "
                "and RAZORPAY_WEBHOOK_SECRET are not configured in server environment variables."
            ),
        )

    with db.atomic():
        old_vpa = get_app_setting(db, "upi_vpa", settings.DEFAULT_UPI_VPA)
        set_app_setting(db, "merchant_name", payload.merchant_name.strip(), admin.id)
        set_app_setting(db, "upi_vpa", payload.upi_vpa.strip(), admin.id)
        set_app_setting(
            db,
            "merchant_description",
            (payload.merchant_description or "").strip(),
            admin.id,
        )
        set_app_setting(db, "currency", payload.currency.strip().upper(), admin.id)
        set_app_setting(db, "payment_mode", mode, admin.id)
        if payload.default_reservation_timeout_minutes is not None:
            set_app_setting(
                db,
                "default_reservation_timeout_minutes",
                int(payload.default_reservation_timeout_minutes),
                admin.id,
            )
        if payload.default_safety_factor is not None:
            set_app_setting(
                db,
                "default_safety_factor",
                float(payload.default_safety_factor),
                admin.id,
            )
        if payload.allow_unpaid_handover_exception is not None:
            set_app_setting(
                db,
                "allow_unpaid_handover_exception",
                bool(payload.allow_unpaid_handover_exception),
                admin.id,
            )

        log_audit(
            db,
            actor_user_id=admin.id,
            actor_role="admin",
            action_type="UPI_SETTINGS_UPDATED",
            target_type="application_settings",
            target_id="payments",
            details={
                "old_upi_vpa": old_vpa,
                "new_upi_vpa": payload.upi_vpa.strip(),
                "merchant_name": payload.merchant_name.strip(),
                "payment_mode": mode,
            },
        )
    return get_public_payment_settings(db)


@app.post("/api/admin/menu", status_code=status.HTTP_201_CREATED, tags=["Admin"])
def admin_create_menu_item(
    payload: MenuItemCreateRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    with db.atomic():
        dup = db.fetch_one(
            "SELECT id FROM menu_items WHERE UPPER(item_code) = UPPER(?)",
            (payload.item_code.strip(),),
        )
        if dup:
            raise HTTPException(
                status_code=409, detail=f"Item code '{payload.item_code}' already exists."
            )
        now = utcnow_iso()
        price_paise = rupees_to_paise(payload.price_inr)
        item_id = db.insert(
            "menu_items",
            {
                "item_code": payload.item_code.strip().upper(),
                "name": payload.name.strip(),
                "description": payload.description.strip(),
                "category": payload.category.strip(),
                "price_paise": price_paise,
                "image_url": payload.image_url,
                "prep_time_minutes": payload.prep_time_minutes,
                "workload_factor": payload.workload_factor,
                "is_available": 1 if payload.is_available else 0,
                "is_archived": 0,
                "stock_quantity": payload.stock_quantity,
                "created_at": now,
                "updated_at": now,
            },
        )
        log_audit(
            db,
            actor_user_id=admin.id,
            actor_role="admin",
            action_type="MENU_ITEM_CREATED",
            target_type="menu_item",
            target_id=str(item_id),
            details={"name": payload.name, "price_paise": price_paise},
        )
        row = db.fetch_one("SELECT * FROM menu_items WHERE id = ?", (item_id,))
    return {"item": _format_menu_item(row)}


@app.put("/api/admin/menu/{item_id}", tags=["Admin"])
def admin_update_menu_item(
    item_id: int,
    payload: MenuItemUpdateRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    with db.atomic():
        row = db.fetch_one("SELECT * FROM menu_items WHERE id = ?", (item_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Menu item not found.")
        updates: Dict[str, Any] = {"updated_at": utcnow_iso()}
        old_price = row.price_paise
        if payload.name is not None:
            updates["name"] = payload.name.strip()
        if payload.description is not None:
            updates["description"] = payload.description.strip()
        if payload.category is not None:
            updates["category"] = payload.category.strip()
        if payload.price_inr is not None:
            updates["price_paise"] = rupees_to_paise(payload.price_inr)
        if payload.image_url is not None:
            updates["image_url"] = payload.image_url
        if payload.prep_time_minutes is not None:
            updates["prep_time_minutes"] = payload.prep_time_minutes
        if payload.workload_factor is not None:
            updates["workload_factor"] = payload.workload_factor
        if payload.is_available is not None:
            updates["is_available"] = 1 if payload.is_available else 0
        if payload.is_archived is not None:
            updates["is_archived"] = 1 if payload.is_archived else 0
        if payload.clear_stock_limit:
            updates["stock_quantity"] = None
        elif payload.stock_quantity is not None:
            updates["stock_quantity"] = payload.stock_quantity

        db.update_by_id("menu_items", item_id, updates)
        if "price_paise" in updates and updates["price_paise"] != old_price:
            log_audit(
                db,
                actor_user_id=admin.id,
                actor_role="admin",
                action_type="MENU_PRICE_CHANGED",
                target_type="menu_item",
                target_id=str(item_id),
                details={
                    "name": row.name,
                    "old_price_paise": old_price,
                    "new_price_paise": updates["price_paise"],
                },
            )
        updated = db.fetch_one("SELECT * FROM menu_items WHERE id = ?", (item_id,))
    return {"item": _format_menu_item(updated)}


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@app.post("/api/admin/slots", status_code=status.HTTP_201_CREATED, tags=["Admin"])
def admin_create_slot(
    payload: SlotCreateRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    start_m = _hhmm_to_minutes(payload.start_time)
    end_m = _hhmm_to_minutes(payload.end_time)
    if end_m <= start_m:
        raise HTTPException(
            status_code=400,
            detail="Slot end_time must be strictly after start_time.",
        )
    duration_minutes = end_m - start_m

    with db.atomic():
        if not payload.allow_overlap:
            existing_slots = db.fetch_all(
                "SELECT * FROM pickup_slots WHERE slot_date = ? AND status != 'DISABLED'",
                (payload.slot_date,),
            )
            for ex in existing_slots:
                ex_s = _hhmm_to_minutes(ex.start_time)
                ex_e = _hhmm_to_minutes(ex.end_time)
                if max(start_m, ex_s) < min(end_m, ex_e):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Slot {payload.start_time}–{payload.end_time} overlaps with existing slot "
                            f"'{ex.label}' ({ex.start_time}–{ex.end_time}) on {payload.slot_date}."
                        ),
                    )
        now = utcnow_iso()
        slot_id = db.insert(
            "pickup_slots",
            {
                "slot_date": payload.slot_date,
                "label": payload.label.strip(),
                "start_time": payload.start_time,
                "end_time": payload.end_time,
                "duration_minutes": duration_minutes,
                "admin_cap": payload.admin_cap,
                "allocated_counters": payload.allocated_counters,
                "service_rate_per_counter": payload.service_rate_per_counter,
                "safety_factor": payload.safety_factor,
                "cutoff_minutes_before": payload.cutoff_minutes_before,
                "hold_timeout_minutes": payload.hold_timeout_minutes,
                "prep_buffer_minutes": payload.prep_buffer_minutes,
                "status": payload.status.value,
                "created_at": now,
                "updated_at": now,
            },
        )
        log_audit(
            db,
            actor_user_id=admin.id,
            actor_role="admin",
            action_type="SLOT_CREATED",
            target_type="pickup_slot",
            target_id=str(slot_id),
            details={"label": payload.label, "admin_cap": payload.admin_cap},
        )
        row = db.fetch_one("SELECT * FROM pickup_slots WHERE id = ?", (slot_id,))
    return {"slot": compute_slot_metrics(db, row)}


@app.put("/api/admin/slots/{slot_id}", tags=["Admin"])
def admin_update_slot(
    slot_id: int,
    payload: SlotUpdateRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    with db.atomic():
        row = db.fetch_one("SELECT * FROM pickup_slots WHERE id = ?", (slot_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Pickup slot not found.")

        new_start = payload.start_time or row.start_time
        new_end = payload.end_time or row.end_time
        s_m = _hhmm_to_minutes(new_start)
        e_m = _hhmm_to_minutes(new_end)
        if e_m <= s_m:
            raise HTTPException(
                status_code=400, detail="Slot end_time must be after start_time."
            )

        updates: Dict[str, Any] = {
            "start_time": new_start,
            "end_time": new_end,
            "duration_minutes": e_m - s_m,
            "updated_at": utcnow_iso(),
        }
        if payload.label is not None:
            updates["label"] = payload.label.strip()
        if payload.admin_cap is not None:
            updates["admin_cap"] = payload.admin_cap
        if payload.allocated_counters is not None:
            updates["allocated_counters"] = payload.allocated_counters
        if payload.service_rate_per_counter is not None:
            updates["service_rate_per_counter"] = payload.service_rate_per_counter
        if payload.safety_factor is not None:
            updates["safety_factor"] = payload.safety_factor
        if payload.cutoff_minutes_before is not None:
            updates["cutoff_minutes_before"] = payload.cutoff_minutes_before
        if payload.hold_timeout_minutes is not None:
            updates["hold_timeout_minutes"] = payload.hold_timeout_minutes
        if payload.prep_buffer_minutes is not None:
            updates["prep_buffer_minutes"] = payload.prep_buffer_minutes
        if payload.status is not None:
            updates["status"] = payload.status.value

        db.update_by_id("pickup_slots", slot_id, updates)
        log_audit(
            db,
            actor_user_id=admin.id,
            actor_role="admin",
            action_type="SLOT_CAPACITY_CHANGED",
            target_type="pickup_slot",
            target_id=str(slot_id),
            details=updates,
        )
        updated = db.fetch_one("SELECT * FROM pickup_slots WHERE id = ?", (slot_id,))
    return {"slot": compute_slot_metrics(db, updated)}


@app.get("/api/admin/students", tags=["Admin"])
def admin_list_students(
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    rows = db.fetch_all("SELECT * FROM users WHERE role = 'student' ORDER BY id DESC")
    return {"students": [sanitize_user_dict(r) for r in rows]}


@app.get("/api/admin/audit-logs", tags=["Admin"])
def admin_list_audit_logs(
    limit: int = Query(100, ge=1, le=500),
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    rows = db.fetch_all(
        """
        SELECT a.*, u.full_name AS actor_name, u.email AS actor_email
        FROM audit_logs a
        LEFT JOIN users u ON u.id = a.actor_user_id
        ORDER BY a.id DESC LIMIT ?
        """,
        (limit,),
    )
    logs = []
    for r in rows:
        d = r.to_dict()
        try:
            d["details"] = json.loads(r.details_json or "{}")
        except Exception:
            d["details"] = {}
        logs.append(d)
    return {"audit_logs": logs}


@app.get("/api/admin/observations", tags=["Observations"])
def admin_list_observations(
    observation_date: Optional[str] = Query(None),
    slot_id: Optional[int] = Query(None),
    source_type: Optional[str] = Query(None),
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    sql = "SELECT * FROM observations WHERE 1=1"
    params: List[Any] = []
    if observation_date:
        sql += " AND observation_date = ?"
        params.append(observation_date)
    if slot_id is not None:
        sql += " AND slot_id = ?"
        params.append(slot_id)
    if source_type and source_type.upper() != "ALL":
        sql += " AND source_type = ?"
        params.append(source_type.upper())
    sql += " ORDER BY observation_date DESC, start_time ASC, id DESC"
    rows = [r.to_dict() for r in db.fetch_all(sql, params)]
    rate_summary = estimate_rates_from_observations(rows)
    return {"observations": rows, "rate_summary": rate_summary}


@app.post("/api/admin/observations", status_code=status.HTTP_201_CREATED, tags=["Observations"])
def admin_create_observation(
    payload: ObservationCreateRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    now = utcnow_iso()
    with db.atomic():
        obs_id = db.insert(
            "observations",
            {
                "observation_date": payload.observation_date,
                "slot_id": payload.slot_id,
                "slot_label": payload.slot_label.strip(),
                "start_time": payload.start_time.strip(),
                "end_time": payload.end_time.strip(),
                "duration_minutes": payload.duration_minutes,
                "event_type_measured": payload.event_type_measured.value,
                "arrivals_count": payload.arrivals_count,
                "served_count": payload.served_count,
                "mean_service_time_minutes": payload.mean_service_time_minutes,
                "active_counters": payload.active_counters,
                "start_queue_length": payload.start_queue_length or 0,
                "end_queue_length": payload.end_queue_length or 0,
                "mean_prep_time_minutes": payload.mean_prep_time_minutes,
                "mean_collection_time_minutes": payload.mean_collection_time_minutes,
                "source_type": payload.source_type.value,
                "notes": payload.notes,
                "created_by_admin_id": admin.id,
                "created_at": now,
            },
        )
        row = db.fetch_one("SELECT * FROM observations WHERE id = ?", (obs_id,))
    return {"observation": row.to_dict()}


@app.delete("/api/admin/observations/{obs_id}", tags=["Observations"])
def admin_delete_observation(
    obs_id: int,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    with db.atomic():
        row = db.fetch_one("SELECT * FROM observations WHERE id = ?", (obs_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Observation not found.")
        db.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
        log_audit(
            db,
            actor_user_id=admin.id,
            actor_role="admin",
            action_type="OBSERVATION_DELETED",
            target_type="observation",
            target_id=str(obs_id),
            details=row.to_dict(),
        )
    return {"deleted_id": obs_id}


CSV_HEADERS = [
    "observation_date",
    "slot_label",
    "start_time",
    "end_time",
    "duration_minutes",
    "event_type_measured",
    "arrivals_count",
    "served_count",
    "mean_service_time_minutes",
    "active_counters",
    "start_queue_length",
    "end_queue_length",
    "notes",
]


@app.get("/api/admin/observations/csv-template", tags=["Observations"])
def download_observations_csv_template(
    admin: RowObject = Depends(require_admin),
) -> PlainTextResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS)
    writer.writerow([
        "2026-10-18",
        "12:00-12:15 PM",
        "12:00",
        "12:15",
        "15",
        "PHYSICAL_ARRIVAL",
        "18",
        "16",
        "1.65",
        "2",
        "1",
        "3",
        "Peak lunch walk-in + scheduled pickup observation",
    ])
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=observations_import_template.csv"},
    )


@app.post("/api/admin/observations/csv-import", tags=["Observations"])
def import_observations_csv(
    payload: Dict[str, Any],
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    csv_text = str(payload.get("csv_content") or "")
    confirm_write = bool(payload.get("confirm_write", False))
    skip_duplicates = bool(payload.get("skip_duplicates", True))

    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    valid_rows = []
    errors = []
    duplicates = []

    for idx, row in enumerate(reader, start=2):
        try:
            obs_date = (row.get("observation_date") or "").strip()
            slot_label = (row.get("slot_label") or "").strip()
            start_t = (row.get("start_time") or "").strip()
            end_t = (row.get("end_time") or "").strip()
            dur = float(row.get("duration_minutes") or 0)
            arr = int(row.get("arrivals_count") or -1)
            srv = int(row.get("served_count") or -1)
            mst = float(row.get("mean_service_time_minutes") or 0)
            cnt = int(row.get("active_counters") or 0)
            ev_type = (row.get("event_type_measured") or "PHYSICAL_ARRIVAL").strip().upper()

            if not obs_date or not slot_label or dur <= 0 or arr < 0 or srv < 0 or mst <= 0 or cnt < 1:
                raise ValueError("Invalid numeric or missing required fields.")

            dup = db.fetch_one(
                """
                SELECT id FROM observations
                WHERE observation_date = ? AND slot_label = ? AND start_time = ? AND arrivals_count = ?
                """,
                (obs_date, slot_label, start_t, arr),
            )
            parsed = {
                "row_number": idx,
                "observation_date": obs_date,
                "slot_label": slot_label,
                "start_time": start_t,
                "end_time": end_t,
                "duration_minutes": dur,
                "event_type_measured": ev_type,
                "arrivals_count": arr,
                "served_count": srv,
                "mean_service_time_minutes": mst,
                "active_counters": cnt,
                "start_queue_length": int(row.get("start_queue_length") or 0),
                "end_queue_length": int(row.get("end_queue_length") or 0),
                "notes": (row.get("notes") or "").strip(),
                "is_duplicate": bool(dup),
            }
            if dup:
                duplicates.append(parsed)
                if not skip_duplicates:
                    valid_rows.append(parsed)
            else:
                valid_rows.append(parsed)
        except Exception as exc:
            errors.append({"row_number": idx, "error": str(exc), "raw": row})

    imported_count = 0
    if confirm_write and valid_rows and not errors:
        now = utcnow_iso()
        with db.atomic():
            for r in valid_rows:
                slot_match = db.fetch_one(
                    "SELECT id FROM pickup_slots WHERE slot_date = ? AND (label = ? OR start_time = ?)",
                    (r["observation_date"], r["slot_label"], r["start_time"]),
                )
                db.insert(
                    "observations",
                    {
                        "observation_date": r["observation_date"],
                        "slot_id": slot_match.id if slot_match else None,
                        "slot_label": r["slot_label"],
                        "start_time": r["start_time"],
                        "end_time": r["end_time"],
                        "duration_minutes": r["duration_minutes"],
                        "event_type_measured": r["event_type_measured"],
                        "arrivals_count": r["arrivals_count"],
                        "served_count": r["served_count"],
                        "mean_service_time_minutes": r["mean_service_time_minutes"],
                        "active_counters": r["active_counters"],
                        "start_queue_length": r["start_queue_length"],
                        "end_queue_length": r["end_queue_length"],
                        "source_type": ObservationSourceType.CSV_IMPORT.value,
                        "notes": r["notes"],
                        "created_by_admin_id": admin.id,
                        "created_at": now,
                    },
                )
                imported_count += 1

    return {
        "preview_mode": not confirm_write,
        "valid_rows_count": len(valid_rows),
        "duplicates_count": len(duplicates),
        "errors_count": len(errors),
        "imported_count": imported_count,
        "valid_rows": valid_rows,
        "duplicates": duplicates,
        "errors": errors,
    }


@app.get("/api/admin/observations/csv-export", tags=["Observations"])
def export_observations_csv(
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> PlainTextResponse:
    rows = db.fetch_all("SELECT * FROM observations ORDER BY observation_date DESC, start_time ASC")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS + ["source_type", "created_at"])
    for r in rows:
        writer.writerow([
            r.observation_date,
            r.slot_label,
            r.start_time,
            r.end_time,
            r.duration_minutes,
            r.event_type_measured,
            r.arrivals_count,
            r.served_count,
            r.mean_service_time_minutes,
            r.active_counters,
            r.start_queue_length,
            r.end_queue_length,
            r.notes or "",
            r.source_type,
            r.created_at,
        ])
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=canteen_observations_export.csv"},
    )


@app.get("/api/admin/queue/analyze", tags=["Operations Research"])
def analyze_queue_endpoint(
    lambda_per_min: float = Query(1.0, ge=0.0),
    mu_per_min: float = Query(0.60, gt=0.0),
    counters: int = Query(2, ge=1),
    admin: RowObject = Depends(require_admin),
) -> Dict[str, Any]:
    mmc = calculate_mmc_metrics(lambda_per_min, mu_per_min, counters)
    sweep = []
    for c_test in range(1, max(8, counters + 4)):
        m = calculate_mmc_metrics(lambda_per_min, mu_per_min, c_test)
        sweep.append({
            "counters": c_test,
            "utilization_rho": m["utilization_rho"],
            "is_stable": m["is_stable"],
            "wq_wait_time": m["wq_wait_time"],
            "lq_queue_length": m["lq_queue_length"],
            "pw_erlang_c": m["pw_erlang_c"],
        })
    return {
        "metrics": mmc,
        "counter_sensitivity_sweep": sweep,
        "tooltips": METRIC_TOOLTIPS,
    }


@app.post("/api/admin/optimization/run", tags=["Operations Research"])
def run_slot_optimization_endpoint(
    payload: OptimizationRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    release_expired_reservations(db)
    slot_rows = db.fetch_all(
        "SELECT * FROM pickup_slots WHERE slot_date = ? AND status != 'DISABLED' ORDER BY start_time ASC",
        (payload.slot_date,),
    )
    if not slot_rows:
        slot_rows = db.fetch_all(
            "SELECT * FROM pickup_slots WHERE status != 'DISABLED' ORDER BY slot_date DESC, start_time ASC LIMIT 8"
        )

    slots_input = []
    for s in slot_rows:
        sm = compute_slot_metrics(db, s)
        obs = [
            r.to_dict()
            for r in db.fetch_all(
                "SELECT * FROM observations WHERE slot_id = ? OR slot_label = ?",
                (s.id, s.label),
            )
        ]
        obs_est = estimate_rates_from_observations(obs)
        obs_lam = obs_est["lambda_per_min"] if obs_est["has_sufficient_data"] else None
        obs_mu = obs_est["mu_per_min"] if obs_est["has_sufficient_data"] else None
        book_lam = sm["active_reservations_consuming_capacity"] / max(1.0, float(s.duration_minutes))

        mode = payload.demand_source.upper()
        if mode == "OBSERVATIONS" and obs_lam is not None:
            lam = obs_lam
            d_src = "EMPIRICAL_OBSERVATIONS"
        elif mode == "BOOKINGS":
            lam = max(0.05, book_lam)
            d_src = "SCHEDULED_BOOKINGS"
        else:
            if obs_lam is not None:
                lam = max(book_lam, 0.6 * obs_lam + 0.4 * book_lam)
                d_src = "HYBRID_OBSERVATIONS_AND_BOOKINGS"
            else:
                lam = max(0.20, book_lam)
                d_src = "BOOKINGS_BASELINE_FALLBACK"

        mu = payload.service_rate_override or obs_mu or float(s.service_rate_per_counter)
        alpha = payload.safety_factor_override or float(s.safety_factor)

        slots_input.append({
            "slot_id": s.id,
            "label": s.label,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "duration_minutes": float(s.duration_minutes),
            "admin_cap": int(s.admin_cap),
            "allocated_counters": int(s.allocated_counters),
            "lambda_per_min": lam,
            "observed_lambda_per_min": obs_lam,
            "mu_per_min": mu,
            "safety_factor": alpha,
            "active_bookings": sm["active_reservations_consuming_capacity"],
            "pending_holds": sm["pending_payment_holds"],
            "demand_source_used": d_src,
        })

    result = optimize_counters_across_slots(
        slots_data=slots_input,
        total_available_counters=payload.total_available_counters,
        min_counters_per_slot=payload.min_counters_per_slot,
        max_counters_per_slot=payload.max_counters_per_slot,
        counter_cost_per_slot_inr=payload.counter_cost_per_slot_inr,
        waiting_cost_per_customer_minute_inr=payload.waiting_cost_per_customer_minute_inr,
        max_acceptable_wait_minutes=payload.max_acceptable_wait_minutes,
        objective_mode=payload.objective_mode,
    )

    if payload.save_experiment and slots_input:
        now = utcnow_iso()
        with db.atomic():
            exp_id = db.insert(
                "simulation_experiments",
                {
                    "experiment_name": f"Slot Optimization ({payload.slot_date})",
                    "experiment_type": "OPTIMIZATION_RUN",
                    "random_seed": 0,
                    "replications": 1,
                    "horizon_minutes": sum(x["duration_minutes"] for x in slots_input),
                    "warmup_minutes": 0.0,
                    "parameters_json": json.dumps(payload.dict()),
                    "baseline_results_json": json.dumps(result["summary"]),
                    "optimized_results_json": json.dumps(result["slots"]),
                    "observation_ids_json": "[]",
                    "created_by_admin_id": admin.id,
                    "created_at": now,
                },
            )
            log_audit(
                db,
                actor_user_id=admin.id,
                actor_role="admin",
                action_type="OPTIMIZATION_EXPERIMENT_CREATED",
                target_type="simulation_experiment",
                target_id=str(exp_id),
                details=result["summary"],
            )
            result["experiment_id"] = exp_id

    return result


@app.post("/api/admin/simulation/run", tags=["Operations Research"])
def run_simulation_endpoint(
    payload: SimulationRunRequest,
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    sim_out = run_multi_replication_comparison(
        random_seed=payload.random_seed,
        replications=payload.replications,
        lambda_per_min=payload.arrival_rate_per_min,
        mu_per_min=payload.service_rate_per_counter_per_min,
        baseline_counters=payload.baseline_counters,
        optimized_counters=payload.optimized_counters,
        horizon_minutes=payload.horizon_minutes,
        warmup_minutes=payload.warmup_minutes,
        scheduled_pickup_fraction=payload.scheduled_pickup_fraction,
        scheduled_jitter_sd_minutes=payload.scheduled_jitter_sd_minutes,
        slot_configs=payload.slot_configs,
    )
    now = utcnow_iso()
    with db.atomic():
        exp_id = db.insert(
            "simulation_experiments",
            {
                "experiment_name": payload.experiment_name,
                "experiment_type": "SLOT_AWARE_COMPARISON",
                "random_seed": payload.random_seed,
                "replications": payload.replications,
                "horizon_minutes": payload.horizon_minutes,
                "warmup_minutes": payload.warmup_minutes,
                "parameters_json": json.dumps(sim_out["parameters"]),
                "baseline_results_json": json.dumps(sim_out["baseline"]),
                "optimized_results_json": json.dumps(sim_out["optimized"]),
                "observation_ids_json": "[]",
                "created_by_admin_id": admin.id,
                "created_at": now,
            },
        )
        sim_out["experiment_id"] = exp_id
    return sim_out


@app.get("/api/admin/simulation/experiments", tags=["Operations Research"])
def list_simulation_experiments(
    admin: RowObject = Depends(require_admin),
    db: DatabaseSession = Depends(get_db),
) -> Dict[str, Any]:
    rows = db.fetch_all("SELECT * FROM simulation_experiments ORDER BY id DESC LIMIT 50")
    out = []
    for r in rows:
        d = r.to_dict()
        for k in ("parameters_json", "baseline_results_json", "optimized_results_json"):
            try:
                d[k.replace("_json", "")] = json.loads(r[k] or "{}")
            except Exception:
                d[k.replace("_json", "")] = {}
        out.append(d)
    return {"experiments": out}


FRONTEND_DIR = BASE_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/student", response_class=HTMLResponse, include_in_schema=False)
def serve_student_app() -> Response:
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return HTMLResponse("<h1>Smart Canteen API is running. Visit /docs for OpenAPI documentation.</h1>")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def serve_admin_app() -> Response:
    adm = FRONTEND_DIR / "admin.html"
    if adm.exists():
        return FileResponse(str(adm))
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return HTMLResponse("<h1>Smart Canteen Admin Interface</h1>")
