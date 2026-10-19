"""Payment Service & Provider Abstraction for Smart Canteen.

Implements:
1. Admin-configured UPI VPA & payment mode settings (never exposing gateway secrets).
2. Direct UPI URI intent generation (`upi://pay?...`) and QR payload construction.
3. Student UTR submission as a reconciliation aid (keeps status PENDING).
4. Admin manual reconciliation with audit logging and late-payment safety recovery.
5. Counter payment recording (Cash / UPI at counter) converting UNPAID -> PAID seamlessly.
6. Razorpay Gateway Adapter with HMAC-SHA256 callback & raw webhook signature verification,
   amount/currency/reference validation, and idempotent event handling.
"""

import hashlib
import hmac
import json
import secrets
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import HTTPException, status

from backend.config import settings
from backend.database import DatabaseSession, RowObject, utcnow_iso
from backend.models import (
    OrderStatus,
    PaymentStatus,
    ReservationStatus,
    ReservationType,
    TokenStatus,
    VerificationMethod,
    paise_to_rupees,
)


def log_audit(
    db: DatabaseSession,
    actor_user_id: Optional[int],
    actor_role: str,
    action_type: str,
    target_type: str,
    target_id: str,
    details: Dict[str, Any],
) -> int:
    """Insert an audit log record."""
    return db.insert(
        "audit_logs",
        {
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "action_type": action_type,
            "target_type": target_type,
            "target_id": str(target_id),
            "details_json": json.dumps(details),
            "created_at": utcnow_iso(),
        },
    )


def get_app_setting(db: DatabaseSession, key: str, default: Any = None) -> Any:
    row = db.fetch_one("SELECT value_json FROM application_settings WHERE key = ?", (key,))
    if not row:
        return default
    try:
        return json.loads(row.value_json)
    except Exception:
        return default


def set_app_setting(
    db: DatabaseSession, key: str, value: Any, admin_id: Optional[int] = None
) -> None:
    now = utcnow_iso()
    val_str = json.dumps(value)
    existing = db.fetch_one("SELECT id FROM application_settings WHERE key = ?", (key,))
    if existing:
        db.execute(
            "UPDATE application_settings SET value_json = ?, updated_by_admin_id = ?, updated_at = ? WHERE key = ?",
            (val_str, admin_id, now, key),
        )
    else:
        db.insert(
            "application_settings",
            {
                "key": key,
                "value_json": val_str,
                "updated_by_admin_id": admin_id,
                "updated_at": now,
            },
        )


def get_public_payment_settings(db: DatabaseSession) -> Dict[str, Any]:
    """Return payment configuration safe for browser/admin display (NO secret keys)."""
    merchant_name = get_app_setting(db, "merchant_name", settings.DEFAULT_MERCHANT_NAME)
    upi_vpa = get_app_setting(db, "upi_vpa", settings.DEFAULT_UPI_VPA)
    merchant_description = get_app_setting(
        db, "merchant_description", settings.DEFAULT_MERCHANT_DESCRIPTION
    )
    currency = get_app_setting(db, "currency", settings.DEFAULT_CURRENCY)
    configured_mode = get_app_setting(db, "payment_mode", settings.DEFAULT_PAYMENT_MODE)
    timeout_min = int(
        get_app_setting(
            db,
            "default_reservation_timeout_minutes",
            settings.DEFAULT_RESERVATION_TIMEOUT_MINUTES,
        )
    )
    safety_factor = float(
        get_app_setting(db, "default_safety_factor", settings.DEFAULT_SAFETY_FACTOR)
    )
    allow_unpaid_exception = bool(
        get_app_setting(db, "allow_unpaid_handover_exception", False)
    )

    gateway_ready = settings.is_gateway_configured
    effective_online_mode = (
        "GATEWAY_UPI"
        if (configured_mode == "GATEWAY_UPI" and gateway_ready)
        else "DIRECT_UPI"
    )

    return {
        "merchant_name": merchant_name,
        "upi_vpa": upi_vpa,
        "merchant_description": merchant_description,
        "currency": currency,
        "configured_payment_mode": configured_mode,
        "effective_online_mode": effective_online_mode,
        "gateway_configured": gateway_ready,
        "gateway_provider": "razorpay" if gateway_ready else None,
        "gateway_key_id_public": settings.RAZORPAY_KEY_ID if gateway_ready else None,
        "gateway_test_mode": settings.RAZORPAY_TEST_MODE if gateway_ready else None,
        "mode_label": (
            "Gateway — automatic verification configured (Razorpay)"
            if effective_online_mode == "GATEWAY_UPI"
            else "Direct UPI — manual administrator verification required"
        ),
        "default_reservation_timeout_minutes": timeout_min,
        "default_safety_factor": safety_factor,
        "allow_unpaid_handover_exception": allow_unpaid_exception,
        "timezone": get_app_setting(db, "timezone", settings.CANTEEN_TIMEZONE),
        "architecture_notice": (
            "In Direct UPI mode, the configured VPA generates a standard UPI payment intent/QR and requires "
            "manual administrator reconciliation against actual bank/UPI records. In Gateway mode, settlement "
            "is governed by the merchant account linked to the provider credentials."
        ),
    }


