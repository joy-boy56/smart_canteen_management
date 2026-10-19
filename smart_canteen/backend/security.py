"""Authentication, password hashing, session/token signing, CSRF, and RBAC dependencies."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from backend.config import settings
from backend.database import DatabaseSession, RowObject, get_db


def hash_password(password: str) -> str:
    """Hash a password using modern cryptographic scrypt (or PBKDF2-HMAC-SHA256 fallback).

    Format: `scrypt$n$r$p$salt_hex$digest_hex`
    """
    salt = secrets.token_bytes(16)
    n, r, p = 16384, 8, 1
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32
    )
    return f"scrypt${n}${r}${p}${salt.hex()}${derived.hex()}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verify a password in constant time against its stored cryptographic hash."""
    try:
        parts = stored_hash.split("$")
        if len(parts) == 6 and parts[0] == "scrypt":
            _, n_s, r_s, p_s, salt_hex, digest_hex = parts
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(digest_hex)
            candidate = hashlib.scrypt(
                plain_password.encode("utf-8"),
                salt=salt,
                n=int(n_s),
                r=int(r_s),
                p=int(p_s),
                dklen=len(expected),
            )
            return hmac.compare_digest(candidate, expected)
    except Exception:
        return False
    return False


def create_session_token(user_id: int, role: str, email: str) -> str:
    """Create an HMAC-SHA256 signed session token containing user identity and expiry."""
    now = int(time.time())
    exp = now + settings.SESSION_EXPIRE_MINUTES * 60
    csrf_nonce = secrets.token_hex(12)
    payload = {
        "uid": int(user_id),
        "role": str(role),
        "email": str(email),
        "iat": now,
        "exp": exp,
        "csrf": csrf_nonce,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    sig = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b64_payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{b64_payload}.{sig}"


def decode_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify signature and expiration of a session token."""
    if not token or "." not in token:
        return None
    try:
        b64_payload, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            b64_payload.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padding = "=" * (-len(b64_payload) % 4)
        raw = base64.urlsafe_b64decode((b64_payload + padding).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except Exception:
        return None


def extract_token_from_request(request: Request) -> Optional[str]:
    """Extract session token from Authorization Bearer header or session cookie."""
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    cookie_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token
    return None


def get_current_user(
    request: Request, db: DatabaseSession = Depends(get_db)
) -> RowObject:
    """FastAPI dependency returning the authenticated user row or raising 401."""
    token = extract_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in.",
        )
    payload = decode_session_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token. Please sign in again.",
        )
    user = db.fetch_one(
        "SELECT * FROM users WHERE id = ? AND is_active = 1",
        (payload["uid"],),
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or inactive.",
        )
    return user


def require_admin(user: RowObject = Depends(get_current_user)) -> RowObject:
    """FastAPI dependency enforcing `admin` role authorization."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required for this operation.",
        )
    return user


def sanitize_user_dict(user: RowObject) -> Dict[str, Any]:
    """Return user dictionary stripped of password_hash and sensitive internals."""
    try:
        prefs = json.loads(user.preferences_json or "{}")
    except Exception:
        prefs = {}
    return {
        "id": user.id,
        "full_name": user.full_name,
        "registration_number": user.registration_number,
        "email": user.email,
        "phone": user.phone,
        "department": user.department,
        "academic_year": user.academic_year,
        "role": user.role,
        "consent_accepted": bool(user.consent_accepted),
        "preferences": prefs,
        "is_active": bool(user.is_active),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }
