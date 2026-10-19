"""Transactional Slot, Order, and Token Service for Smart Canteen.

Implements:
1. Expiration cleanup of stale temporary payment-pending reservations (`TEMP_HOLD`).
2. Slot capacity evaluation and alternative slot suggestions when full.
3. Atomic order creation (`BEGIN IMMEDIATE` transaction):
   - Authoritative server-side price calculation in integer paise.
   - Stock validation & atomic stock decrement.
   - Atomic slot capacity check & reservation creation (never double-counts an order).
   - Cryptographically opaque QR token generation (`secrets.token_urlsafe(24)`).
4. Protected single-redemption token verification with audit logging.
"""

from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from backend.database import DatabaseSession, RowObject, utcnow_iso
from backend.models import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ReservationStatus,
    ReservationType,
    SlotStatus,
    TokenStatus,
    VALID_ORDER_TRANSITIONS,
    paise_to_rupees,
)
from backend.or_engine.queueing import calculate_mmc_metrics, calculate_slot_capacity
from backend.services.payment_service import (
    build_upi_intent_uri,
    gateway_adapter,
    get_public_payment_settings,
    log_audit,
)


def release_expired_reservations(db: DatabaseSession) -> int:
    """Expire stale TEMP_HOLD reservations whose timeout has elapsed and payment is still PENDING."""
    now_iso = utcnow_iso()
    expired_rows = db.fetch_all(
        """
        SELECT sr.id AS res_id, sr.order_id, o.payment_status, o.order_status
        FROM slot_reservations sr
        JOIN orders o ON o.id = sr.order_id
        WHERE sr.status = 'ACTIVE'
          AND sr.reservation_type = 'TEMP_HOLD'
          AND sr.expires_at IS NOT NULL
          AND sr.expires_at <= ?
        """,
        (now_iso,),
    )
    released_count = 0
    if not expired_rows:
        return 0

    with db.atomic():
        for row in expired_rows:
            # Re-verify inside atomic lock that payment did not just become PAID
            ord_fresh = db.fetch_one(
                "SELECT id, payment_status, order_status FROM orders WHERE id = ?",
                (row.order_id,),
            )
            if not ord_fresh or ord_fresh.payment_status == PaymentStatus.PAID.value:
                continue
            db.update_by_id(
                "slot_reservations",
                row.res_id,
                {"status": ReservationStatus.EXPIRED.value, "updated_at": now_iso},
            )
            db.update_by_id(
                "tokens",
                row.order_id,  # wait: update by order_id below
                {},
            )
            db.execute(
                "UPDATE tokens SET token_status = 'EXPIRED', updated_at = ? WHERE order_id = ? AND token_status = 'ACTIVE'",
                (now_iso, row.order_id),
            )
            released_count += 1
    return released_count


