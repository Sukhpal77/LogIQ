"""JWT issuance and verification."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from jwt import InvalidTokenError as JWTError
import bcrypt

from app.config import settings

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, UnicodeError):
        return False


def issue_token(subject: str, role: str, tenant: int = 1, session_version: int = 0, auth: str = "full") -> str:
    now = datetime.now(tz=timezone.utc)
    payload: Dict[str, Any] = {
        "sub": subject,
        "role": role,
        "tenant_id": tenant,
        "sv": session_version,
        "auth": auth,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5 if auth != "full" else settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg], options={"require":["exp","sub"]})
    except JWTError as e:
        raise ValueError(str(e)) from e
