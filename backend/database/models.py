from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Text, Float, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
from backend.database.database import Base

# Enums for statuses
class UserRole(str, enum.Enum):
    student = "student"
    admin = "admin"

class OrderStatus(str, enum.Enum):
    PLACED = "PLACED"
    CONFIRMED = "CONFIRMED"
    PREPARING = "PREPARING"
    READY = "READY"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

class TokenStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REDEEMED = "REDEEMED"

class ObservationSource(str, enum.Enum):
    manual = "manual"
    csv_import = "csv_import"
    system_derived = "system_derived"

# User model
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    student_reg_number = Column(String, unique=True, nullable=True)  # nullable for admins
    email = Column(String, unique=True, index=True, nullable=False)
    phone_number = Column(String, nullable=True)
    department = Column(String, nullable=True)
    year_semester = Column(String, nullable=True)
    upi_id = Column(String, nullable=True)  # for admins to receive UPI payments
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.student)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    orders = relationship("Order", back_populates="student")
    tokens = relationship("Token", back_populates="user")
    observations = relationship("Observation", back_populates="user")

# Menu item model
class MenuItem(Base):
    __tablename__ = "menu_items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String, nullable=False)
    price = Column(Integer, nullable=False)  # in paise to avoid floating point issues
    preparation_time_estimate = Column(Integer, nullable=True)  # in minutes
    image_url = Column(String, nullable=True)
    is_available = Column(Boolean, default=True)
    stock_quantity = Column(Integer, nullable=True)  # optional stock tracking
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    order_items = relationship("OrderItem", back_populates="menu_item")

# Order model
class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    pickup_slot_id = Column(Integer, ForeignKey("pickup_slots.id"), nullable=True)
    human_readable_id = Column(String, unique=True, index=True, nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.PLACED)
    total_amount = Column(Integer, nullable=False)  # in paise
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    requested_pickup_time = Column(DateTime(timezone=True), nullable=True)
    payment_method = Column(String, nullable=False, default='cash')  # 'cash' or 'online'
    payment_status = Column(String, nullable=False, default='unpaid')  # 'paid' or 'unpaid'
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    preparation_start_at = Column(DateTime(timezone=True), nullable=True)
    ready_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    student = relationship("User", back_populates="orders")
    pickup_slot = relationship("PickupSlot", back_populates="orders")
    order_items = relationship("OrderItem", back_populates="order")
    tokens = relationship("Token", back_populates="order")
    status_history = relationship("OrderStatusHistory", back_populates="order")

    @property
    def token(self):
        if self.tokens:
            return self.tokens[0].opaque_token
        return None

# Order item model
class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    item_name_snapshot = Column(String, nullable=False)
    unit_price_snapshot = Column(Integer, nullable=False)  # in paise
    quantity = Column(Integer, nullable=False)
    subtotal = Column(Integer, nullable=False)  # in paise

    # Relationships
    order = relationship("Order", back_populates="order_items")
    menu_item = relationship("MenuItem", back_populates="order_items")

# Token model
class Token(Base):
    __tablename__ = "tokens"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    opaque_token = Column(String, unique=True, index=True, nullable=False)
    status = Column(Enum(TokenStatus), default=TokenStatus.ACTIVE)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    redeemed_at = Column(DateTime(timezone=True), nullable=True)
    redeemed_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    order = relationship("Order", back_populates="tokens")
    user = relationship("User", foreign_keys=[user_id], back_populates="tokens")
    redeemed_by_user = relationship("User", foreign_keys=[redeemed_by])

# Order status history model
class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    previous_status = Column(Enum(OrderStatus), nullable=True)
    new_status = Column(Enum(OrderStatus), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())
    note = Column(Text, nullable=True)

    # Relationships
    order = relationship("Order", back_populates="status_history")
    actor = relationship("User")

# Observation model
class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, index=True)
    observation_date = Column(DateTime(timezone=True), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=True)  # optional, can be derived
    arrivals = Column(Integer, nullable=False)
    number_served = Column(Integer, nullable=True)
    mean_service_time_seconds = Column(Float, nullable=True)  # in seconds
    active_counters = Column(Integer, nullable=False)
    waiting_start = Column(Integer, nullable=True)
    waiting_end = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    source = Column(Enum(ObservationSource), default=ObservationSource.manual)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    user = relationship("User", back_populates="observations")

# Simulation experiment model
class SimulationExperiment(Base):
    __tablename__ = "simulation_experiments"

    id = Column(Integer, primary_key=True, index=True)
    arrival_rate = Column(Float, nullable=False)  # per minute
    service_rate = Column(Float, nullable=False)  # per minute per counter
    counter_count = Column(Integer, nullable=False)
    simulation_duration_minutes = Column(Integer, nullable=False)
    warm_up_period_minutes = Column(Integer, default=0)
    number_of_replications = Column(Integer, default=1)
    random_seed = Column(Integer, nullable=True)
    mean_waiting_time = Column(Float, nullable=True)
    median_waiting_time = Column(Float, nullable=True)
    p90_waiting_time = Column(Float, nullable=True)
    mean_queue_length = Column(Float, nullable=True)
    max_queue_length = Column(Integer, nullable=True)
    estimated_utilization = Column(Float, nullable=True)
    throughput = Column(Float, nullable=True)
    completed_customers = Column(Integer, nullable=True)
    remaining_customers = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# Enums for slot status
class SlotStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    closed = "closed"


# Pickup slot model
class PickupSlot(Base):
    __tablename__ = "pickup_slots"

    id = Column(Integer, primary_key=True, index=True)
    start_time = Column(DateTime(timezone=True), nullable=False)  # Start of slot (e.g., 12:00)
    end_time = Column(DateTime(timezone=True), nullable=False)    # End of slot (e.g., 12:15)
    booking_cutoff = Column(DateTime(timezone=True), nullable=True)  # When booking closes for this slot
    max_orders = Column(Integer, nullable=False)  # Maximum orders allowed in this slot
    current_orders = Column(Integer, default=0)   # Current confirmed orders count
    safety_factor = Column(Float, default=0.8)    # Safety factor for capacity planning (0-1)
    status = Column(Enum(SlotStatus), default=SlotStatus.active)
    description = Column(String, nullable=True)   # Optional description
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    orders = relationship("Order", back_populates="pickup_slot")