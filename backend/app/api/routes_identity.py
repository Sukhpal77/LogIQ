import base64
import secrets
import hashlib
from datetime import datetime,timedelta
from fastapi import APIRouter,Depends,Request,Response,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select,delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import models as m
from app.db.session import get_session
from app.config import settings
from app.auth.rbac import current_user,require_roles
from app.auth.jwt import hash_password,issue_token
from app.auth.security import encrypt,decrypt,verify_totp
from app.api.routes_workspace import serialize
router=APIRouter(tags=["account security"])


async def challenge(session,user,kind):
    token=secrets.token_urlsafe(32)
    session.add(m.AuthChallenge(digest=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,kind=kind,expires_at=datetime.utcnow()+timedelta(hours=24 if kind=="verify" else 1)))
    path="verify" if kind=="verify" else "reset"
    link=settings.public_url.rstrip("/")+f"/{path}?token={token}"
    session.add(m.MailOutbox(recipient=user.email or user.username,subject="Verify your LogIQ account" if kind=="verify" else "Set your LogIQ password",encrypted_body=encrypt(f"Use this one-time link to complete your request:\n{link}\n\nIgnore this message if you did not request it.")))


class ResetRequest(BaseModel):
    username:str=Field(min_length=3,max_length=128)


@router.post("/auth/reset/request")
async def request_reset(body:ResetRequest,session:AsyncSession=Depends(get_session, scope="function")):
    from app.db.redis_client import get_redis
    key="reset:"+hashlib.sha256(body.username.lower().encode()).hexdigest()
    count=await get_redis().eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],3600) end; return n",1,key)
    if count<=3:
        user=(await session.execute(select(m.User).where(m.User.username==body.username.lower()))).scalar_one_or_none()
        if user and user.email:
            await challenge(session,user,"reset")
    return {"status":"If the account has a registered email, a reset link has been queued."}


class ChallengeInput(BaseModel):
    token:str=Field(min_length=20,max_length=128)


class NewPassword(ChallengeInput):
    password:str=Field(min_length=12,max_length=72)


async def consume(session,token,kinds):
    row=(await session.execute(select(m.AuthChallenge).where(m.AuthChallenge.digest==hashlib.sha256(token.encode()).hexdigest(),m.AuthChallenge.kind.in_(kinds),m.AuthChallenge.expires_at>datetime.utcnow()).with_for_update())).scalar_one_or_none()
    if row is None:
        raise HTTPException(400,"link expired or already used")
    user=await session.get(m.User,row.user_id,with_for_update=True)
    await session.delete(row)
    return user


@router.post("/auth/verify")
async def verify(body:ChallengeInput,session:AsyncSession=Depends(get_session, scope="function")):
    user=await consume(session,body.token,["verify"])
    user.email_verified=True;user.active=True
    return {"status":"Email verified. You can sign in now."}


@router.post("/auth/reset/confirm")
async def reset(body:NewPassword,session:AsyncSession=Depends(get_session, scope="function")):
    if len(body.password.encode())>72:
        raise HTTPException(422,"password must be at most 72 UTF-8 bytes")
    user=await consume(session,body.token,["reset","invite"])
    user.password_hash=hash_password(body.password);user.session_version+=1;user.active=True;user.email_verified=True
    await session.execute(delete(m.AuthChallenge).where(m.AuthChallenge.user_id==user.id,m.AuthChallenge.kind.in_(["reset","invite"])))
    return {"status":"Password updated. Existing sessions have been revoked."}


@router.post("/auth/mfa/enroll")
async def enroll(session:AsyncSession=Depends(get_session, scope="function"),principal=Depends(current_user)):
    if principal.role not in ("admin","billing_admin","support","mfa_setup"):
        raise HTTPException(403,"administrator enrollment only")
    user=(await session.execute(select(m.User).where(m.User.username==principal.username).with_for_update())).scalar_one()
    if user.totp_secret:
        raise HTTPException(409,"authenticator already enabled; use recovery codes or the documented operator recovery procedure")
    secret=base64.b32encode(secrets.token_bytes(20)).decode()
    user.pending_totp_secret=encrypt(secret)
    return {"secret":secret,"instruction":"Add this setup key to an authenticator app, then enter its current six-digit code."}


class Code(BaseModel):
    code:str=Field(pattern=r"^\d{6}$")


@router.post("/auth/mfa/confirm")
async def confirm(body:Code,response:Response,session:AsyncSession=Depends(get_session, scope="function"),principal=Depends(current_user)):
    if principal.role not in ("admin","billing_admin","support","mfa_setup"):
        raise HTTPException(403,"administrator enrollment only")
    user=(await session.execute(select(m.User).where(m.User.username==principal.username).with_for_update())).scalar_one()
    if not user.pending_totp_secret or user.totp_secret:
        raise HTTPException(409,"begin enrollment first")
    step=verify_totp(decrypt(user.pending_totp_secret),body.code)
    if step is None:
        raise HTTPException(400,"invalid authenticator code")
    codes=[secrets.token_hex(8) for _ in range(10)]
    user.recovery_codes=[hashlib.sha256(code.encode()).hexdigest() for code in codes]
    user.totp_secret=user.pending_totp_secret;user.pending_totp_secret=None;user.last_totp_step=step;user.session_version+=1
    token=issue_token(user.username,user.role,user.tenant_id,user.session_version)
    response.set_cookie("logiq_session",token,httponly=True,secure=settings.app_env=="production",samesite="lax",max_age=settings.jwt_expire_minutes*60,path="/")
    return {"status":"Authenticator enabled. Store these recovery codes securely; each can be used once.","recovery_codes":codes,"next":"/admin/support" if user.role=="support" else "/admin/customers"}


@router.get("/admin/mail")
async def mail(session:AsyncSession=Depends(get_session, scope="function"),user=Depends(require_roles("admin"))):
    return [serialize(row) for row in (await session.execute(select(m.MailOutbox).order_by(m.MailOutbox.id.desc()).limit(100))).scalars()]


@router.get("/admin/mail/{mail_id}/preview")
async def preview(mail_id:int,session:AsyncSession=Depends(get_session, scope="function"),user=Depends(require_roles("admin"))):
    raise HTTPException(403, "Email bodies and account recovery links are private.")