def compute_slot_metrics(db: DatabaseSession, slot: RowObject) -> Dict[str, Any]:
    """Compute real-time booking counts, remaining capacity, and M/M/c wait estimate for a slot."""
    res_rows = db.fetch_all(
        """
        SELECT reservation_type, COUNT(*) AS cnt, COALESCE(SUM(workload_units), 0.0) AS wl
        FROM slot_reservations
        WHERE slot_id = ? AND status = 'ACTIVE'
        GROUP BY reservation_type
        """,
        (slot.id,),
    )
    confirmed_paid = 0
    confirmed_unpaid = 0
    temp_holds = 0
    total_workload = 0.0

    for r in res_rows:
        rtype = r.reservation_type
        cnt = int(r.cnt)
        wl = float(r.wl)
        if rtype == ReservationType.CONFIRMED_PAID.value:
            confirmed_paid += cnt
            total_workload += wl
        elif rtype == ReservationType.CONFIRMED_UNPAID.value:
            confirmed_unpaid += cnt
            total_workload += wl
        elif rtype == ReservationType.TEMP_HOLD.value:
            temp_holds += cnt
            total_workload += wl

    # Total active capacity-consuming reservations (each order has at most 1 reservation row)
    active_bookings = confirmed_paid + confirmed_unpaid + temp_holds

    cap_info = calculate_slot_capacity(
        allocated_counters=int(slot.allocated_counters),
        service_rate_per_counter_per_min=float(slot.service_rate_per_counter),
        duration_minutes=float(slot.duration_minutes),
        safety_factor=float(slot.safety_factor),
        admin_cap=int(slot.admin_cap),
        active_bookings_count=active_bookings,
        active_workload_units=total_workload if total_workload > 0 else float(active_bookings),
    )

    # Estimate slot arrival rate lambda_s from active bookings + historical observations
    obs_row = db.fetch_one(
        """
        SELECT SUM(arrivals_count) AS tot_arr, SUM(duration_minutes) AS tot_dur
        FROM observations
        WHERE slot_id = ? OR slot_label = ?
        """,
        (slot.id, slot.label),
    )
    obs_lambda = None
    if obs_row and obs_row.tot_dur and float(obs_row.tot_dur) > 0:
        obs_lambda = float(obs_row.tot_arr) / float(obs_row.tot_dur)

    booking_lambda = active_bookings / max(1.0, float(slot.duration_minutes))
    effective_lambda = max(booking_lambda, obs_lambda or 0.0)
    mmc = calculate_mmc_metrics(
        lambda_rate=effective_lambda,
        mu_rate=float(slot.service_rate_per_counter),
        counters=int(slot.allocated_counters),
    )

    est_wait = mmc["wq_wait_time"] if mmc["is_stable"] else None
    est_total_wait = (
        round((est_wait or 0.0) + float(slot.prep_buffer_minutes), 1)
        if mmc["is_stable"]
        else None
    )

    state = cap_info["availability_state"]
    if slot.status != SlotStatus.OPEN.value:
        state = slot.status

    return {
        "id": slot.id,
        "slot_date": slot.slot_date,
        "label": slot.label,
        "start_time": slot.start_time,
        "end_time": slot.end_time,
        "duration_minutes": slot.duration_minutes,
        "status": slot.status,
        "availability_state": state,
        "admin_cap": slot.admin_cap,
        "theoretical_capacity_ks": cap_info["theoretical_capacity_ks"],
        "effective_capacity": cap_info["effective_capacity"],
        "confirmed_paid_bookings": confirmed_paid,
        "confirmed_unpaid_bookings": confirmed_unpaid,
        "confirmed_bookings_total": confirmed_paid + confirmed_unpaid,
        "pending_payment_holds": temp_holds,
        "active_reservations_consuming_capacity": active_bookings,
        "consumed_workload_units": cap_info["active_workload_units"],
        "remaining_capacity": cap_info["remaining_capacity_rs"] if slot.status == SlotStatus.OPEN.value else 0,
        "allocated_counters": slot.allocated_counters,
        "service_rate_per_counter": slot.service_rate_per_counter,
        "safety_factor": slot.safety_factor,
        "cutoff_minutes_before": slot.cutoff_minutes_before,
        "hold_timeout_minutes": slot.hold_timeout_minutes,
        "prep_buffer_minutes": slot.prep_buffer_minutes,
        "estimated_queue_wait_minutes": round(est_wait, 2) if est_wait is not None else None,
        "estimated_readiness_minutes": est_total_wait,
        "utilization_rho": mmc["utilization_rho"],
        "is_queue_stable": mmc["is_stable"],
    }


def list_slots_for_date(
    db: DatabaseSession, slot_date: Optional[str] = None, include_disabled: bool = False
) -> List[Dict[str, Any]]:
    release_expired_reservations(db)
    if slot_date:
        if include_disabled:
            rows = db.fetch_all(
                "SELECT * FROM pickup_slots WHERE slot_date = ? ORDER BY start_time ASC",
                (slot_date,),
            )
        else:
            rows = db.fetch_all(
                "SELECT * FROM pickup_slots WHERE slot_date = ? AND status != 'DISABLED' ORDER BY start_time ASC",
                (slot_date,),
            )
    else:
        if include_disabled:
            rows = db.fetch_all(
                "SELECT * FROM pickup_slots ORDER BY slot_date DESC, start_time ASC"
            )
        else:
            rows = db.fetch_all(
                "SELECT * FROM pickup_slots WHERE status != 'DISABLED' ORDER BY slot_date DESC, start_time ASC"
            )
    return [compute_slot_metrics(db, r) for r in rows]


def _generate_unique_opaque_token(db: DatabaseSession) -> str:
    """Generate a non-guessable 192-bit URL-safe opaque token, retrying on collision."""
    for _ in range(10):
        candidate = secrets.token_urlsafe(24)
        exists = db.fetch_one(
            "SELECT id FROM tokens WHERE opaque_token = ?", (candidate,)
        )
        if not exists:
            return candidate
    raise RuntimeError("Failed to generate a unique cryptographic token.")


