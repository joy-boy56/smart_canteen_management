"""Pydantic request and response schemas for Smart Canteen."""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, validator

from backend.models import (
    ObservationEventType,
    ObservationSourceType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    SlotStatus,
    TokenStatus,
    UserRole,
)

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
UPI_VPA_RE = re.compile(r"^[a-zA-Z0-9._\-]{2,256}@[a-zA-Z]{2,64}$")
TIME_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DATE_YYYYMMDD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StudentRegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    registration_number: Optional[str] = Field(None, max_length=40)
    email: str = Field(..., min_length=5, max_length=160)
    phone: str = Field(..., min_length=7, max_length=20)
    department: Optional[str] = Field(None, max_length=80)
    academic_year: Optional[str] = Field(None, max_length=40)
    password: str = Field(..., min_length=8, max_length=128)
    consent_accepted: bool = Field(...)

    @validator("email")
    def validate_email(cls, v: str) -> str:
        norm = v.strip().lower()
        if not EMAIL_RE.match(norm):
            raise ValueError("Invalid email address format.")
        return norm

    @validator("registration_number")
    def validate_reg_no(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip().upper()
        return cleaned if cleaned else None

    @validator("consent_accepted")
    def validate_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must accept the canteen data-use notice to register.")
        return v


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=160)
    password: str = Field(..., min_length=1, max_length=128)

    @validator("email")
    def normalize_login_email(cls, v: str) -> str:
        return v.strip().lower()


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    phone: Optional[str] = Field(None, min_length=7, max_length=20)
    department: Optional[str] = Field(None, max_length=80)
    academic_year: Optional[str] = Field(None, max_length=40)
    preferences: Optional[Dict[str, Any]] = None


class MenuItemCreateRequest(BaseModel):
    item_code: str = Field(..., min_length=2, max_length=32)
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field("", max_length=500)
    category: str = Field(..., min_length=2, max_length=60)
    price_inr: float = Field(..., gt=0, le=10000)
    image_url: Optional[str] = Field(None, max_length=500)
    prep_time_minutes: float = Field(5.0, gt=0, le=120)
    workload_factor: float = Field(1.0, gt=0, le=10.0)
    is_available: bool = True
    stock_quantity: Optional[int] = Field(None, ge=0)


class MenuItemUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    category: Optional[str] = Field(None, min_length=2, max_length=60)
    price_inr: Optional[float] = Field(None, gt=0, le=10000)
    image_url: Optional[str] = Field(None, max_length=500)
    prep_time_minutes: Optional[float] = Field(None, gt=0, le=120)
    workload_factor: Optional[float] = Field(None, gt=0, le=10.0)
    is_available: Optional[bool] = None
    is_archived: Optional[bool] = None
    stock_quantity: Optional[int] = Field(None, ge=0)
    clear_stock_limit: bool = False


class SlotCreateRequest(BaseModel):
    slot_date: str = Field(..., description="YYYY-MM-DD")
    label: str = Field(..., min_length=3, max_length=64)
    start_time: str = Field(..., description="HH:MM (24-hr)")
    end_time: str = Field(..., description="HH:MM (24-hr)")
    admin_cap: int = Field(..., ge=0, le=1000)
    allocated_counters: int = Field(2, ge=1, le=50)
    service_rate_per_counter: float = Field(0.60, gt=0, le=20.0)
    safety_factor: float = Field(0.85, gt=0.05, le=1.0)
    cutoff_minutes_before: int = Field(5, ge=0, le=1440)
    hold_timeout_minutes: int = Field(10, ge=1, le=120)
    prep_buffer_minutes: int = Field(5, ge=0, le=120)
    status: SlotStatus = SlotStatus.OPEN
    allow_overlap: bool = False

    @validator("slot_date")
    def check_date(cls, v: str) -> str:
        if not DATE_YYYYMMDD_RE.match(v.strip()):
            raise ValueError("slot_date must be in YYYY-MM-DD format.")
        return v.strip()

    @validator("start_time", "end_time")
    def check_time(cls, v: str) -> str:
        if not TIME_HHMM_RE.match(v.strip()):
            raise ValueError("Time must be in 24-hour HH:MM format (e.g. 12:15).")
        return v.strip()


class SlotUpdateRequest(BaseModel):
    label: Optional[str] = Field(None, min_length=3, max_length=64)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    admin_cap: Optional[int] = Field(None, ge=0, le=1000)
    allocated_counters: Optional[int] = Field(None, ge=1, le=50)
    service_rate_per_counter: Optional[float] = Field(None, gt=0, le=20.0)
    safety_factor: Optional[float] = Field(None, gt=0.05, le=1.0)
    cutoff_minutes_before: Optional[int] = Field(None, ge=0, le=1440)
    hold_timeout_minutes: Optional[int] = Field(None, ge=1, le=120)
    prep_buffer_minutes: Optional[int] = Field(None, ge=0, le=120)
    status: Optional[SlotStatus] = None


class CartItemInput(BaseModel):
    menu_item_id: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1, le=50)


class CartValidateRequest(BaseModel):
    items: List[CartItemInput] = Field(..., min_items=1)
    slot_id: Optional[int] = None


class OrderCreateRequest(BaseModel):
    items: List[CartItemInput] = Field(..., min_items=1)
    slot_id: int = Field(..., ge=1, description="Explicitly selected pickup slot ID")
    payment_method: PaymentMethod = Field(...)
    notes: Optional[str] = Field(None, max_length=300)


