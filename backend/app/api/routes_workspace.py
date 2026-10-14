"""Customer workflows and platform administration. Sandbox purchases never charge money."""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.rbac import current_user, require_roles, require_owner, ingest_user
from app.auth.jwt import hash_password
from app.auth.security import encrypt, decrypt
from app.config import settings
from app.db import models as m
from app.db.session import get_session, session_scope
from app.tenancy import identity, tenant_scope

router = APIRouter(tags=["workspaces"])
operator = require_roles("admin", "devops")
administrator = require_roles("admin")
billing_staff = require_roles("admin", "billing_admin")


class StaffInput(BaseModel):
    email: str = Field(min_length=5, max_length=128, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    role: Literal["billing_admin", "support"]


@router.get("/admin/staff")
async def staff(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(administrator)):
    return [serialize(row) for row in (await session.execute(select(m.User).where(m.User.tenant_id == 1, m.User.role.in_(["admin", "billing_admin", "support"])))).scalars()]


@router.post("/admin/staff", status_code=201)
async def invite_staff(body: StaffInput, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(administrator)):
    if settings.app_env == "production" and (not settings.smtp_host or not settings.smtp_sender):
        raise HTTPException(503, "staff invitations require email delivery")
    email = body.email.lower()
    if (await session.execute(select(m.User.id).where(m.User.username == email))).first():
        raise HTTPException(409, "account already exists")
    password = secrets.token_urlsafe(24)
    row = m.User(username=email, email=email, password_hash=hash_password(password), role=body.role, tenant_id=1, active=settings.app_env != "production", email_verified=settings.app_env != "production")
    session.add(row)
    await session.flush()
    if settings.app_env == "production":
        from app.api.routes_identity import challenge
        await challenge(session, row, "invite")
    audit(session, user, "staff.invite", {"email":email, "role":body.role})
    return {"status":"Invitation sent" if settings.app_env == "production" else "Local staff account created. Save its one-time password.", "password":password if settings.app_env != "production" else None}


class StaffUpdate(BaseModel):
    active: bool


@router.patch("/admin/staff/{staff_id}")
async def disable_staff(staff_id:int, body:StaffUpdate, session:AsyncSession=Depends(get_session, scope="function"), user=Depends(administrator)):
    row=(await session.execute(select(m.User).where(m.User.id==staff_id,m.User.tenant_id==1,m.User.role.in_(["billing_admin","support"])).with_for_update())).scalar_one_or_none()
    if not row:
        raise HTTPException(404,"staff account not found")
    row.active=body.active
    row.session_version+=1
    audit(session,user,"staff.access",{"staff_id":staff_id,"active":body.active})
    return serialize(row)


def serialize(row):
    result = {c.key: getattr(row, c.key) for c in row.__table__.columns if c.key not in ("password_hash", "digest", "totp_secret", "pending_totp_secret", "recovery_codes", "encrypted_body")}
    if isinstance(row, m.WorkspaceSetting) and "encrypted_url" in row.config:
        result["config"] = {k:v for k,v in row.config.items() if k != "encrypted_url"}
        result["config"]["url"] = "[stored securely]"
    if isinstance(row, m.WorkspaceSetting) and row.kind == "llm":
        result["config"] = {k: v for k, v in row.config.items() if "api_key" not in k}
    if isinstance(row, m.WorkspaceSetting) and row.kind == "notification_platform":
        result["config"] = {k: v for k, v in row.config.items() if k != "password_encrypted"}
    return result


def audit(session, user, action, details=None):
    session.add(m.Audit(tenant_id=identity(), actor=user.username, action=action, details=details or {}))


async def subscription(session):
    sub = (await session.execute(select(m.Subscription))).scalar_one_or_none()
    if not sub:
        raise HTTPException(403, "workspace has no subscription")
    plan = await session.get(m.Plan, sub.plan_id)
    return sub, plan


async def entitlement(session, field, used=0):
    sub, plan = await subscription(session)
    grace = sub.status == "past_due" and sub.grace_until and sub.grace_until > datetime.utcnow() and field == "ingestion_gb"
    if not grace and (sub.status not in ("active", "trial") or sub.period_end <= datetime.utcnow()):
        raise HTTPException(403, "subscription expired or inactive")
    if used >= plan.limits[field]:
        raise HTTPException(429, f"{field} plan limit reached")
    return plan


@router.get("/public/plans")
async def public_plans(session: AsyncSession = Depends(get_session, scope="function")):
    return [serialize(p) for p in (await session.execute(select(m.Plan).where(m.Plan.published.is_(True)).order_by(m.Plan.price))).scalars()]


class SupportInput(BaseModel):
    email: str = Field(min_length=3, max_length=128, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    subject: str = Field(min_length=3, max_length=128)
    message: str = Field(min_length=5, max_length=4000)


@router.post("/public/support", status_code=201)
async def public_support(body: SupportInput, request: Request, session: AsyncSession = Depends(get_session, scope="function")):
    from app.db.redis_client import get_redis
    key = "support:" + hashlib.sha256(request.client.host.encode()).hexdigest()
    count = await get_redis().eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],3600) end; return n", 1, key)
    if count > 5:
        raise HTTPException(429, "support request limit reached; retry later")
    with tenant_scope(1):
        session.add(m.WorkspaceSetting(tenant_id=1, kind="support", name=body.subject, config=body.model_dump(), enabled=True))
        await session.flush()
    return {"status": "recorded"}


