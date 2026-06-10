import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet
from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

# Passlib expects bcrypt.__about__.__version__, but bcrypt 4.x exposes only
# bcrypt.__version__. Add the missing attribute before CryptContext initializes.
if not hasattr(bcrypt, "__about__"):
    class _BcryptAbout:
        __version__ = getattr(bcrypt, "__version__", "unknown")

    bcrypt.__about__ = _BcryptAbout()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def set_auth_cookie(response, token: str, request) -> None:
    """Store the access token in an HttpOnly cookie so page JS can't read it
    (XSS can't exfiltrate the session). `secure` follows the request scheme so
    it works on http://localhost in dev and stays Secure behind https/nginx."""
    from app.api.deps import ACCESS_TOKEN_COOKIE

    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    is_https = forwarded_proto == "https" or request.url.scheme == "https"
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response) -> None:
    from app.api.deps import ACCESS_TOKEN_COOKIE

    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, path="/")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, prefix, key_hash)."""
    raw = "aip_" + secrets.token_urlsafe(48)
    prefix = raw[:12]
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, prefix, key_hash


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_fernet() -> Fernet:
    key = settings.ENCRYPTION_KEY
    if len(key) < 44:
        import base64
        key = base64.urlsafe_b64encode(key.ljust(32, "0")[:32].encode()).decode()
    return Fernet(key.encode())


def encrypt_value(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    return _get_fernet().decrypt(encrypted.encode()).decode()


def mask_secret(value: str, visible: int = 4) -> str:
    if len(value) <= visible:
        return "****"
    return value[:visible] + "****" + value[-2:]


def redact_for_log(data: dict) -> dict:
    """Redact sensitive fields in a dict for logging.

    Uses specific substring patterns rather than bare "token"/"key" to avoid
    false positives like `max_tokens`, `prompt_tokens`, `api_key_id`.
    """
    # Substring patterns — match if key.lower() contains any of these.
    sensitive_substrings = (
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "authorization",
        "access_token",
        "refresh_token",
        "id_token",
        "bearer",
        "session_id",
        "private_key",
        "provider_api_key",
    )
    # Exact keys that are always safe even if they technically match a pattern.
    safe_keys = {
        "api_key_id",
        "max_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "tokens_system",
        "tokens_tools",
        "tokens_memory",
        "tokens_kb",
        "tokens_history",
        "tokens_user",
    }
    result = {}
    for k, v in data.items():
        kl = k.lower()
        if kl in safe_keys:
            redacted = False
        else:
            redacted = any(s in kl for s in sensitive_substrings)
        if redacted:
            result[k] = "***REDACTED***"
        elif isinstance(v, dict):
            result[k] = redact_for_log(v)
        elif isinstance(v, list):
            result[k] = [redact_for_log(x) if isinstance(x, dict) else x for x in v]
        else:
            result[k] = v
    return result