def build_upi_intent_uri(
    payee_vpa: str,
    payee_name: str,
    amount_paise: int,
    payment_reference: str,
    transaction_note: str,
    currency: str = "INR",
) -> str:
    """Build a standard RFC-3986 URI-encoded NPCI `upi://pay` intent string."""
    amount_str = f"{amount_paise / 100.0:.2f}"
    pa_enc = quote(payee_vpa.strip(), safe="@._-")
    pn_enc = quote(payee_name.strip(), safe="")
    tr_enc = quote(payment_reference.strip(), safe="-_")
    tn_enc = quote(transaction_note.strip(), safe="")
    cu_enc = quote(currency.strip().upper(), safe="")
    return (
        f"upi://pay?pa={pa_enc}&pn={pn_enc}&am={amount_str}"
        f"&cu={cu_enc}&tr={tr_enc}&tn={tn_enc}"
    )


class RazorpayGatewayAdapter:
    """Concrete payment gateway adapter for Razorpay UPI checkout and signature verification."""

    @property
    def key_id(self) -> str:
        return settings.RAZORPAY_KEY_ID

    @property
    def key_secret(self) -> str:
        return settings.RAZORPAY_KEY_SECRET

    @property
    def webhook_secret(self) -> str:
        return settings.RAZORPAY_WEBHOOK_SECRET

    @property
    def test_mode(self) -> bool:
        return settings.RAZORPAY_TEST_MODE

    @property
    def is_configured(self) -> bool:
        return settings.is_gateway_configured

    def create_checkout_order(
        self, internal_order_ref: str, amount_paise: int, currency: str = "INR"
    ) -> Dict[str, Any]:
        if not self.is_configured:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment gateway credentials are not configured. Use Direct UPI or Pay at Canteen.",
            )
        # Deterministic or unique provider order identifier tied to server session
        digest = hashlib.sha256(
            f"{internal_order_ref}:{amount_paise}:{secrets.token_hex(6)}".encode("utf-8")
        ).hexdigest()[:14]
        provider_order_id = f"order_{digest}"
        return {
            "provider_name": "razorpay",
            "provider_order_id": provider_order_id,
            "key_id": self.key_id,
            "amount_paise": amount_paise,
            "currency": currency,
            "merchant_order_reference": internal_order_ref,
            "test_mode": self.test_mode,
        }

    def verify_callback_signature(
        self, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str
    ) -> bool:
        if not self.is_configured or not razorpay_signature:
            return False
        msg = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
        expected = hmac.new(
            self.key_secret.encode("utf-8"), msg, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature.strip())

    def verify_webhook_signature(self, raw_body: bytes, header_signature: str) -> bool:
        if not self.is_configured or not header_signature:
            return False
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, header_signature.strip())


gateway_adapter = RazorpayGatewayAdapter()