class Registration(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9@._+-]+$")
    password: str = Field(min_length=12, max_length=72)
    organization: str = Field(min_length=2, max_length=128)


@router.post("/auth/register", status_code=201)
async def register(body: Registration, request: Request, session: AsyncSession = Depends(get_session, scope="function")):
    from app.db.redis_client import get_redis
    key = "signup:" + hashlib.sha256(request.client.host.encode()).hexdigest()
    attempts = await get_redis().eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],3600) end; return n", 1, key)
    if attempts > (5 if settings.app_env == "production" else 100):
        raise HTTPException(429, "registration limit reached; retry later")
    if settings.app_env == "production" and ("@" not in body.username or not settings.smtp_host or not settings.smtp_sender):
        raise HTTPException(503, "production signup requires an email address and configured email delivery")
    if len(body.password.encode()) > 72:
        raise HTTPException(422, "password must be at most 72 UTF-8 bytes")
    await session.execute(select(m.Organization).where(m.Organization.id == 1).with_for_update())
    if (await session.execute(select(m.User).where(m.User.username == body.username.lower()))).scalar_one_or_none():
        raise HTTPException(409, "username already registered")
    trial = (await session.execute(select(m.Plan).where(m.Plan.name == "Trial", m.Plan.published.is_(True)))).scalar_one()
    org = m.Organization(name=body.organization, status="active")
    session.add(org)
    await session.flush()
    user = m.User(username=body.username.lower(), email=body.username.lower() if "@" in body.username else None, password_hash=hash_password(body.password), role="devops", tenant_id=org.id, owner=True, active=settings.app_env != "production", email_verified=settings.app_env != "production")
    session.add(user)
    with tenant_scope(org.id):
        session.add(m.Subscription(tenant_id=org.id, plan_id=trial.id, status="trial", period_end=datetime.utcnow() + timedelta(days=14)))
        await session.flush()
    if settings.app_env == "production":
        from app.api.routes_identity import challenge
        await challenge(session, user, "verify")
    return {"status": "created", "verification_required": settings.app_env == "production", "next": "/verify" if settings.app_env == "production" else "/login"}


@router.get("/admin/support")
async def admin_support(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(require_roles("admin", "support"))):
    with tenant_scope(None):
        return [serialize(row) for row in (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind == "support").order_by(m.WorkspaceSetting.id.desc()).limit(100))).scalars()]