class DirectUpiUtrSubmitRequest(BaseModel):
    utr_number: str = Field(..., min_length=6, max_length=64)

    @validator("utr_number")
    def clean_utr(cls, v: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9\-_]", "", v.strip())
        if len(cleaned) < 6:
            raise ValueError("UTR / Transaction Reference must be at least 6 alphanumeric characters.")
        return cleaned.upper()


class GatewayVerifyCallbackRequest(BaseModel):
    order_id: int
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class AdminPaymentReconcileRequest(BaseModel):
    action: str = Field(..., description="VERIFY_PAID | MARK_FAILED | RECORD_REFUND")
    bank_or_utr_reference: Optional[str] = Field(None, max_length=80)
    notes: str = Field(..., min_length=3, max_length=400)


class AdminCounterPaymentRequest(BaseModel):
    collection_mode: str = Field(..., description="CASH | UPI_AT_COUNTER")
    transaction_reference: Optional[str] = Field(None, max_length=80)
    notes: Optional[str] = Field(None, max_length=300)


class AdminOrderStatusUpdateRequest(BaseModel):
    new_status: OrderStatus
    notes: Optional[str] = Field(None, max_length=300)
    authorized_unpaid_exception: bool = False


class TokenRedeemRequest(BaseModel):
    token_input: str = Field(..., min_length=3, max_length=256, description="Opaque token or token number")
    notes: Optional[str] = Field(None, max_length=300)
    authorized_unpaid_exception: bool = False


class UpiSettingsUpdateRequest(BaseModel):
    merchant_name: str = Field(..., min_length=2, max_length=100)
    upi_vpa: str = Field(..., min_length=4, max_length=120)
    merchant_description: Optional[str] = Field("College Smart Canteen Food Order", max_length=160)
    currency: str = Field("INR", min_length=3, max_length=3)
    payment_mode: str = Field("DIRECT_UPI", description="DIRECT_UPI | GATEWAY_UPI")
    default_reservation_timeout_minutes: Optional[int] = Field(None, ge=1, le=120)
    default_safety_factor: Optional[float] = Field(None, gt=0.1, le=1.0)
    allow_unpaid_handover_exception: Optional[bool] = None

    @validator("upi_vpa")
    def validate_vpa(cls, v: str) -> str:
        cleaned = v.strip()
        if not UPI_VPA_RE.match(cleaned):
            raise ValueError("Invalid UPI VPA format (expected handle@bank, e.g. canteen@sbi).")
        return cleaned


class ObservationCreateRequest(BaseModel):
    observation_date: str = Field(..., description="YYYY-MM-DD")
    slot_id: Optional[int] = None
    slot_label: str = Field(..., min_length=2, max_length=64)
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    duration_minutes: float = Field(..., gt=0, le=720)
    event_type_measured: ObservationEventType = ObservationEventType.PHYSICAL_ARRIVAL
    arrivals_count: int = Field(..., ge=0, le=10000)
    served_count: int = Field(..., ge=0, le=10000)
    mean_service_time_minutes: float = Field(..., gt=0, le=120)
    active_counters: int = Field(..., ge=1, le=50)
    start_queue_length: Optional[int] = Field(0, ge=0)
    end_queue_length: Optional[int] = Field(0, ge=0)
    mean_prep_time_minutes: Optional[float] = Field(None, gt=0)
    mean_collection_time_minutes: Optional[float] = Field(None, gt=0)
    source_type: ObservationSourceType = ObservationSourceType.MANUAL_ENTRY
    notes: Optional[str] = Field(None, max_length=400)


class OptimizationRequest(BaseModel):
    slot_date: str = Field(..., description="YYYY-MM-DD")
    demand_source: str = Field("HYBRID", description="OBSERVATIONS | BOOKINGS | HYBRID")
    total_available_counters: int = Field(12, ge=1, le=200)
    min_counters_per_slot: int = Field(1, ge=1, le=20)
    max_counters_per_slot: int = Field(6, ge=1, le=50)
    counter_cost_per_slot_inr: float = Field(150.0, ge=0)
    waiting_cost_per_customer_minute_inr: float = Field(12.0, ge=0)
    max_acceptable_wait_minutes: float = Field(6.0, gt=0, le=120)
    safety_factor_override: Optional[float] = Field(None, gt=0.1, le=1.0)
    service_rate_override: Optional[float] = Field(None, gt=0.05, le=20.0)
    objective_mode: str = Field("WEIGHTED_COST", description="WEIGHTED_COST | MIN_STAFF_UNDER_WAIT_TARGET")
    save_experiment: bool = True


class SimulationRunRequest(BaseModel):
    experiment_name: str = Field("Slot-Aware DES Evaluation", min_length=2, max_length=120)
    slot_date: Optional[str] = None
    arrival_rate_per_min: float = Field(1.0, ge=0.0, le=50.0)
    service_rate_per_counter_per_min: float = Field(0.60, gt=0.01, le=50.0)
    baseline_counters: int = Field(2, ge=1, le=50)
    optimized_counters: int = Field(3, ge=1, le=50)
    horizon_minutes: float = Field(60.0, gt=1.0, le=1440.0)
    warmup_minutes: float = Field(5.0, ge=0.0, le=240.0)
    replications: int = Field(20, ge=1, le=200)
    random_seed: int = Field(42, ge=1, le=999999999)
    scheduled_pickup_fraction: float = Field(0.65, ge=0.0, le=1.0)
    scheduled_jitter_sd_minutes: float = Field(2.0, ge=0.0, le=30.0)
    slot_configs: Optional[List[Dict[str, Any]]] = None