def submit_student_utr(
    db: DatabaseSession,
    student_id: int,
    order_id: int,
    utr_number: str,
) -> Dict[str, Any]:
    """Record a student-reported UTR reference for a pending Direct UPI payment.

    IMPORTANT: Does NOT mark the payment as PAID. Status remains PENDING until
    an authorized administrator reconciles the transaction against bank records.
    """
    with db.atomic():
        order = db.fetch_one(
            "SELECT * FROM orders WHERE id = ? AND student_id = ?",
            (order_id, student_id),
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")
        payment = db.fetch_one(
            "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1",
            (order_id,),
        )
        if not payment:
            raise HTTPException(status_code=404, detail="Payment record not found.")
        if payment.payment_status == PaymentStatus.PAID.value:
            raise HTTPException(
                status_code=400, detail="Payment is already verified as PAID."
            )
        # Ensure the same UTR is not already claimed by another order
        dup = db.fetch_one(
            "SELECT id, order_id FROM payments WHERE submitted_utr = ? AND order_id != ?",
            (utr_number, order_id),
        )
        if dup:
            raise HTTPException(
                status_code=409,
                detail="This UTR / transaction reference has already been submitted for another order.",
            )
        now = utcnow_iso()
        db.update_by_id(
            "payments",
            payment.id,
            {
                "submitted_utr": utr_number,
                "utr_submitted_at": now,
                "updated_at": now,
            },
        )
        log_audit(
            db,
            actor_user_id=student_id,
            actor_role="student",
            action_type="STUDENT_UTR_SUBMITTED",
            target_type="payment",
            target_id=payment.payment_reference,
            details={"order_id": order_id, "submitted_utr": utr_number},
        )
        updated = db.fetch_one("SELECT * FROM payments WHERE id = ?", (payment.id,))
        return {
            "payment_id": updated.id,
            "payment_reference": updated.payment_reference,
            "payment_status": updated.payment_status,
            "submitted_utr": updated.submitted_utr,
            "utr_submitted_at": updated.utr_submitted_at,
            "notice": (
                "UTR recorded for reconciliation. Payment status remains PENDING until an "
                "administrator verifies receipt against the canteen bank/UPI statement."
            ),
        }


def _finalize_verified_payment_in_tx(
    db: DatabaseSession,
    order: RowObject,
    payment: RowObject,
    verified_amount_paise: int,
    verification_method: str,
    admin_id: Optional[int],
    notes: str,
    provider_payment_id: Optional[str] = None,
    webhook_event_id: Optional[str] = None,
    bank_or_utr_reference: Optional[str] = None,
) -> Dict[str, Any]:
    """Internal helper executed inside `with db.atomic():` to mark an order PAID safely."""
    if verified_amount_paise != int(order.total_paise):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Payment amount mismatch: expected {order.total_paise} paise, "
                f"got {verified_amount_paise} paise."
            ),
        )

    now = utcnow_iso()
    pay_updates: Dict[str, Any] = {
        "payment_status": PaymentStatus.PAID.value,
        "verified_amount_paise": verified_amount_paise,
        "verification_method": verification_method,
        "verified_by_admin_id": admin_id,
        "reconciliation_notes": notes,
        "verified_at": now,
        "updated_at": now,
    }
    if provider_payment_id:
        pay_updates["provider_payment_id"] = provider_payment_id
    if webhook_event_id:
        pay_updates["webhook_event_id"] = webhook_event_id
    if bank_or_utr_reference:
        pay_updates["submitted_utr"] = bank_or_utr_reference

    db.update_by_id("payments", payment.id, pay_updates)

    # Upgrade order payment_status to PAID; if order was PLACED, advance to CONFIRMED
    new_order_status = order.order_status
    order_updates: Dict[str, Any] = {
        "payment_status": PaymentStatus.PAID.value,
        "updated_at": now,
    }
    if order.order_status == OrderStatus.PLACED.value:
        new_order_status = OrderStatus.CONFIRMED.value
        order_updates["order_status"] = new_order_status
        order_updates["confirmed_at"] = now
        db.insert(
            "order_status_history",
            {
                "order_id": order.id,
                "from_status": OrderStatus.PLACED.value,
                "to_status": OrderStatus.CONFIRMED.value,
                "actor_user_id": admin_id,
                "actor_role": "admin" if admin_id else "system_gateway",
                "notes": f"Order confirmed upon verified payment ({verification_method}).",
                "created_at": now,
            },
        )
    elif order.order_status == OrderStatus.CANCELLED.value:
        # Late payment recovery: keep record of verified payment and flag in notes
        order_updates["notes"] = (
            (order.notes or "")
            + f" [LATE PAYMENT VERIFIED at {now}: reconcile slot or refund]"
        ).strip()

    db.update_by_id("orders", order.id, order_updates)

    # Upgrade slot reservation in place (TEMP_HOLD or CONFIRMED_UNPAID -> CONFIRMED_PAID)
    # Never creates a second reservation row for the same order!
    res = db.fetch_one(
        "SELECT * FROM slot_reservations WHERE order_id = ?", (order.id,)
    )
    if res:
        db.update_by_id(
            "slot_reservations",
            res.id,
            {
                "reservation_type": ReservationType.CONFIRMED_PAID.value,
                "status": (
                    ReservationStatus.ACTIVE.value
                    if order.order_status != OrderStatus.CANCELLED.value
                    else res.status
                ),
                "expires_at": None,
                "updated_at": now,
            },
        )

    # Ensure token is ACTIVE (if not already REDEEMED or CANCELLED)
    token = db.fetch_one("SELECT * FROM tokens WHERE order_id = ?", (order.id,))
    if token and token.token_status == TokenStatus.EXPIRED.value and order.order_status != OrderStatus.CANCELLED.value:
        db.update_by_id(
            "tokens",
            token.id,
            {"token_status": TokenStatus.ACTIVE.value, "updated_at": now},
        )

    return {
        "order_id": order.id,
        "order_reference": order.order_reference,
        "order_status": new_order_status,
        "payment_status": PaymentStatus.PAID.value,
        "payment_reference": payment.payment_reference,
        "verification_method": verification_method,
        "verified_at": now,
    }