@router.get("/admin/health")
async def admin_health(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(administrator)):
    from app.db.redis_client import get_redis
    await session.execute(select(1))
    redis = get_redis()
    await redis.ping()
    return {"database": "ready", "redis": "ready", "logs_pending": await redis.xlen(settings.log_stream), "metrics_pending": await redis.xlen(settings.metric_stream), "environment": settings.app_env, "provider": settings.llm_provider, "notifications_available": True}


@router.post("/admin/llm/test")
async def test_llm(user=Depends(administrator)):
    import asyncio
    from app.ai.analyzer import llm_json, provider_error
    if not settings.llm_configured:
        return {"status": "No provider key configured; rules-only analysis remains available.", "success": False}
    def probe():
        result = llm_json('Return JSON with a single boolean field "ok" set to true.', "Connection test")
        return result, provider_error.get()
    result, failure = await asyncio.to_thread(probe)
    return {"success": result.get("ok") is True, "status": "Provider connection verified." if result.get("ok") is True else failure or "Provider did not return the expected JSON response."}


@router.get("/workspace")
async def workspace(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(current_user)):
    org = await session.get(m.Organization, identity())
    sub, plan = await subscription(session)
    def count(model):
        return select(func.count()).select_from(model)
    counts = {name: (await session.execute(count(model).where(model.archived.is_(False)) if model == m.Application else count(model).where(model.active.is_(True)) if model == m.Service else count(model))).scalar() for name, model in (("applications", m.Application), ("services", m.Service), ("logs", m.Log), ("metrics", m.Metric))}
    counts["open_incidents"] = (await session.execute(count(m.Incident).where(m.Incident.status == "open"))).scalar()
    return {"organization": serialize(org), "user": {"username": user.username, "role": user.role, "owner": user.owner}, "subscription": serialize(sub), "plan": serialize(plan), "counts": counts, "billing_mode": settings.billing_provider if settings.app_env != "production" or settings.billing_provider == "stripe" else "disabled"}


class AppInput(BaseModel):
    name: str = Field(min_length=2, max_length=48, pattern=r"^[a-zA-Z0-9_-]+$")
    environment: str = Field(default="production", min_length=2, max_length=24, pattern=r"^[a-zA-Z0-9_-]+$")


@router.get("/workspace/applications")
async def applications(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(current_user)):
    return [serialize(a) for a in (await session.execute(select(m.Application).order_by(m.Application.id))).scalars()]


@router.post("/workspace/applications", status_code=201)
async def create_application(body: AppInput, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(operator)):
    await session.execute(select(m.Organization).where(m.Organization.id == identity()).with_for_update())
    used = (await session.execute(select(func.count()).select_from(m.Application).where(m.Application.archived.is_(False)))).scalar()
    await entitlement(session, "applications", used)
    if (await session.execute(select(m.Application).where(m.Application.name == body.name, m.Application.environment == body.environment))).scalar_one_or_none():
        raise HTTPException(409, "application/environment already exists")
    row = m.Application(name=body.name, environment=body.environment)
    session.add(row)
    audit(session, user, "application.create", body.model_dump())
    await session.flush()
    return serialize(row)


class KeyInput(BaseModel):
    application_id: int
    name: str = Field(default="Production agent", min_length=1, max_length=64)
    scope: Literal["ingest"] = "ingest"
    validity_days: int = Field(default=90, ge=1, le=365)


class Heartbeat(BaseModel):
    agent_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    version: str = Field(max_length=32)
    pending: int = Field(ge=0, le=100000000)
    rejected: int = Field(ge=0, le=100000000)


@router.post("/agent/heartbeat")
async def heartbeat(body: Heartbeat, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(ingest_user)):
    if not user.application_id:
        raise HTTPException(403, "an application-scoped ingestion key is required")
    await session.execute(select(m.Application).where(m.Application.id == user.application_id).with_for_update())
    name = f"agent:{user.application_id}:{body.agent_id}"
    row = (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind == "agent", m.WorkspaceSetting.name == name))).scalar_one_or_none()
    config = {**body.model_dump(), "application_id": user.application_id, "last_seen": datetime.utcnow().isoformat()+"Z"}
    if row is None:
        count = (await session.execute(select(func.count()).select_from(m.WorkspaceSetting).where(m.WorkspaceSetting.kind == "agent"))).scalar()
        if count >= 100:
            raise HTTPException(429, "agent registration limit reached")
        session.add(m.WorkspaceSetting(kind="agent", name=name, config=config))
    else:
        row.config = config
    return {"status": "received"}


