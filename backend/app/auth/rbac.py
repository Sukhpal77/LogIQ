"""FastAPI dependencies for authentication and role-based access control."""
from typing import Iterable
import secrets

from fastapi import Depends, Header, HTTPException, status, Request

from app.auth.jwt import decode_token
from app.config import settings


def ingest_user(authorization: str | None = Header(default=None), request: Request = None) -> "Principal":
    if request is not None and getattr(request.state, "principal", None):
        principal = request.state.principal
        if principal.role in ("ingest", "admin", "devops"):
            return principal
        raise HTTPException(403, "ingestion requires an operator or ingestion key")
    token = _extract_token(authorization)
    if settings.app_env != "production" and settings.ingest_api_key and secrets.compare_digest(token, settings.ingest_api_key):
        return Principal("collector", "ingest")
    return current_user(authorization, request)


class Principal:
    def __init__(self, username: str, role: str, tenant: int = 1, application_id=None, owner=False) -> None:
        self.username = username
        self.role = role
        self.tenant_id = tenant
        self.application_id = application_id
        self.owner = owner or role == "admin"


def _extract_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def current_user(authorization: str | None = Header(default=None), request: Request = None) -> Principal:
    if request is not None and getattr(request.state, "principal", None):
        user = request.state.principal
        if user.role == "ingest":
            raise HTTPException(401, "ingestion keys cannot access the dashboard")
        return user
    token = _extract_token(authorization)
    try:
        claims = decode_token(token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    return Principal(username=claims["sub"], role=claims.get("role", "viewer"), tenant=claims.get("tenant_id", 1))


def require_roles(*allowed: str):
    allowed_set: Iterable[str] = set(allowed)

    def _dep(user: Principal = Depends(current_user)) -> Principal:
        if user.role not in allowed_set:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return user

    return _dep


def require_owner(user: Principal = Depends(current_user)):
    if not user.owner:
        raise HTTPException(403, "workspace owner required")
    return user
