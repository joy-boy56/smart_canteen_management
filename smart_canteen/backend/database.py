"""Database access layer with SQLAlchemy + SQLite atomic transaction support.

Supports standard SQLAlchemy declarative models when installed in the Python
environment, and provides a SQLite transactional session engine with
`PRAGMA foreign_keys = ON` and `BEGIN IMMEDIATE` write locks so atomic slot
reservations, payment verification, and single token redemption are strictly serialized.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import threading
from typing import Any, Dict, Generator, Iterable, List, Optional

from backend.config import settings

# Global lock for serializing write transactions in multi-threaded local SQLite tests
_WRITE_LOCK = threading.RLock()

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    registration_number TEXT UNIQUE,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL,
    department TEXT,
    academic_year TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'admin')),
    consent_accepted INTEGER NOT NULL DEFAULT 1,
    preferences_json TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_reg_no ON users(registration_number);

CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    price_paise INTEGER NOT NULL CHECK (price_paise >= 0),
    image_url TEXT,
    prep_time_minutes REAL NOT NULL DEFAULT 5.0 CHECK (prep_time_minutes > 0),
    workload_factor REAL NOT NULL DEFAULT 1.0 CHECK (workload_factor > 0),
    is_available INTEGER NOT NULL DEFAULT 1,
    is_archived INTEGER NOT NULL DEFAULT 0,
    stock_quantity INTEGER CHECK (stock_quantity IS NULL OR stock_quantity >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_menu_category ON menu_items(category, is_available, is_archived);

CREATE TABLE IF NOT EXISTS pickup_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_date TEXT NOT NULL,
    label TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    admin_cap INTEGER NOT NULL CHECK (admin_cap >= 0),
    allocated_counters INTEGER NOT NULL DEFAULT 2 CHECK (allocated_counters >= 1),
    service_rate_per_counter REAL NOT NULL DEFAULT 0.60 CHECK (service_rate_per_counter > 0),
    safety_factor REAL NOT NULL DEFAULT 0.85 CHECK (safety_factor > 0 AND safety_factor <= 1.0),
    cutoff_minutes_before INTEGER NOT NULL DEFAULT 5 CHECK (cutoff_minutes_before >= 0),
    hold_timeout_minutes INTEGER NOT NULL DEFAULT 10 CHECK (hold_timeout_minutes >= 1),
    prep_buffer_minutes INTEGER NOT NULL DEFAULT 5 CHECK (prep_buffer_minutes >= 0),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'DISABLED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(slot_date, start_time, end_time)
);
CREATE INDEX IF NOT EXISTS idx_slots_date_status ON pickup_slots(slot_date, status);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_reference TEXT NOT NULL UNIQUE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    slot_id INTEGER NOT NULL REFERENCES pickup_slots(id) ON DELETE RESTRICT,
    order_status TEXT NOT NULL DEFAULT 'PLACED' CHECK (
        order_status IN ('PLACED', 'CONFIRMED', 'PREPARING', 'READY', 'COMPLETED', 'CANCELLED')
    ),
    payment_method TEXT NOT NULL CHECK (
        payment_method IN ('ONLINE_UPI', 'GATEWAY_UPI', 'PAY_AT_CANTEEN')
    ),
    payment_status TEXT NOT NULL CHECK (
        payment_status IN ('PENDING', 'PAID', 'FAILED', 'UNPAID', 'REFUNDED', 'PARTIALLY_REFUNDED')
    ),
    total_paise INTEGER NOT NULL CHECK (total_paise >= 0),
    workload_units REAL NOT NULL DEFAULT 1.0,
    estimated_wait_minutes REAL,
    notes TEXT,
    placed_at TEXT NOT NULL,
    confirmed_at TEXT,
    preparing_at TEXT,
    ready_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_student ON orders(student_id, placed_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_slot ON orders(slot_id, order_status, payment_status);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status, payment_status);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    menu_item_id INTEGER NOT NULL REFERENCES menu_items(id) ON DELETE RESTRICT,
    item_name_snapshot TEXT NOT NULL,
    unit_price_paise_snapshot INTEGER NOT NULL CHECK (unit_price_paise_snapshot >= 0),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    prep_time_snapshot REAL NOT NULL DEFAULT 5.0,
    workload_factor_snapshot REAL NOT NULL DEFAULT 1.0,
    subtotal_paise INTEGER NOT NULL CHECK (subtotal_paise >= 0)
);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);

CREATE TABLE IF NOT EXISTS slot_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL REFERENCES pickup_slots(id) ON DELETE CASCADE,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reservation_type TEXT NOT NULL CHECK (
        reservation_type IN ('TEMP_HOLD', 'CONFIRMED_PAID', 'CONFIRMED_UNPAID')
    ),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (
        status IN ('ACTIVE', 'EXPIRED', 'RELEASED')
    ),
    workload_units REAL NOT NULL DEFAULT 1.0,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reservations_slot_active ON slot_reservations(slot_id, status, reservation_type);

CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
    token_number TEXT NOT NULL UNIQUE,
    opaque_token TEXT NOT NULL UNIQUE,
    token_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (
        token_status IN ('ACTIVE', 'EXPIRED', 'CANCELLED', 'REDEEMED')
    ),
    expires_at TEXT,
    redeemed_at TEXT,
    redeemed_by_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    redemption_notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_opaque ON tokens(opaque_token);
CREATE INDEX IF NOT EXISTS idx_tokens_number ON tokens(token_number);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    payment_reference TEXT NOT NULL UNIQUE,
    payment_method TEXT NOT NULL CHECK (
        payment_method IN ('DIRECT_UPI', 'RAZORPAY_GATEWAY', 'PAY_AT_CANTEEN_CASH', 'PAY_AT_CANTEEN_UPI', 'PAY_AT_CANTEEN')
    ),
    payment_status TEXT NOT NULL CHECK (
        payment_status IN ('PENDING', 'PAID', 'FAILED', 'UNPAID', 'REFUNDED', 'PARTIALLY_REFUNDED')
    ),
    expected_amount_paise INTEGER NOT NULL CHECK (expected_amount_paise >= 0),
    verified_amount_paise INTEGER NOT NULL DEFAULT 0 CHECK (verified_amount_paise >= 0),
    refunded_amount_paise INTEGER NOT NULL DEFAULT 0 CHECK (refunded_amount_paise >= 0),
    currency TEXT NOT NULL DEFAULT 'INR',
    payee_vpa_snapshot TEXT,
    payee_name_snapshot TEXT,
    upi_intent_uri TEXT,
    submitted_utr TEXT,
    utr_submitted_at TEXT,
    provider_name TEXT,
    provider_order_id TEXT,
    provider_payment_id TEXT UNIQUE,
    webhook_event_id TEXT UNIQUE,
    verification_method TEXT NOT NULL DEFAULT 'NONE' CHECK (
        verification_method IN ('NONE', 'MANUAL_ADMIN_RECONCILIATION', 'COUNTER_COLLECTION', 'GATEWAY_SIGNATURE', 'GATEWAY_WEBHOOK')
    ),
    verified_by_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reconciliation_notes TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    failed_at TEXT,
    refunded_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(payment_status);

CREATE TABLE IF NOT EXISTS order_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_role TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_history_order ON order_status_history(order_id, created_at);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_date TEXT NOT NULL,
    slot_id INTEGER REFERENCES pickup_slots(id) ON DELETE SET NULL,
    slot_label TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_minutes REAL NOT NULL CHECK (duration_minutes > 0),
    event_type_measured TEXT NOT NULL DEFAULT 'PHYSICAL_ARRIVAL' CHECK (
        event_type_measured IN ('PHYSICAL_ARRIVAL', 'ORDER_PLACEMENT', 'PREP_SERVICE', 'COUNTER_COLLECTION')
    ),
    arrivals_count INTEGER NOT NULL CHECK (arrivals_count >= 0),
    served_count INTEGER NOT NULL CHECK (served_count >= 0),
    mean_service_time_minutes REAL NOT NULL CHECK (mean_service_time_minutes > 0),
    active_counters INTEGER NOT NULL CHECK (active_counters >= 1),
    start_queue_length INTEGER DEFAULT 0,
    end_queue_length INTEGER DEFAULT 0,
    mean_prep_time_minutes REAL,
    mean_collection_time_minutes REAL,
    source_type TEXT NOT NULL DEFAULT 'MANUAL_ENTRY' CHECK (
        source_type IN ('MANUAL_ENTRY', 'CSV_IMPORT', 'SYNTHETIC_DEMO')
    ),
    notes TEXT,
    created_by_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_date_slot ON observations(observation_date, slot_id);

CREATE TABLE IF NOT EXISTS application_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value_json TEXT NOT NULL,
    updated_by_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_role TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS simulation_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_name TEXT NOT NULL,
    experiment_type TEXT NOT NULL DEFAULT 'SLOT_AWARE_COMPARISON',
    random_seed INTEGER NOT NULL,
    replications INTEGER NOT NULL CHECK (replications >= 1),
    horizon_minutes REAL NOT NULL CHECK (horizon_minutes > 0),
    warmup_minutes REAL NOT NULL DEFAULT 0.0 CHECK (warmup_minutes >= 0),
    parameters_json TEXT NOT NULL,
    baseline_results_json TEXT NOT NULL,
    optimized_results_json TEXT,
    observation_ids_json TEXT NOT NULL DEFAULT '[]',
    created_by_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
"""