@router.get("/workspace/agents")
async def agents(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(current_user)):
    return [serialize(a) for a in (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind == "agent"))).scalars()]


@router.get("/workspace/preferences")
async def preferences(session:AsyncSession=Depends(get_session, scope="function"),user=Depends(current_user)):
    row=(await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind=="preferences").order_by(m.WorkspaceSetting.id.desc()).limit(1))).scalar_one_or_none()
    return {"timezone":row.config.get("timezone","UTC") if row else "UTC"}


class MemberUpdate(BaseModel):
    role: Literal["devops", "viewer"]
    active: bool


@router.patch("/workspace/team/{member_id}")
async def update_member(member_id: int, body: MemberUpdate, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(require_owner)):
    row = (await session.execute(select(m.User).where(m.User.id == member_id, m.User.tenant_id == identity()).with_for_update())).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "member not found")
    if row.owner or row.role == "admin" or row.username == user.username:
        raise HTTPException(409, "workspace owners and platform administrators cannot be changed here")
    row.role, row.active = body.role, body.active
    row.session_version += 1
    audit(session, user, "team.update", {"member_id":member_id, **body.model_dump()})
    return serialize(row)


@router.get("/workspace/keys")
async def keys(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(current_user)):
    return [serialize(k) for k in (await session.execute(select(m.ApiKey).where(m.ApiKey.tenant_id == identity()).order_by(m.ApiKey.id))).scalars()]


@router.post("/workspace/keys", status_code=201)
async def create_key(body: KeyInput, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(operator)):
    app = (await session.execute(select(m.Application).where(m.Application.id == body.application_id, m.Application.archived.is_(False)))).scalar_one_or_none()
    if app is None:
        raise HTTPException(404, "application not found")
    token = "lq_" + secrets.token_urlsafe(32)
    row = m.ApiKey(tenant_id=identity(), application_id=app.id, name=body.name, scope=body.scope, digest=hashlib.sha256(token.encode()).hexdigest(), prefix=token[:12], expires_at=datetime.utcnow()+timedelta(days=body.validity_days))
    session.add(row)
    audit(session, user, "key.create", {"application_id": app.id})
    await session.flush()
    return {**serialize(row), "key": token, "note": "Save this key now. It will not be shown again."}


@router.delete("/workspace/keys/{key_id}")
async def revoke_key(key_id: int, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(operator)):
    row = (await session.execute(select(m.ApiKey).where(m.ApiKey.id == key_id, m.ApiKey.tenant_id == identity()))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "key not found")
    row.active = False
    audit(session, user, "key.revoke", {"key_id": key_id})
    return {"status": "revoked"}


@router.get("/workspace/usage")
async def usage(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(current_user)):
    sub, plan = await subscription(session)
    totals = (await session.execute(select(m.Usage.kind, func.sum(m.Usage.amount)).where(m.Usage.created_at >= sub.period_start).group_by(m.Usage.kind))).all()
    return {"totals": dict(totals), "limits": plan.limits, "period_end": sub.period_end, "price": plan.price, "plan": plan.name, "version": plan.version}


class Purchase(BaseModel):
    plan_id: int
    confirm_sandbox: bool = False
    confirm_paid_change: bool = False