def format_order_details(db: DatabaseSession, order: RowObject) -> Dict[str, Any]:
    """Assemble full authoritative representation of an order, its token, slot, and payment."""
    slot = db.fetch_one("SELECT * FROM pickup_slots WHERE id = ?", (order.slot_id,))
    student = db.fetch_one(
        "SELECT id, full_name, registration_number, email, phone, department FROM users WHERE id = ?",
        (order.student_id,),
    )
    items = db.fetch_all(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id ASC", (order.id,)
    )
    token = db.fetch_one("SELECT * FROM tokens WHERE order_id = ?", (order.id,))
    payment = db.fetch_one(
        "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1",
        (order.id,),
    )
    reservation = db.fetch_one(
        "SELECT * FROM slot_reservations WHERE order_id = ?", (order.id,)
    )
    history = db.fetch_all(
        "SELECT * FROM order_status_history WHERE order_id = ? ORDER BY id ASC",
        (order.id,),
    )

    # Derive authoritative token badge state from backend payment_status
    if order.payment_status == PaymentStatus.PAID.value:
        token_badge = "PAID"
        badge_color = "green"
        amount_due_paise = 0
        collection_instruction = (
            "Payment verified. Present your Collection Order QR at the pickup counter during your selected slot."
        )
    elif order.payment_status == PaymentStatus.UNPAID.value:
        token_badge = "UNPAID"
        badge_color = "amber"
        amount_due_paise = int(order.total_paise)
        collection_instruction = (
            f"Payment due at canteen: ₹{paise_to_rupees(order.total_paise):.2f}. "
            "Present this UNPAID token at the counter and complete payment at collection."
        )
    elif order.payment_status == PaymentStatus.PENDING.value:
        token_badge = "PAYMENT_PENDING"
        badge_color = "amber"
        amount_due_paise = int(order.total_paise)
        collection_instruction = (
            "Online UPI payment is pending verification. A confirmed PAID token is only activated "
            "once payment is verified by the payment gateway or reconciled by an administrator."
        )
    else:
        token_badge = order.payment_status
        badge_color = "red"
        amount_due_paise = int(order.total_paise)
        collection_instruction = f"Order payment state: {order.payment_status}."

    # Opaque verification payload for Order Token QR (contains NO student PII or secrets!)
    order_qr_payload = (
        f"SMARTCANTEEN-TOKEN:{token.opaque_token}" if token else None
    )

    return {
        "id": order.id,
        "order_reference": order.order_reference,
        "student_id": order.student_id,
        "student": student.to_dict() if student else None,
        "slot_id": order.slot_id,
        "slot": {
            "id": slot.id,
            "slot_date": slot.slot_date,
            "label": slot.label,
            "start_time": slot.start_time,
            "end_time": slot.end_time,
        }
        if slot
        else None,
        "order_status": order.order_status,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "total_paise": order.total_paise,
        "total_inr": paise_to_rupees(order.total_paise),
        "amount_due_paise": amount_due_paise,
        "amount_due_inr": paise_to_rupees(amount_due_paise),
        "workload_units": order.workload_units,
        "estimated_wait_minutes": order.estimated_wait_minutes,
        "notes": order.notes,
        "placed_at": order.placed_at,
        "confirmed_at": order.confirmed_at,
        "preparing_at": order.preparing_at,
        "ready_at": order.ready_at,
        "completed_at": order.completed_at,
        "cancelled_at": order.cancelled_at,
        "items": [
            {
                "id": it.id,
                "menu_item_id": it.menu_item_id,
                "name": it.item_name_snapshot,
                "unit_price_paise": it.unit_price_paise_snapshot,
                "unit_price_inr": paise_to_rupees(it.unit_price_paise_snapshot),
                "quantity": it.quantity,
                "prep_time_minutes": it.prep_time_snapshot,
                "subtotal_paise": it.subtotal_paise,
                "subtotal_inr": paise_to_rupees(it.subtotal_paise),
            }
            for it in items
        ],
        "token": {
            "id": token.id,
            "token_number": token.token_number,
            "opaque_token": token.opaque_token,
            "order_qr_payload": order_qr_payload,
            "token_status": token.token_status,
            "payment_badge": token_badge,
            "badge_color": badge_color,
            "is_confirmed_paid": order.payment_status == PaymentStatus.PAID.value,
            "collection_instruction": collection_instruction,
            "expires_at": token.expires_at,
            "redeemed_at": token.redeemed_at,
            "redeemed_by_admin_id": token.redeemed_by_admin_id,
        }
        if token
        else None,
        "payment": {
            "id": payment.id,
            "payment_reference": payment.payment_reference,
            "payment_method": payment.payment_method,
            "payment_status": payment.payment_status,
            "expected_amount_paise": payment.expected_amount_paise,
            "expected_amount_inr": paise_to_rupees(payment.expected_amount_paise),
            "verified_amount_paise": payment.verified_amount_paise,
            "verified_amount_inr": paise_to_rupees(payment.verified_amount_paise),
            "currency": payment.currency,
            "payee_vpa": payment.payee_vpa_snapshot,
            "payee_name": payment.payee_name_snapshot,
            "upi_intent_uri": payment.upi_intent_uri,
            "payment_qr_payload": payment.upi_intent_uri,
            "submitted_utr": payment.submitted_utr,
            "utr_submitted_at": payment.utr_submitted_at,
            "provider_name": payment.provider_name,
            "provider_order_id": payment.provider_order_id,
            "provider_payment_id": payment.provider_payment_id,
            "verification_method": payment.verification_method,
            "verified_at": payment.verified_at,
            "reconciliation_notes": payment.reconciliation_notes,
        }
        if payment
        else None,
        "reservation": {
            "id": reservation.id,
            "reservation_type": reservation.reservation_type,
            "status": reservation.status,
            "expires_at": reservation.expires_at,
        }
        if reservation
        else None,
        "status_history": [h.to_dict() for h in history],
    }


