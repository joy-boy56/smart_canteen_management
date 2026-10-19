"""Configuration management for Smart Canteen application."""

import os
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    """Application settings loaded from environment variables with safe defaults."""

    def __init__(self) -> None:
        self.APP_NAME: str = os.getenv(
            "APP_NAME",
            "Smart Canteen — Queue Optimization & Intelligent Capacity Planning",
        )
        self.APP_ENV: str = os.getenv("APP_ENV", "development")
        self.DEBUG: bool = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")

        default_db_path = BASE_DIR / "smart_canteen.db"
        self.DATABASE_URL: str = os.getenv(
            "DATABASE_URL", f"sqlite:///{default_db_path}"
        )

        self.SECRET_KEY: str = os.getenv(
            "SECRET_KEY",
            "dev-only-ephemeral-secret-key-change-in-production-9f8e7d6c5b4a",
        )
        self.SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "sc_session")
        self.CSRF_COOKIE_NAME: str = os.getenv("CSRF_COOKIE_NAME", "sc_csrf")
        self.SESSION_EXPIRE_MINUTES: int = int(
            os.getenv("SESSION_EXPIRE_MINUTES", "480")
        )

        self.CANTEEN_TIMEZONE: str = os.getenv("CANTEEN_TIMEZONE", "Asia/Kolkata")
        self.REQUIRE_REGISTRATION_NUMBER: bool = os.getenv(
            "REQUIRE_REGISTRATION_NUMBER", "true"
        ).lower() in ("1", "true", "yes")
        self.DEFAULT_RESERVATION_TIMEOUT_MINUTES: int = int(
            os.getenv("DEFAULT_RESERVATION_TIMEOUT_MINUTES", "10")
        )
        self.DEFAULT_SAFETY_FACTOR: float = float(
            os.getenv("DEFAULT_SAFETY_FACTOR", "0.85")
        )

        self.DEFAULT_MERCHANT_NAME: str = os.getenv(
            "DEFAULT_MERCHANT_NAME", "Smart Canteen Campus"
        )
        self.DEFAULT_UPI_VPA: str = os.getenv(
            "DEFAULT_UPI_VPA", "canteen@examplebank"
        )
        self.DEFAULT_MERCHANT_DESCRIPTION: str = os.getenv(
            "DEFAULT_MERCHANT_DESCRIPTION", "College Smart Canteen Food Order"
        )
        self.DEFAULT_CURRENCY: str = os.getenv("DEFAULT_CURRENCY", "INR")
        self.DEFAULT_PAYMENT_MODE: str = os.getenv(
            "DEFAULT_PAYMENT_MODE", "DIRECT_UPI"
        )

        self.RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "").strip()
        self.RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
        self.RAZORPAY_WEBHOOK_SECRET: str = os.getenv(
            "RAZORPAY_WEBHOOK_SECRET", ""
        ).strip()
        self.RAZORPAY_TEST_MODE: bool = os.getenv(
            "RAZORPAY_TEST_MODE", "true"
        ).lower() in ("1", "true", "yes")

        cors_raw = os.getenv(
            "CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
        )
        self.CORS_ORIGINS: List[str] = [
            o.strip() for o in cors_raw.split(",") if o.strip()
        ]

    @property
    def is_gateway_configured(self) -> bool:
        return bool(
            self.RAZORPAY_KEY_ID
            and self.RAZORPAY_KEY_SECRET
            and self.RAZORPAY_WEBHOOK_SECRET
        )

    @property
    def sqlite_path(self) -> str:
        if self.DATABASE_URL.startswith("sqlite:///"):
            return self.DATABASE_URL[len("sqlite:///") :]
        if self.DATABASE_URL.startswith("sqlite://"):
            return self.DATABASE_URL[len("sqlite://") :]
        return str(BASE_DIR / "smart_canteen.db")


settings = Settings()