@router.post("/workspace/checkout")
async def sandbox_checkout(body: Purchase, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(require_owner)):
    if settings.billing_provider == "disabled" or settings.app_env == "production" and settings.billing_provider != "stripe":
        raise HTTPException(503, "live payment integration must be configured before purchases are enabled")
    if settings.billing_provider == "sandbox" and not body.confirm_sandbox:
        raise HTTPException(400, "confirm this is a sandbox purchase; no money is charged")
    await session.execute(select(m.Organization).where(m.Organization.id == identity()).with_for_update())
    plan = (await session.execute(select(m.Plan).where(m.Plan.id == body.plan_id, m.Plan.published.is_(True)))).scalar_one_or_none()
    if plan is None or plan.name == "Trial":
        raise HTTPException(400, "select an available paid plan")
    sub, previous = await subscription(session)
    apps = (await session.execute(select(func.count()).select_from(m.Application).where(m.Application.archived.is_(False)))).scalar()
    services = (await session.execute(select(func.count()).select_from(m.Service).where(m.Service.active.is_(True)))).scalar()
    if apps > plan.limits["applications"] or services > plan.limits["services"]:
        raise HTTPException(409, "current resources exceed this plan; reduce them before changing plan")
    if settings.billing_provider == "stripe":
        from app.billing import checkout
        return await checkout(session, user, plan, sub, body.confirm_paid_change)
    sub.plan_id, sub.status, sub.period_end, sub.cancel_at_end = plan.id, "active", datetime.utcnow() + timedelta(days=30), False
    sub.period_start = datetime.utcnow()
    audit(session, user, "billing.sandbox_purchase", {"plan_id": plan.id, "price": plan.price})
    return {"status": "sandbox_active", "charged": False, "next": "/onboarding"}


@router.post("/workspace/cancel")
async def cancel(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(require_owner)):
    sub, _ = await subscription(session)
    if sub.provider_id:
        from app.billing import provider
        await provider("POST", "/subscriptions/" + sub.provider_id, {"cancel_at_period_end": "true"})
    sub.cancel_at_end = True
    audit(session, user, "subscription.cancel_at_end")
    return {"status": "access remains available until the current period ends"}


@router.get("/workspace/team")
async def team(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(current_user)):
    return [serialize(row) for row in (await session.execute(select(m.User).where(m.User.tenant_id == identity()))).scalars()]


class Member(Registration):
    role: Literal["devops", "viewer"] = "viewer"


@router.post("/workspace/team", status_code=201)
async def add_member(body: Member, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(require_owner)):
    if settings.app_env == "production" and ("@" not in body.username or not settings.smtp_host or not settings.smtp_sender):
        raise HTTPException(503, "production invitations require email delivery")
    await session.execute(select(m.Organization).where(m.Organization.id == identity()).with_for_update())
    used = (await session.execute(select(func.count()).select_from(m.User).where(m.User.tenant_id == identity()))).scalar()
    await entitlement(session, "members", used)
    if len(body.password.encode()) > 72:
        raise HTTPException(422, "password too long")
    if (await session.execute(select(m.User).where(m.User.username == body.username.lower()))).scalar_one_or_none():
        raise HTTPException(409, "username unavailable")
    member = m.User(username=body.username.lower(), email=body.username.lower() if "@" in body.username else None, password_hash=hash_password(secrets.token_urlsafe(24) if settings.app_env == "production" else body.password), role=body.role, tenant_id=identity(), active=settings.app_env != "production", email_verified=settings.app_env != "production")
    session.add(member)
    await session.flush()
    if settings.app_env == "production":
        from app.api.routes_identity import challenge
        await challenge(session, member, "invite")
    audit(session, user, "member.create", {"username": body.username, "role": body.role})
    return {"status": "created"}


class SettingInput(BaseModel):
    kind: Literal["notification", "preferences", "support"]
    name: str = Field(min_length=2, max_length=128)
    config: dict = Field(default_factory=dict)
    enabled: bool = False


@router.get("/workspace/settings")
async def workspace_settings(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(current_user)):
    return [serialize(row) for row in (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind.in_(["notification", "preferences", "support"])))) .scalars()]