def admin_reconcile_payment(
    db: DatabaseSession,
    admin_user: RowObject,
    payment_id: int,
    action: str,
    bank_or_utr_reference: Optional[str],
    notes: str,
) -> Dict[str, Any]:
    """Allow an authorized administrator to manually verify, fail, or refund a payment."""
    with db.atomic():
        payment = db.fetch_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
        if not payment:
            raise HTTPException(status_code=404, detail="Payment record not found.")
        order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (payment.order_id,))
        if not order:
            raise HTTPException(status_code=404, detail="Associated order not found.")

        now = utcnow_iso()
        act = action.strip().upper()

        if act == "VERIFY_PAID":
            if payment.payment_status == PaymentStatus.PAID.value:
                return {
                    "idempotent": True,
                    "message": "Payment was already verified as PAID.",
                    "order_id": order.id,
                    "payment_status": PaymentStatus.PAID.value,
                }
            if bank_or_utr_reference:
                dup = db.fetch_one(
                    "SELECT id FROM payments WHERE submitted_utr = ? AND id != ? AND payment_status = 'PAID'",
                    (bank_or_utr_reference.strip().upper(), payment.id),
                )
                if dup:
                    raise HTTPException(
                        status_code=409,
                        detail="This bank/UTR reference is already linked to another verified payment.",
                    )
            res = _finalize_verified_payment_in_tx(
                db=db,
                order=order,
                payment=payment,
                verified_amount_paise=payment.expected_amount_paise,
                verification_method=VerificationMethod.MANUAL_ADMIN_RECONCILIATION.value,
                admin_id=admin_user.id,
                notes=notes,
                bank_or_utr_reference=(
                    bank_or_utr_reference.strip().upper()
                    if bank_or_utr_reference
                    else payment.submitted_utr
                ),
            )
            log_audit(
                db,
                actor_user_id=admin_user.id,
                actor_role="admin",
                action_type="PAYMENT_RECONCILED_PAID",
                target_type="payment",
                target_id=payment.payment_reference,
                details={
                    "order_id": order.id,
                    "amount_paise": payment.expected_amount_paise,
                    "utr": bank_or_utr_reference or payment.submitted_utr,
                    "notes": notes,
                },
            )
            return res

        elif act == "MARK_FAILED":
            if payment.payment_status == PaymentStatus.PAID.value:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot mark an already verified PAID transaction as FAILED. Use RECORD_REFUND instead.",
                )
            db.update_by_id(
                "payments",
                payment.id,
                {
                    "payment_status": PaymentStatus.FAILED.value,
                    "verified_by_admin_id": admin_user.id,
                    "reconciliation_notes": notes,
                    "failed_at": now,
                    "updated_at": now,
                },
            )
            db.update_by_id(
                "orders",
                order.id,
                {
                    "payment_status": PaymentStatus.FAILED.value,
                    "order_status": OrderStatus.CANCELLED.value,
                    "cancelled_at": now,
                    "updated_at": now,
                },
            )
            # Release slot reservation capacity
            res_row = db.fetch_one(
                "SELECT * FROM slot_reservations WHERE order_id = ?", (order.id,)
            )
            if res_row and res_row.status == ReservationStatus.ACTIVE.value:
                db.update_by_id(
                    "slot_reservations",
                    res_row.id,
                    {"status": ReservationStatus.RELEASED.value, "updated_at": now},
                )
            tok_row = db.fetch_one("SELECT * FROM tokens WHERE order_id = ?", (order.id,))
            if tok_row:
                db.update_by_id(
                    "tokens",
                    tok_row.id,
                    {"token_status": TokenStatus.CANCELLED.value, "updated_at": now},
                )
            log_audit(
                db,
                actor_user_id=admin_user.id,
                actor_role="admin",
                action_type="PAYMENT_MARKED_FAILED",
                target_type="payment",
                target_id=payment.payment_reference,
                details={"order_id": order.id, "notes": notes},
            )
            return {
                "order_id": order.id,
                "order_reference": order.order_reference,
                "order_status": OrderStatus.CANCELLED.value,
                "payment_status": PaymentStatus.FAILED.value,
                "payment_reference": payment.payment_reference,
            }

        elif act == "RECORD_REFUND":
            if payment.payment_status != PaymentStatus.PAID.value:
                raise HTTPException(
                    status_code=400,
                    detail="Only verified PAID payments can be marked REFUNDED.",
                )
            db.update_by_id(
                "payments",
                payment.id,
                {
                    "payment_status": PaymentStatus.REFUNDED.value,
                    "refunded_amount_paise": payment.verified_amount_paise,
                    "verified_by_admin_id": admin_user.id,
                    "reconciliation_notes": notes,
                    "refunded_at": now,
                    "updated_at": now,
                },
            )
            db.update_by_id(
                "orders",
                order.id,
                {
                    "payment_status": PaymentStatus.REFUNDED.value,
                    "updated_at": now,
                },
            )
            log_audit(
                db,
                actor_user_id=admin_user.id,
                actor_role="admin",
                action_type="PAYMENT_REFUNDED",
                target_type="payment",
                target_id=payment.payment_reference,
                details={"order_id": order.id, "notes": notes},
            )
            return {
                "order_id": order.id,
                "order_reference": order.order_reference,
                "payment_status": PaymentStatus.REFUNDED.value,
                "payment_reference": payment.payment_reference,
            }
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported reconciliation action. Use VERIFY_PAID, MARK_FAILED, or RECORD_REFUND.",
            )