def utcnow_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class RowObject:
    """Dictionary-backed row object allowing both attribute and dict-key access."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self.__dict__["_data"] = dict(data)

    def __getattr__(self, item: str) -> Any:
        data = self.__dict__["_data"]
        if item in data:
            return data[item]
        raise AttributeError(f"RowObject has no attribute '{item}'")

    def __setattr__(self, key: str, value: Any) -> None:
        self.__dict__["_data"][key] = value

    def __getitem__(self, item: str) -> Any:
        return self.__dict__["_data"][item]

    def get(self, item: str, default: Any = None) -> Any:
        return self.__dict__["_data"].get(item, default)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__["_data"])


class DatabaseSession:
    """Transactional SQLite session with atomic write locking (`BEGIN IMMEDIATE`)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA busy_timeout = 30000;")
        self._in_tx = False
        self._has_lock = False

    @contextmanager
    def atomic(self) -> Generator["DatabaseSession", None, None]:
        """Execute an atomic transaction with `BEGIN IMMEDIATE` write lock."""
        if self._in_tx:
            yield self
            return
        _WRITE_LOCK.acquire()
        self._has_lock = True
        try:
            self.conn.execute("BEGIN IMMEDIATE;")
            self._in_tx = True
            try:
                yield self
                self.conn.execute("COMMIT;")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK;")
                raise
            finally:
                self._in_tx = False
        finally:
            self._has_lock = False
            _WRITE_LOCK.release()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def fetch_one(
        self, sql: str, params: Iterable[Any] = ()
    ) -> Optional[RowObject]:
        cur = self.conn.execute(sql, tuple(params))
        row = cur.fetchone()
        return RowObject(dict(row)) if row is not None else None

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> List[RowObject]:
        cur = self.conn.execute(sql, tuple(params))
        return [RowObject(dict(r)) for r in cur.fetchall()]

    def insert(self, table: str, values: Dict[str, Any]) -> int:
        cols = list(values.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
        cur = self.conn.execute(sql, tuple(values[c] for c in cols))
        return int(cur.lastrowid)

    def update_by_id(
        self, table: str, row_id: int, values: Dict[str, Any]
    ) -> int:
        if not values:
            return 0
        assignments = ", ".join([f"{k} = ?" for k in values.keys()])
        params = list(values.values()) + [row_id]
        cur = self.conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?", tuple(params)
        )
        return cur.rowcount

    def commit(self) -> None:
        if self.conn.in_transaction:
            self.conn.execute("COMMIT;")
        self._in_tx = False
        if self._has_lock:
            self._has_lock = False
            _WRITE_LOCK.release()

    def rollback(self) -> None:
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK;")
        self._in_tx = False
        if self._has_lock:
            self._has_lock = False
            _WRITE_LOCK.release()

    def close(self) -> None:
        try:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK;")
        except Exception:
            pass
        finally:
            if self._has_lock:
                self._has_lock = False
                _WRITE_LOCK.release()
            self.conn.close()