@router.post("/workspace/settings", status_code=201)
async def create_setting(body: SettingInput, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(operator)):
    await prepare_setting(body, session)
    row = m.WorkspaceSetting(**body.model_dump())
    session.add(row)
    audit(session, user, "setting.create", {"kind": body.kind, "name": body.name})
    await session.flush()
    return serialize(row)


async def prepare_setting(body, session, existing=None):
    if body.kind == "preferences":
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(body.config.get("timezone", "UTC"))
        except (ZoneInfoNotFoundError, TypeError):
            raise HTTPException(422, "choose a valid timezone")
    if body.kind == "notification":
        from app.api.routes_notification_config import load_delivery_settings, DestinationConfig
        await load_delivery_settings()
        from pydantic import ValidationError
        try:
            body.config = DestinationConfig.model_validate(body.config).model_dump()
        except ValidationError:
            raise HTTPException(422, "choose valid destination fields, severity and service filter")
        if existing and body.config.get("channel") == existing.config.get("channel") and not body.config.get("url") and existing.config.get("encrypted_url"):
            body.config["encrypted_url"] = existing.config["encrypted_url"]
        from app.actions.delivery import validate_destination
        await validate_destination(body.config)
        if body.config.get("channel") == "email":
            member = (await session.execute(select(m.User).where(m.User.tenant_id == identity(), m.User.email == body.config.get("recipient"), m.User.email_verified.is_(True), m.User.active.is_(True)))).scalar_one_or_none()
            if member is None:
                raise HTTPException(422, "email destination must be a verified workspace member")
        elif body.config.get("url"):
            from app.auth.security import encrypt
            body.config = {**body.config, "encrypted_url": encrypt(body.config["url"])}
            del body.config["url"]


@router.put("/workspace/settings/{setting_id}")
async def edit_setting(setting_id: int, body: SettingInput, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(operator)):
    row = (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.id == setting_id, m.WorkspaceSetting.kind == "notification").with_for_update())).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "destination not found")
    if body.kind != "notification":
        raise HTTPException(422, "notification destination required")
    await prepare_setting(body, session, row)
    row.name, row.config, row.enabled = body.name, body.config, body.enabled
    audit(session, user, "notification.update", {"destination_id": row.id})
    return {**serialize(row), "status": "Saved. No notification was sent."}


@router.delete("/workspace/settings/{setting_id}")
async def delete_setting(setting_id: int, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(operator)):
    row = (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.id == setting_id, m.WorkspaceSetting.kind.in_(["notification", "preferences", "support"])))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "setting not found")
    await session.delete(row)
    audit(session, user, "setting.delete", {"id": setting_id})
    return {"status": "deleted"}


class SettingSwitch(BaseModel):
    enabled: bool


@router.patch("/workspace/settings/{setting_id}")
async def toggle_setting(setting_id:int,body:SettingSwitch,session:AsyncSession=Depends(get_session, scope="function"),user=Depends(operator)):
    row=(await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.id==setting_id,m.WorkspaceSetting.kind.in_(["notification"])).with_for_update())).scalar_one_or_none()
    if not row:
        raise HTTPException(404,"configuration not found")
    if body.enabled:
        from app.api.routes_notification_config import load_delivery_settings
        await load_delivery_settings()
        from app.actions.delivery import validate_destination
        await validate_destination(row.config)
    row.enabled=body.enabled
    audit(session,user,"setting.enabled",{"id":row.id,"enabled":body.enabled})
    return serialize(row)


@router.post("/workspace/settings/{setting_id}/test")
async def test_notification(setting_id: int, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(operator)):
    from app.api.routes_notification_config import load_delivery_settings
    await load_delivery_settings()
    from app.actions.delivery import deliver
    row = (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.id == setting_id, m.WorkspaceSetting.kind == "notification"))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "destination not found")
    result = await deliver(row.config, "LogIQ connection test: notification delivery is working.")
    audit(session, user, "notification.test", {"destination_id": row.id, **result})
    return result


@router.get("/workspace/history")
async def history(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(current_user)):
    return [serialize(row) for row in (await session.execute(select(m.Audit).where(m.Audit.tenant_id == identity()).order_by(m.Audit.id.desc()).limit(100))).scalars()]