def create_order_atomic(
    db: DatabaseSession,
    student: RowObject,
    items_input: List[Dict[str, int]],
    slot_id: int,
    payment_method: str,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically validate cart, reserve slot capacity, create order, payment, and token.

    Never silently reassigns a student to a different slot if the requested slot is full.
    """
    release_expired_reservations(db)

    with db.atomic():
        # 1. Validate pickup slot inside atomic lock
        slot = db.fetch_one("SELECT * FROM pickup_slots WHERE id = ?", (slot_id,))
        if not slot:
            raise HTTPException(status_code=404, detail="Selected pickup time slot not found.")

        slot_metrics = compute_slot_metrics(db, slot)
        if slot.status != SlotStatus.OPEN.value:
            alternatives = [
                s
                for s in list_slots_for_date(db, slot.slot_date)
                if s["id"] != slot.id and s["remaining_capacity"] > 0 and s["status"] == "OPEN"
            ]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "SLOT_CLOSED",
                    "message": f"Pickup slot {slot.label} is currently {slot.status} and not accepting bookings.",
                    "requested_slot_id": slot.id,
                    "available_alternatives": alternatives,
                },
            )

        # 2. Validate menu items, authoritative pricing, and stock
        consolidated: Dict[int, int] = {}
        for entry in items_input:
            mid = int(entry["menu_item_id"])
            qty = int(entry["quantity"])
            if qty <= 0:
                raise HTTPException(status_code=400, detail="Item quantity must be positive.")
            consolidated[mid] = consolidated.get(mid, 0) + qty

        total_paise = 0
        max_prep_time = 0.0
        total_item_workload = 0.0
        snapshots: List[Dict[str, Any]] = []

        for mid, qty in consolidated.items():
            m_item = db.fetch_one(
                "SELECT * FROM menu_items WHERE id = ? AND is_archived = 0", (mid,)
            )
            if not m_item or not m_item.is_available:
                raise HTTPException(
                    status_code=400,
                    detail=f"Menu item ID {mid} is unavailable.",
                )
            if m_item.stock_quantity is not None and int(m_item.stock_quantity) < qty:
                raise HTTPException(
                    status_code=409,
                    detail=f"Insufficient stock for '{m_item.name}' (available: {m_item.stock_quantity}, requested: {qty}).",
                )
            unit_paise = int(m_item.price_paise)
            sub_paise = unit_paise * qty
            total_paise += sub_paise
            max_prep_time = max(max_prep_time, float(m_item.prep_time_minutes))
            total_item_workload += float(m_item.workload_factor) * (qty / 3.0)
            snapshots.append({
                "menu_item": m_item,
                "quantity": qty,
                "unit_price_paise": unit_paise,
                "subtotal_paise": sub_paise,
                "prep_time_minutes": float(m_item.prep_time_minutes),
                "workload_factor": float(m_item.workload_factor),
            })

        order_workload_units = max(1.0, round(total_item_workload, 2))

        # 3. Check slot remaining capacity against this booking
        if slot_metrics["remaining_capacity"] < 1:
            alternatives = [
                s
                for s in list_slots_for_date(db, slot.slot_date)
                if s["id"] != slot.id and s["remaining_capacity"] > 0 and s["status"] == "OPEN"
            ]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "SLOT_FULL",
                    "message": (
                        f"Preferred pickup slot '{slot.label}' is FULL "
                        f"(0 remaining of {slot_metrics['effective_capacity']} capacity). "
                        "Please select an available alternative slot."
                    ),
                    "requested_slot_id": slot.id,
                    "requested_slot_label": slot.label,
                    "available_alternatives": alternatives,
                },
            )

        # 4. Prevent duplicate pending online orders from rapid double-clicks
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        if payment_method in (PaymentMethod.ONLINE_UPI.value, PaymentMethod.GATEWAY_UPI.value):
            existing_pending = db.fetch_one(
                """
                SELECT o.id FROM orders o
                JOIN slot_reservations sr ON sr.order_id = o.id
                WHERE o.student_id = ?
                  AND o.slot_id = ?
                  AND o.total_paise = ?
                  AND o.payment_status = 'PENDING'
                  AND o.order_status = 'PLACED'
                  AND sr.status = 'ACTIVE'
                  AND sr.expires_at > ?
                ORDER BY o.id DESC LIMIT 1
                """,
                (student.id, slot.id, total_paise, now_iso),
            )
            if existing_pending:
                existing_ord = db.fetch_one(
                    "SELECT * FROM orders WHERE id = ?", (existing_pending.id,)
                )
                return format_order_details(db, existing_ord)

        # 5. Decrement tracked stock quantities atomically
        for snap in snapshots:
            m_item = snap["menu_item"]
            if m_item.stock_quantity is not None:
                new_stock = int(m_item.stock_quantity) - int(snap["quantity"])
                db.update_by_id(
                    "menu_items",
                    m_item.id,
                    {
                        "stock_quantity": new_stock,
                        "is_available": 1 if new_stock > 0 else 0,
                        "updated_at": now_iso,
                    },
                )

        # 6. Determine initial payment & order states
        pay_settings = get_public_payment_settings(db)
        if payment_method == PaymentMethod.PAY_AT_CANTEEN.value:
            init_pay_status = PaymentStatus.UNPAID.value
            init_ord_status = OrderStatus.CONFIRMED.value
            res_type = ReservationType.CONFIRMED_UNPAID.value
            res_expires = None
        else:
            init_pay_status = PaymentStatus.PENDING.value
            init_ord_status = OrderStatus.PLACED.value
            res_type = ReservationType.TEMP_HOLD.value
            hold_mins = int(slot.hold_timeout_minutes or pay_settings["default_reservation_timeout_minutes"])
            res_expires = (now_dt + timedelta(minutes=hold_mins)).isoformat()

        est_wait = round(
            (slot_metrics["estimated_queue_wait_minutes"] or 0.0) + max_prep_time, 1
        )

        # 7. Insert order record
        seq_row = db.fetch_one("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM orders")
        next_num = int(seq_row.next_id) if seq_row else 1
        date_compact = slot.slot_date.replace("-", "")
        order_ref = f"ORD-{date_compact}-{next_num:04d}-{secrets.token_hex(2).upper()}"

        order_id = db.insert(
            "orders",
            {
                "order_reference": order_ref,
                "student_id": student.id,
                "slot_id": slot.id,
                "order_status": init_ord_status,
                "payment_method": payment_method,
                "payment_status": init_pay_status,
                "total_paise": total_paise,
                "workload_units": order_workload_units,
                "estimated_wait_minutes": est_wait,
                "notes": notes,
                "placed_at": now_iso,
                "confirmed_at": now_iso if init_ord_status == OrderStatus.CONFIRMED.value else None,
                "updated_at": now_iso,
            },
        )

        # 8. Insert immutable order_items snapshots
        for snap in snapshots:
            db.insert(
                "order_items",
                {
                    "order_id": order_id,
                    "menu_item_id": snap["menu_item"].id,
                    "item_name_snapshot": snap["menu_item"].name,
                    "unit_price_paise_snapshot": snap["unit_price_paise"],
                    "quantity": snap["quantity"],
                    "prep_time_snapshot": snap["prep_time_minutes"],
                    "workload_factor_snapshot": snap["workload_factor"],
                    "subtotal_paise": snap["subtotal_paise"],
                },
            )

        # 9. Insert atomic slot_reservation row
        db.insert(
            "slot_reservations",
            {
                "slot_id": slot.id,
                "order_id": order_id,
                "student_id": student.id,
                "reservation_type": res_type,
                "status": ReservationStatus.ACTIVE.value,
                "workload_units": order_workload_units,
                "expires_at": res_expires,
                "created_at": now_iso,
                "updated_at": now_iso,
            },
        )

        # 10. Generate opaque QR token and human-readable token number
        token_number = f"SC-{order_id:05d}"
        opaque_token = _generate_unique_opaque_token(db)
        db.insert(
            "tokens",
            {
                "order_id": order_id,
                "token_number": token_number,
                "opaque_token": opaque_token,
                "token_status": TokenStatus.ACTIVE.value,
                "expires_at": res_expires,
                "created_at": now_iso,
                "updated_at": now_iso,
            },
        )

        # 11. Create payment record & UPI intent / Gateway session
        pay_ref = f"PAY-{order_id:05d}-{secrets.token_hex(3).upper()}"
        if payment_method == PaymentMethod.PAY_AT_CANTEEN.value:
            db.insert(
                "payments",
                {
                    "order_id": order_id,
                    "payment_reference": pay_ref,
                    "payment_method": "PAY_AT_CANTEEN",
                    "payment_status": PaymentStatus.UNPAID.value,
                    "expected_amount_paise": total_paise,
                    "verified_amount_paise": 0,
                    "refunded_amount_paise": 0,
                    "currency": pay_settings["currency"],
                    "verification_method": "NONE",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                },
            )
        elif payment_method == PaymentMethod.GATEWAY_UPI.value or (
            payment_method == PaymentMethod.ONLINE_UPI.value
            and pay_settings["effective_online_mode"] == "GATEWAY_UPI"
        ):
            gw_order = gateway_adapter.create_checkout_order(
                internal_order_ref=order_ref,
                amount_paise=total_paise,
                currency=pay_settings["currency"],
            )
            upi_uri = build_upi_intent_uri(
                payee_vpa=pay_settings["upi_vpa"],
                payee_name=pay_settings["merchant_name"],
                amount_paise=total_paise,
                payment_reference=pay_ref,
                transaction_note=f"Order {token_number} ({order_ref})",
                currency=pay_settings["currency"],
            )
            db.insert(
                "payments",
                {
                    "order_id": order_id,
                    "payment_reference": pay_ref,
                    "payment_method": "RAZORPAY_GATEWAY",
                    "payment_status": PaymentStatus.PENDING.value,
                    "expected_amount_paise": total_paise,
                    "verified_amount_paise": 0,
                    "refunded_amount_paise": 0,
                    "currency": pay_settings["currency"],
                    "payee_vpa_snapshot": pay_settings["upi_vpa"],
                    "payee_name_snapshot": pay_settings["merchant_name"],
                    "upi_intent_uri": upi_uri,
                    "provider_name": gw_order["provider_name"],
                    "provider_order_id": gw_order["provider_order_id"],
                    "verification_method": "NONE",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                },
            )
        else:
            upi_uri = build_upi_intent_uri(
                payee_vpa=pay_settings["upi_vpa"],
                payee_name=pay_settings["merchant_name"],
                amount_paise=total_paise,
                payment_reference=pay_ref,
                transaction_note=f"Order {token_number} ({order_ref})",
                currency=pay_settings["currency"],
            )
            db.insert(
                "payments",
                {
                    "order_id": order_id,
                    "payment_reference": pay_ref,
                    "payment_method": "DIRECT_UPI",
                    "payment_status": PaymentStatus.PENDING.value,
                    "expected_amount_paise": total_paise,
                    "verified_amount_paise": 0,
                    "refunded_amount_paise": 0,
                    "currency": pay_settings["currency"],
                    "payee_vpa_snapshot": pay_settings["upi_vpa"],
                    "payee_name_snapshot": pay_settings["merchant_name"],
                    "upi_intent_uri": upi_uri,
                    "verification_method": "NONE",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                },
            )

        # 12. Record initial order status history
        db.insert(
            "order_status_history",
            {
                "order_id": order_id,
                "from_status": None,
                "to_status": init_ord_status,
                "actor_user_id": student.id,
                "actor_role": "student",
                "notes": f"Order created with payment method {payment_method} for slot {slot.label}.",
                "created_at": now_iso,
            },
        )

        created_order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        return format_order_details(db, created_order)


def update_order_status_atomic(
    db: DatabaseSession,
    actor: RowObject,
    order_id: int,
    new_status: OrderStatus,
    notes: Optional[str] = None,
    authorized_unpaid_exception: bool = False,
) -> Dict[str, Any]:
    """Validate and execute an order status transition atomically."""
    with db.atomic():
        order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")

        curr_status = OrderStatus(order.order_status)
        if curr_status == new_status:
            return format_order_details(db, order)

        if new_status not in VALID_ORDER_TRANSITIONS.get(curr_status, set()):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid order status transition from {curr_status.value} to {new_status.value}.",
            )

        # Enforce payment verification before completing/handing over order unless authorized exception
        if new_status == OrderStatus.COMPLETED.value and order.payment_status != PaymentStatus.PAID.value:
            if not authorized_unpaid_exception:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot mark order COMPLETED while payment status is {order.payment_status}. "
                        "Record counter payment or reconcile UPI payment first."
                    ),
                )

        now = utcnow_iso()
        updates: Dict[str, Any] = {
            "order_status": new_status.value,
            "updated_at": now,
        }
        if new_status == OrderStatus.CONFIRMED:
            updates["confirmed_at"] = now
        elif new_status == OrderStatus.PREPARING:
            updates["preparing_at"] = now
        elif new_status == OrderStatus.READY:
            updates["ready_at"] = now
        elif new_status == OrderStatus.COMPLETED:
            updates["completed_at"] = now
        elif new_status == OrderStatus.CANCELLED:
            updates["cancelled_at"] = now
            # Release slot capacity on cancellation
            res = db.fetch_one(
                "SELECT * FROM slot_reservations WHERE order_id = ?", (order.id,)
            )
            if res and res.status == ReservationStatus.ACTIVE.value:
                db.update_by_id(
                    "slot_reservations",
                    res.id,
                    {"status": ReservationStatus.RELEASED.value, "updated_at": now},
                )
            tok = db.fetch_one("SELECT * FROM tokens WHERE order_id = ?", (order.id,))
            if tok and tok.token_status == TokenStatus.ACTIVE.value:
                db.update_by_id(
                    "tokens",
                    tok.id,
                    {"token_status": TokenStatus.CANCELLED.value, "updated_at": now},
                )

        db.update_by_id("orders", order.id, updates)
        db.insert(
            "order_status_history",
            {
                "order_id": order.id,
                "from_status": curr_status.value,
                "to_status": new_status.value,
                "actor_user_id": actor.id,
                "actor_role": actor.role,
                "notes": notes or f"Status updated to {new_status.value}",
                "created_at": now,
            },
        )
        log_audit(
            db,
            actor_user_id=actor.id,
            actor_role=actor.role,
            action_type="ORDER_STATUS_CHANGED",
            target_type="order",
            target_id=order.order_reference,
            details={
                "order_id": order.id,
                "from_status": curr_status.value,
                "to_status": new_status.value,
                "notes": notes,
            },
        )
        updated = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order.id,))
        return format_order_details(db, updated)


def redeem_token_atomic(
    db: DatabaseSession,
    admin_user: RowObject,
    token_input: str,
    notes: Optional[str] = None,
    authorized_unpaid_exception: bool = False,
) -> Dict[str, Any]:
    """Verify and atomically redeem a student collection token (single redemption only)."""
    cleaned = token_input.strip()
    if cleaned.startswith("SMARTCANTEEN-TOKEN:"):
        cleaned = cleaned[len("SMARTCANTEEN-TOKEN:") :].strip()

    # Reject if someone scans a UPI payment QR (`upi://pay?...`) instead of an Order Token QR!
    if cleaned.lower().startswith("upi://"):
        log_audit(
            db,
            actor_user_id=admin_user.id,
            actor_role="admin",
            action_type="TOKEN_REDEMPTION_REJECTED",
            target_type="token",
            target_id="PAYMENT_QR_SCANNED",
            details={"reason": "Attempted to scan a payment initiation QR instead of an Order Collection Token QR."},
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid QR type: This is a UPI Payment Initiation QR, not an Order Collection Token QR.",
        )

    with db.atomic():
        token = db.fetch_one(
            "SELECT * FROM tokens WHERE opaque_token = ? OR UPPER(token_number) = UPPER(?)",
            (cleaned, cleaned),
        )
        if not token:
            log_audit(
                db,
                actor_user_id=admin_user.id,
                actor_role="admin",
                action_type="TOKEN_REDEMPTION_REJECTED",
                target_type="token",
                target_id=cleaned[:32],
                details={"reason": "TOKEN_NOT_FOUND"},
            )
            raise HTTPException(
                status_code=404, detail="Collection token not found or invalid."
            )

        order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (token.order_id,))
        if not order:
            raise HTTPException(status_code=404, detail="Associated order not found.")

        if token.token_status == TokenStatus.REDEEMED.value:
            log_audit(
                db,
                actor_user_id=admin_user.id,
                actor_role="admin",
                action_type="TOKEN_REDEMPTION_REJECTED",
                target_type="token",
                target_id=token.token_number,
                details={
                    "reason": "ALREADY_REDEEMED",
                    "order_id": order.id,
                    "previously_redeemed_at": token.redeemed_at,
                },
            )
            raise HTTPException(
                status_code=409,
                detail=f"Token {token.token_number} has already been redeemed at {token.redeemed_at}.",
            )

        if token.token_status in (TokenStatus.CANCELLED.value, TokenStatus.EXPIRED.value):
            log_audit(
                db,
                actor_user_id=admin_user.id,
                actor_role="admin",
                action_type="TOKEN_REDEMPTION_REJECTED",
                target_type="token",
                target_id=token.token_number,
                details={"reason": f"TOKEN_{token.token_status}", "order_id": order.id},
            )
            raise HTTPException(
                status_code=400,
                detail=f"Token {token.token_number} cannot be redeemed because its status is {token.token_status}.",
            )

        if order.order_status == OrderStatus.CANCELLED.value:
            raise HTTPException(
                status_code=400, detail="Cannot redeem token for a cancelled order."
            )

        if order.payment_status != PaymentStatus.PAID.value and not authorized_unpaid_exception:
            log_audit(
                db,
                actor_user_id=admin_user.id,
                actor_role="admin",
                action_type="TOKEN_REDEMPTION_REJECTED",
                target_type="token",
                target_id=token.token_number,
                details={
                    "reason": f"PAYMENT_{order.payment_status}",
                    "order_id": order.id,
                },
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Payment not completed (current status: {order.payment_status}). "
                    "Please collect payment at the counter or verify UPI receipt before redeeming token."
                ),
            )

        now = utcnow_iso()
        cur = db.execute(
            """
            UPDATE tokens
            SET token_status = 'REDEEMED',
                redeemed_at = ?,
                redeemed_by_admin_id = ?,
                redemption_notes = ?,
                updated_at = ?
            WHERE id = ? AND token_status = 'ACTIVE'
            """,
            (
                now,
                admin_user.id,
                notes or "Redeemed at counter handover.",
                now,
                token.id,
            ),
        )
        if cur.rowcount != 1:
            raise HTTPException(
                status_code=409, detail="Concurrent token redemption rejected."
            )

        prev_status = order.order_status
        db.update_by_id(
            "orders",
            order.id,
            {
                "order_status": OrderStatus.COMPLETED.value,
                "completed_at": now,
                "updated_at": now,
            },
        )
        db.insert(
            "order_status_history",
            {
                "order_id": order.id,
                "from_status": prev_status,
                "to_status": OrderStatus.COMPLETED.value,
                "actor_user_id": admin_user.id,
                "actor_role": "admin",
                "notes": notes or f"Token {token.token_number} redeemed and order handed over.",
                "created_at": now,
            },
        )
        log_audit(
            db,
            actor_user_id=admin_user.id,
            actor_role="admin",
            action_type="TOKEN_REDEEMED",
            target_type="token",
            target_id=token.token_number,
            details={
                "order_id": order.id,
                "order_reference": order.order_reference,
                "payment_status": order.payment_status,
                "authorized_unpaid_exception": authorized_unpaid_exception,
            },
        )
        updated_order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order.id,))
        return format_order_details(db, updated_order)
