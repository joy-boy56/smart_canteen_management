"""Securely create or promote an administrator account for Smart Canteen."""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import DatabaseSession, get_database_path, init_db, utcnow_iso
from backend.security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Smart Canteen Administrator Account")
    parser.add_argument("--name", required=True, help="Administrator full name")
    parser.add_argument("--email", required=True, help="Administrator email address")
    parser.add_argument("--phone", default="9000000000", help="Administrator phone number")
    parser.add_argument("--password", help="Administrator password (if omitted, prompted securely)")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass("Enter administrator password (min 8 chars): ")
    if len(password) < 8:
        print("Error: Password must be at least 8 characters long.", file=sys.stderr)
        sys.exit(1)

    init_db()
    db = DatabaseSession(get_database_path())
    try:
        email_norm = args.email.strip().lower()
        now = utcnow_iso()
        with db.atomic():
            existing = db.fetch_one("SELECT id FROM users WHERE email = ?", (email_norm,))
            if existing:
                db.update_by_id(
                    "users",
                    existing.id,
                    {
                        "full_name": args.name.strip(),
                        "password_hash": hash_password(password),
                        "role": "admin",
                        "is_active": 1,
                        "updated_at": now,
                    },
                )
                print(f"[OK] Updated existing user '{email_norm}' to administrator.")
            else:
                db.insert(
                    "users",
                    {
                        "full_name": args.name.strip(),
                        "registration_number": "ADMIN-001",
                        "email": email_norm,
                        "phone": args.phone.strip(),
                        "department": "Canteen Operations",
                        "academic_year": "Staff",
                        "password_hash": hash_password(password),
                        "role": "admin",
                        "consent_accepted": 1,
                        "preferences_json": "{}",
                        "is_active": 1,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                print(f"[OK] Created administrator account '{email_norm}'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