@router.get("/workspace/deliveries")
async def deliveries(session:AsyncSession=Depends(get_session, scope="function"),user=Depends(current_user)):
    return [serialize(row) for row in (await session.execute(select(m.NotificationOutbox).order_by(m.NotificationOutbox.id.desc()).limit(100))).scalars()]


@router.post("/workspace/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id:int,session:AsyncSession=Depends(get_session, scope="function"),user=Depends(operator)):
    row=(await session.execute(select(m.NotificationOutbox).where(m.NotificationOutbox.id==delivery_id).with_for_update())).scalar_one_or_none()
    if not row or row.sent_at:
        raise HTTPException(409,"delivery is missing or already processed")
    row.attempts=0
    row.next_attempt=datetime.utcnow()
    audit(session,user,"notification.retry",{"delivery_id":row.id})
    return {"status":"Delivery queued for retry"}


@router.get("/admin/customers")
async def customers(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(billing_staff)):
    with tenant_scope(None):
        orgs = (await session.execute(select(m.Organization).order_by(m.Organization.id))).scalars().all()
        result = []
        for org in orgs:
            sub = (await session.execute(select(m.Subscription).where(m.Subscription.tenant_id == org.id))).scalar_one_or_none()
            plan = await session.get(m.Plan, sub.plan_id) if sub else None
            counts = (await session.execute(select(func.count()).select_from(m.Service).where(m.Service.tenant_id == org.id))).scalar()
            result.append({**serialize(org), "subscription": serialize(sub) if sub else None, "plan": serialize(plan) if plan else None, "services": counts})
    return result


class CustomerUpdate(BaseModel):
    status: Literal["active", "suspended"]


@router.patch("/admin/customers/{customer_id}")
async def update_customer(customer_id: int, body: CustomerUpdate, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(administrator)):
    if customer_id == 1:
        raise HTTPException(409, "cannot suspend the platform administration workspace")
    row = await session.get(m.Organization, customer_id)
    if row is None:
        raise HTTPException(404, "customer not found")
    row.status = body.status
    audit(session, user, "customer.status", {"customer_id": customer_id, "status": body.status})
    return serialize(row)


@router.get("/admin/plans")
async def admin_plans(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(billing_staff)):
    return [serialize(row) for row in (await session.execute(select(m.Plan).order_by(m.Plan.id))).scalars()]


class PlanInput(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    price: int = Field(ge=0, le=100000000)
    applications: int = Field(ge=1, le=1000)
    services: int = Field(ge=1, le=10000)
    ingestion_gb: int = Field(ge=1, le=100000)
    retention_days: int = Field(ge=1, le=3650)
    ai_credits: int = Field(ge=0, le=1000000)
    members: int = Field(ge=1, le=1000)
    stripe_price_id: str = Field(default="", max_length=128, pattern=r"^(price_[a-zA-Z0-9]+)?$")


@router.post("/admin/plans", status_code=201)
async def publish_plan(body: PlanInput, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(billing_staff)):
    await session.execute(select(m.Organization).where(m.Organization.id == 1).with_for_update())
    previous = (await session.execute(select(m.Plan).where(m.Plan.name == body.name))).scalars().all()
    if body.stripe_price_id and any(p.limits.get("stripe_price_id") == body.stripe_price_id for p in (await session.execute(select(m.Plan))).scalars()):
        raise HTTPException(409, "a provider price belongs to one immutable plan version; create a new provider price")
    for plan in previous:
        plan.published = False
    limits = body.model_dump(exclude={"name", "price"})
    row = m.Plan(name=body.name, price=body.price, version=max((p.version for p in previous), default=0) + 1, limits=limits)
    session.add(row)
    audit(session, user, "plan.publish", body.model_dump())
    await session.flush()
    return serialize(row)


@router.get("/admin/audit")
async def admin_audit(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(administrator)):
    return [serialize(row) for row in (await session.execute(select(m.Audit).order_by(m.Audit.id.desc()).limit(200))).scalars()]


@router.get("/admin/usage")
async def admin_usage(session: AsyncSession = Depends(get_session, scope="function"), user=Depends(billing_staff)):
    with tenant_scope(None):
        rows = (await session.execute(select(m.Usage.tenant_id, m.Usage.kind, func.sum(m.Usage.amount)).group_by(m.Usage.tenant_id, m.Usage.kind))).all()
    return [{"tenant_id": tenant, "kind": kind, "amount": total} for tenant, kind, total in rows]


@router.get("/admin/llm")
async def llm_settings(user=Depends(administrator)):
    return {"provider": settings.llm_provider, "openai_model": settings.openai_model, "gemini_model": settings.gemini_model, "configured": settings.llm_configured, "openai_key_configured": bool(settings.openai_api_key), "gemini_key_configured": bool(settings.gemini_api_key)}


class LlmInput(BaseModel):
    provider: Literal["openai", "gemini", "disabled"]
    openai_model: str = Field(min_length=2, max_length=128)
    gemini_model: str = Field(min_length=2, max_length=128)
    openai_api_key: str = Field(default="", max_length=512)
    gemini_api_key: str = Field(default="", max_length=512)


@router.put("/admin/llm")
async def update_llm(body: LlmInput, session: AsyncSession = Depends(get_session, scope="function"), user=Depends(administrator)):
    routing = body.model_dump(exclude={"openai_api_key", "gemini_api_key"})
    replacements = {name: getattr(body, name).strip() for name in ("openai_api_key", "gemini_api_key") if getattr(body, name).strip()}
    with tenant_scope(1):
        row = (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind == "llm"))).scalar_one_or_none()
        if row is None:
            row = m.WorkspaceSetting(kind="llm", name="Platform AI configuration", config={}, enabled=True)
            session.add(row)
        stored = {**(row.config or {}), **routing}
        for name, value in replacements.items():
            stored[name + "_encrypted"] = encrypt(value)
        row.config = stored
        audit(session, user, "llm.update", {**routing, "keys_updated": list(replacements)})
        await session.commit()
    for name, value in {**routing, **replacements}.items():
        setattr(settings, "llm_provider" if name == "provider" else name, value)
    return {"status": "saved", "configured": settings.llm_configured}


