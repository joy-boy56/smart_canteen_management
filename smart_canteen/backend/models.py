"""Domain enums and model definitions for Smart Canteen."""

from enum import Enum


class UserRole(str, Enum):
    STUDENT = "student"
    ADMIN = "admin"


class SlotStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    DISABLED = "DISABLED"


class ReservationType(str, Enum):
    TEMP_HOLD = "TEMP_HOLD"
    CONFIRMED_PAID = "CONFIRMED_PAID"
    CONFIRMED_UNPAID = "CONFIRMED_UNPAID"


class ReservationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class OrderStatus(str, Enum):
    PLACED = "PLACED"
    CONFIRMED = "CONFIRMED"
    PREPARING = "PREPARING"
    READY = "READY"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PaymentMethod(str, Enum):
    ONLINE_UPI = "ONLINE_UPI"
    GATEWAY_UPI = "GATEWAY_UPI"
    PAY_AT_CANTEEN = "PAY_AT_CANTEEN"


class PaymentRecordMethod(str, Enum):
    DIRECT_UPI = "DIRECT_UPI"
    RAZORPAY_GATEWAY = "RAZORPAY_GATEWAY"
    PAY_AT_CANTEEN_CASH = "PAY_AT_CANTEEN_CASH"
    PAY_AT_CANTEEN_UPI = "PAY_AT_CANTEEN_UPI"
    PAY_AT_CANTEEN = "PAY_AT_CANTEEN"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    UNPAID = "UNPAID"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class TokenStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REDEEMED = "REDEEMED"


class VerificationMethod(str, Enum):
    NONE = "NONE"
    MANUAL_ADMIN_RECONCILIATION = "MANUAL_ADMIN_RECONCILIATION"
    COUNTER_COLLECTION = "COUNTER_COLLECTION"
    GATEWAY_SIGNATURE = "GATEWAY_SIGNATURE"
    GATEWAY_WEBHOOK = "GATEWAY_WEBHOOK"


class ObservationEventType(str, Enum):
    PHYSICAL_ARRIVAL = "PHYSICAL_ARRIVAL"
    ORDER_PLACEMENT = "ORDER_PLACEMENT"
    PREP_SERVICE = "PREP_SERVICE"
    COUNTER_COLLECTION = "COUNTER_COLLECTION"


class ObservationSourceType(str, Enum):
    MANUAL_ENTRY = "MANUAL_ENTRY"
    CSV_IMPORT = "CSV_IMPORT"
    SYNTHETIC_DEMO = "SYNTHETIC_DEMO"


# Validated Order Status State Machine Transitions
VALID_ORDER_TRANSITIONS = {
    OrderStatus.PLACED: {OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.CANCELLED},
    OrderStatus.PREPARING: {OrderStatus.READY, OrderStatus.CANCELLED},
    OrderStatus.READY: {OrderStatus.COMPLETED, OrderStatus.CANCELLED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELLED: set(),
}


def paise_to_rupees(paise: int) -> float:
    """Convert integer paise to INR decimal float for display."""
    return round(int(paise) / 100.0, 2)


def rupees_to_paise(rupees: float) -> int:
    """Convert INR rupees to exact integer paise."""
    return int(round(float(rupees) * 100))