def record_counter_payment(
    db: DatabaseSession,
    admin_user: RowObject,
    order_id: int,
    collection_mode: str,
    transaction_reference: Optional[str],
    notes: Optional[str],
) -> Dict[str, Any]:
    """Record cash or UPI payment received at the canteen counter for an UNPAID order.

    Updates order & payment status to PAID while keeping the existing order identity
    and opaque QR token unchanged.
    """
    mode = collection_mode.strip().upper()
    if mode not in ("CASH", "UPI_AT_COUNTER"):
        raise HTTPException(
            status_code=400, detail="collection_mode must be CASH or UPI_AT_COUNTER."
        )

    with db.atomic():
        order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")
        if order.payment_status == PaymentStatus.PAID.value:
            return {
                "idempotent": True,
                "message": "Order is already marked PAID.",
                "order_id": order.id,
                "payment_status": PaymentStatus.PAID.value,
            }
        if order.order_status == OrderStatus.CANCELLED.value:
            raise HTTPException(
                status_code=400, detail="Cannot record counter payment for a cancelled order."
            )

        payment = db.fetch_one(
            "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1",
            (order_id,),
        )
        now = utcnow_iso()
        pay_method_val = (
            "PAY_AT_CANTEEN_CASH" if mode == "CASH" else "PAY_AT_CANTEEN_UPI"
        )
        rec_notes = notes or f"Collected at canteen counter via {mode}."

        if payment:
            db.update_by_id(
                "payments",
                payment.id,
                {
                    "payment_method": pay_method_val,
                    "payment_status": PaymentStatus.PAID.value,
                    "verified_amount_paise": order.total_paise,
                    "submitted_utr": transaction_reference,
                    "verification_method": VerificationMethod.COUNTER_COLLECTION.value,
                    "verified_by_admin_id": admin_user.id,
                    "reconciliation_notes": rec_notes,
                    "verified_at": now,
                    "updated_at": now,
                },
            )
            pay_ref = payment.payment_reference
        else:
            pay_ref = f"PAY-CTR-{secrets.token_hex(4).upper()}"
            db.insert(
                "payments",
                {
                    "order_id": order.id,
                    "payment_reference": pay_ref,
                    "payment_method": pay_method_val,
                    "payment_status": PaymentStatus.PAID.value,
                    "expected_amount_paise": order.total_paise,
                    "verified_amount_paise": order.total_paise,
                    "refunded_amount_paise": 0,
                    "currency": "INR",
                    "submitted_utr": transaction_reference,
                    "verification_method": VerificationMethod.COUNTER_COLLECTION.value,
                    "verified_by_admin_id": admin_user.id,
                    "reconciliation_notes": rec_notes,
                    "created_at": now,
                    "verified_at": now,
                    "updated_at": now,
                },
            )

        new_order_status = order.order_status
        order_updates: Dict[str, Any] = {
            "payment_status": PaymentStatus.PAID.value,
            "updated_at": now,
        }
        if order.order_status == OrderStatus.PLACED.value:
            new_order_status = OrderStatus.CONFIRMED.value
            order_updates["order_status"] = new_order_status
            order_updates["confirmed_at"] = now

        db.update_by_id("orders", order.id, order_updates)

        # Upgrade reservation type from CONFIRMED_UNPAID -> CONFIRMED_PAID in place
        res_row = db.fetch_one(
            "SELECT * FROM slot_reservations WHERE order_id = ?", (order.id,)
        )
        if res_row:
            db.update_by_id(
                "slot_reservations",
                res_row.id,
                {
                    "reservation_type": ReservationType.CONFIRMED_PAID.value,
                    "updated_at": now,
                },
            )

        tok = db.fetch_one("SELECT * FROM tokens WHERE order_id = ?", (order.id,))
        log_audit(
            db,
            actor_user_id=admin_user.id,
            actor_role="admin",
            action_type="COUNTER_PAYMENT_RECORDED",
            target_type="order",
            target_id=order.order_reference,
            details={
                "order_id": order.id,
                "collection_mode": mode,
                "amount_paise": order.total_paise,
                "transaction_reference": transaction_reference,
                "token_number": tok.token_number if tok else None,
            },
        )
        return {
            "order_id": order.id,
            "order_reference": order.order_reference,
            "order_status": new_order_status,
            "payment_status": PaymentStatus.PAID.value,
            "payment_reference": pay_ref,
            "collection_mode": mode,
            "token_number": tok.token_number if tok else None,
            "opaque_token": tok.opaque_token if tok else None,
            "verified_at": now,
        }


