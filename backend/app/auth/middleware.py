"""Establish customer identity before any SQL or background work is scheduled."""
import hashlib
import secrets
from datetime import datetime
from sqlalchemy import select
from starlette.responses import JSONResponse
from app.auth.jwt import decode_token
from app.auth.rbac import Principal
from app.config import settings
from app.db import models as m
from app.db.session import session_scope
from app.tenancy import tenant_scope


async def workspace_middleware(request, call_next):
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    cookie = request.cookies.get("logiq_session", "")
    token = bearer or cookie
    if request.url.path in ("/auth/login", "/auth/logout", "/auth/register", "/auth/reset/request", "/auth/reset/confirm", "/auth/verify"):
        token = ""
    principal = None
    if cookie and not bearer and request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        allowed = {x.strip() for x in settings.cors_origins.split(",")}
        allowed.add(str(request.base_url).rstrip("/"))
        if not origin or origin not in allowed:
            return JSONResponse({"detail": "session requests require a trusted Origin"}, status_code=403)
    try:
        if token and settings.app_env != "production" and settings.ingest_api_key and secrets.compare_digest(token, settings.ingest_api_key):
            principal = Principal("legacy-collector", "ingest")
        elif token.startswith("lq_"):
            async with session_scope() as s:
                key = (await s.execute(select(m.ApiKey).where(m.ApiKey.digest == hashlib.sha256(token.encode()).hexdigest(), m.ApiKey.active.is_(True)))).scalar_one_or_none()
                if key and key.scope == "ingest" and (key.expires_at is None or key.expires_at > datetime.utcnow()):
                    org = await s.get(m.Organization, key.tenant_id)
                    if org and org.status == "active":
                        with tenant_scope(key.tenant_id):
                            app = (await s.execute(select(m.Application).where(m.Application.id == key.application_id, m.Application.archived.is_(False)))).scalar_one_or_none()
                        if app:
                            principal = Principal("agent", key.scope, key.tenant_id, key.application_id)
                            key.last_seen = datetime.utcnow()
            if principal is None:
                return JSONResponse({"detail": "invalid or revoked key"}, status_code=401)
        elif token:
            claims = decode_token(token)
            async with session_scope() as s:
                user = (await s.execute(select(m.User).where(m.User.username == claims["sub"], m.User.active.is_(True)))).scalar_one_or_none()
                if user is None or user.tenant_id != claims.get("tenant_id", 1) or user.session_version != claims.get("sv", 0):
                    return JSONResponse({"detail": "account unavailable"}, status_code=401)
                org = await s.get(m.Organization, user.tenant_id)
                if not org or org.status != "active":
                    return JSONResponse({"detail": "workspace suspended"}, status_code=403)
                principal = Principal(user.username, user.role, user.tenant_id, owner=user.owner)
                if claims.get("auth", "full") != "full":
                    if claims.get("auth") != "mfa_setup" or request.url.path not in ("/auth/mfa/enroll", "/auth/mfa/confirm"):
                        return JSONResponse({"detail": "complete administrator MFA enrollment"}, status_code=403)
                    principal.role = "mfa_setup"
        scope = principal.tenant_id if principal else 1
        if principal and principal.role == "admin":
            path = request.url.path
            if not path.startswith(("/admin/", "/auth/", "/public/")) and path not in ("/workspace", "/workspace/preferences"):
                return JSONResponse({"detail": "Platform administrators manage accounts and billing; customer application data is private."}, status_code=403)
        if principal and principal.role in ("billing_admin", "support"):
            path = request.url.path
            allowed_paths = ("/workspace", "/workspace/preferences", "/admin/customers", "/admin/plans", "/admin/usage") if principal.role == "billing_admin" else ("/workspace", "/workspace/preferences", "/admin/support")
            if path not in allowed_paths and not path.startswith(("/auth/", "/public/")):
                return JSONResponse({"detail": "staff role cannot access application telemetry or this platform setting"}, status_code=403)
        if request.headers.get("x-workspace-id"):
            return JSONResponse({"detail": "Customer workspace impersonation is disabled for privacy."}, status_code=403)
        request.state.principal = principal
        with tenant_scope(scope):
            return await call_next(request)
    except (ValueError, KeyError):
        return JSONResponse({"detail": "invalid credentials or workspace"}, status_code=401)
