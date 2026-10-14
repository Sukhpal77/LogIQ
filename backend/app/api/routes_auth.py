"""Auth endpoints: POST /auth/login."""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
import hashlib
from app.db.redis_client import get_redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import issue_token, verify_password
from app.db import models
from app.db.session import get_session
from app.models.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, response: Response, session: AsyncSession = Depends(get_session, scope="function")) -> TokenResponse:
    identity = hashlib.sha256(body.username.lower().strip().encode()).hexdigest()
    redis = get_redis()
    # Atomic fixed window, shared by all backend replicas.
    attempts = await redis.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],300) end; return n",
                                1, f"logiq:login:{identity}")
    if attempts > 20:
        raise HTTPException(429, "too many login attempts; retry in five minutes")
    user = (
        await session.execute(select(models.User).where(models.User.username == body.username.lower().strip()).with_for_update())
    ).scalar_one_or_none()
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    token = issue_token(user.username, user.role, user.tenant_id, user.session_version)
    from app.config import settings
    response.set_cookie("logiq_session", token, httponly=True, secure=settings.app_env == "production", samesite="lax", max_age=settings.jwt_expire_minutes * 60, path="/")
    from app.config import settings
    from app.auth.security import decrypt, verify_totp
    if user.totp_secret:
        step = verify_totp(decrypt(user.totp_secret), body.code, user.last_totp_step)
        recovery = hashlib.sha256(body.code.encode()).hexdigest()
        if step is None and recovery not in (user.recovery_codes or []):
            response.delete_cookie("logiq_session", path="/")
            raise HTTPException(401, "enter your authenticator code or an unused recovery code")
        if step is not None:
            user.last_totp_step = step
        else:
            user.recovery_codes = [code for code in user.recovery_codes if code != recovery]
    elif user.role in ("admin", "billing_admin", "support") and settings.app_env == "production":
        token = issue_token(user.username, user.role, user.tenant_id, user.session_version, auth="mfa_setup")
        response.set_cookie("logiq_session", token, httponly=True, secure=True, samesite="lax", max_age=300, path="/")
        return TokenResponse(access_token=token, role="mfa_setup")
    return TokenResponse(access_token=token, role=user.role)


@router.post("/logout")
async def logout(response: Response, request: Request, session: AsyncSession = Depends(get_session, scope="function")):
    from app.auth.jwt import decode_token
    token = request.cookies.get("logiq_session") or request.headers.get("authorization", "").removeprefix("Bearer ")
    if token:
        try:
            claims = decode_token(token)
            user = (await session.execute(select(models.User).where(models.User.username == claims["sub"]).with_for_update())).scalar_one_or_none()
            if user and user.session_version == claims.get("sv", 0):
                user.session_version += 1
        except ValueError:
            pass
    response.delete_cookie("logiq_session", path="/")
    return {"status": "signed out"}