async def load_llm_settings():
    with tenant_scope(1):
        async with session_scope() as s:
            row = (await s.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind == "llm"))).scalar_one_or_none()
            if row:
                for name, value in row.config.items():
                    if name.endswith("_api_key_encrypted"):
                        setattr(settings, name.removesuffix("_encrypted"), decrypt(value))
                    elif name in ("provider", "openai_model", "gemini_model"):
                        setattr(settings, "llm_provider" if name == "provider" else name, value)


async def seed_catalog():
    with tenant_scope(1):
        async with session_scope() as s:
            await s.execute(select(m.Organization).where(m.Organization.id == 1).with_for_update())
            for name, price, apps, services, gb, days, ai, members in (
                ("Trial", 0, 1, 4, 1, 3, 20, 1), ("Starter", 149900, 1, 4, 10, 7, 100, 2),
                ("Team", 499900, 3, 15, 50, 30, 500, 10), ("Business", 1499900, 10, 50, 200, 90, 2000, 25)):
                exists = (await s.execute(select(m.Plan).where(m.Plan.name == name))).first()
                if not exists:
                    s.add(m.Plan(name=name, version=1, price=price, limits={"applications": apps, "services": services, "ingestion_gb": gb, "retention_days": days, "ai_credits": ai, "members": members}))
            await s.flush()
            if not (await s.execute(select(m.Subscription))).first():
                business = (await s.execute(select(m.Plan).where(m.Plan.name == "Business").order_by(m.Plan.version.desc()).limit(1))).scalar_one()
                s.add(m.Subscription(plan_id=business.id, status="active", period_end=datetime.utcnow() + timedelta(days=30)))