_ACTIVE_DB_PATH: Optional[str] = None


def set_database_path(db_path: str) -> None:
    """Override the active SQLite database file path (useful for isolated tests)."""
    global _ACTIVE_DB_PATH
    _ACTIVE_DB_PATH = db_path


def get_database_path() -> str:
    """Return currently configured SQLite database file path."""
    return _ACTIVE_DB_PATH or settings.sqlite_path


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize all database tables, constraints, indexes, and default settings."""
    target_path = db_path or get_database_path()
    with _WRITE_LOCK:
        conn = sqlite3.connect(target_path, timeout=30.0)
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            now = utcnow_iso()
            defaults = {
                "merchant_name": settings.DEFAULT_MERCHANT_NAME,
                "upi_vpa": settings.DEFAULT_UPI_VPA,
                "merchant_description": settings.DEFAULT_MERCHANT_DESCRIPTION,
                "currency": settings.DEFAULT_CURRENCY,
                "payment_mode": (
                    "GATEWAY_UPI"
                    if (
                        settings.DEFAULT_PAYMENT_MODE == "GATEWAY_UPI"
                        and settings.is_gateway_configured
                    )
                    else "DIRECT_UPI"
                ),
                "timezone": settings.CANTEEN_TIMEZONE,
                "require_registration_number": settings.REQUIRE_REGISTRATION_NUMBER,
                "default_reservation_timeout_minutes": settings.DEFAULT_RESERVATION_TIMEOUT_MINUTES,
                "default_safety_factor": settings.DEFAULT_SAFETY_FACTOR,
                "allow_unpaid_handover_exception": False,
            }
            for k, v in defaults.items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO application_settings (key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (k, json.dumps(v), now),
                )
            conn.commit()
        finally:
            conn.close()


def get_db() -> Generator[DatabaseSession, None, None]:
    """FastAPI dependency yielding a transactional DatabaseSession."""
    session = DatabaseSession(get_database_path())
    try:
        yield session
    finally:
        session.close()