def verify_gateway_callback(
    db: DatabaseSession,
    order_id: int,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> Dict[str, Any]:
    """Verify signed Razorpay callback and mark payment PAID if valid."""
    if not gateway_adapter.is_configured:
        raise HTTPException(
            status_code=400, detail="Gateway mode is not configured on the server."
        )
    if not gateway_adapter.verify_callback_signature(
        razorpay_order_id, razorpay_payment_id, razorpay_signature
    ):
        raise HTTPException(
            status_code=400, detail="Invalid payment gateway signature."
        )

    with db.atomic():
        order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")
        payment = db.fetch_one(
            "SELECT * FROM payments WHERE order_id = ? AND provider_order_id = ?",
            (order_id, razorpay_order_id),
        )
        if not payment:
            raise HTTPException(
                status_code=400,
                detail="Provider order reference does not match this order.",
            )
        if payment.payment_status == PaymentStatus.PAID.value:
            return {
                "idempotent": True,
                "order_id": order.id,
                "payment_status": PaymentStatus.PAID.value,
                "payment_reference": payment.payment_reference,
            }
        res = _finalize_verified_payment_in_tx(
            db=db,
            order=order,
            payment=payment,
            verified_amount_paise=payment.expected_amount_paise,
            verification_method=VerificationMethod.GATEWAY_SIGNATURE.value,
            admin_id=None,
            notes="Verified via HMAC-SHA256 Razorpay checkout signature.",
            provider_payment_id=razorpay_payment_id,
        )
        log_audit(
            db,
            actor_user_id=order.student_id,
            actor_role="system_gateway",
            action_type="GATEWAY_CALLBACK_VERIFIED",
            target_type="payment",
            target_id=payment.payment_reference,
            details={
                "order_id": order.id,
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
            },
        )
        return res


def process_gateway_webhook(
    db: DatabaseSession,
    raw_body: bytes,
    signature_header: str,
) -> Dict[str, Any]:
    """Verify and idempotently process a signed Razorpay webhook payload."""
    if not gateway_adapter.is_configured:
        raise HTTPException(
            status_code=400, detail="Gateway webhook secret is not configured."
        )
    if not gateway_adapter.verify_webhook_signature(raw_body, signature_header):
        raise HTTPException(
            status_code=400, detail="Invalid webhook HMAC-SHA256 signature."
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed JSON webhook body.") from exc

    event_id = str(payload.get("event_id") or payload.get("id") or "")
    event_type = str(payload.get("event") or "")
    payment_entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )
    provider_order_id = str(payment_entity.get("order_id") or "")
    provider_payment_id = str(payment_entity.get("id") or "")
    amount_paise = int(payment_entity.get("amount") or 0)
    currency = str(payment_entity.get("currency") or "").upper()
    notes_obj = payment_entity.get("notes") or {}
    merchant_order_ref = notes_obj.get("order_reference")

    if currency != "INR":
        raise HTTPException(
            status_code=400, detail=f"Invalid payment currency '{currency}'; expected INR."
        )

    with db.atomic():
        if event_id:
            existing_ev = db.fetch_one(
                "SELECT * FROM payments WHERE webhook_event_id = ?", (event_id,)
            )
            if existing_ev:
                return {
                    "status": "IDEMPOTENT_DUPLICATE",
                    "webhook_event_id": event_id,
                    "payment_reference": existing_ev.payment_reference,
                    "payment_status": existing_ev.payment_status,
                }

        if provider_payment_id:
            existing_pid = db.fetch_one(
                "SELECT * FROM payments WHERE provider_payment_id = ? AND payment_status = 'PAID'",
                (provider_payment_id,),
            )
            if existing_pid:
                return {
                    "status": "IDEMPOTENT_DUPLICATE",
                    "provider_payment_id": provider_payment_id,
                    "payment_reference": existing_pid.payment_reference,
                    "payment_status": existing_pid.payment_status,
                }

        payment = db.fetch_one(
            "SELECT * FROM payments WHERE provider_order_id = ?",
            (provider_order_id,),
        )
        if not payment:
            raise HTTPException(
                status_code=404,
                detail="Unknown provider_order_id in webhook payload.",
            )

        order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (payment.order_id,))
        if not order:
            raise HTTPException(status_code=404, detail="Associated order not found.")

        if merchant_order_ref and merchant_order_ref != order.order_reference:
            raise HTTPException(
                status_code=400,
                detail="Webhook merchant order_reference does not match internal order.",
            )

        if event_type in ("payment.captured", "order.paid"):
            res = _finalize_verified_payment_in_tx(
                db=db,
                order=order,
                payment=payment,
                verified_amount_paise=amount_paise,
                verification_method=VerificationMethod.GATEWAY_WEBHOOK.value,
                admin_id=None,
                notes=f"Verified via signed webhook event {event_type}.",
                provider_payment_id=provider_payment_id,
                webhook_event_id=event_id or None,
            )
            log_audit(
                db,
                actor_user_id=None,
                actor_role="system_webhook",
                action_type="GATEWAY_WEBHOOK_PAID",
                target_type="payment",
                target_id=payment.payment_reference,
                details={
                    "event_id": event_id,
                    "provider_order_id": provider_order_id,
                    "provider_payment_id": provider_payment_id,
                    "amount_paise": amount_paise,
                },
            )
            return {"status": "PROCESSED_PAID", **res}
        elif event_type == "payment.failed":
            now = utcnow_iso()
            if payment.payment_status != PaymentStatus.PAID.value:
                db.update_by_id(
                    "payments",
                    payment.id,
                    {
                        "payment_status": PaymentStatus.FAILED.value,
                        "webhook_event_id": event_id or None,
                        "failed_at": now,
                        "updated_at": now,
                    },
                )
            return {
                "status": "PROCESSED_FAILED",
                "payment_reference": payment.payment_reference,
            }
        return {"status": "IGNORED_EVENT", "event": event_type}
