"""Platform delivery configuration, stored encrypted rather than in environment files."""
import re
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from app.api.routes_workspace import administrator, audit
from app.auth.security import encrypt, decrypt
from app.config import settings
from app.db import models as m
from app.db.session import get_session, session_scope
from app.tenancy import tenant_scope

router = APIRouter(tags=["notification configuration"])


class DestinationConfig(BaseModel):
    channel: Literal["slack", "webhook", "email"] = "slack"
    url: str = Field(default="", max_length=2048)
    recipient: str = Field(default="", max_length=256)
    service: str = Field(default="", max_length=128)
    minimum_severity: Literal["info", "medium", "high", "critical"] = "info"


class DeliverySettings(BaseModel):
    smtp_host: str = Field(default="", max_length=253, pattern=r"^([a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?)?$")
    smtp_port: Literal[465, 587] = 587
    smtp_user: str = Field(default="", max_length=256)
    smtp_password: str = Field(default="", max_length=1024)
    smtp_sender: str = Field(default="", max_length=256)
    notification_hosts: str = Field(default="", max_length=2048)
    clear_password: bool = False

    @field_validator("smtp_host", "notification_hosts")
    @classmethod
    def hosts(cls, value):
        for host in value.split(","):
            if host.strip() and not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host.strip()):
                raise ValueError("use hostnames without URLs, ports or paths")
        return value.strip().lower()

    @field_validator("smtp_sender")
    @classmethod
    def sender(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("valid sender email required")
        return value


def public_settings():
    return {k: getattr(settings, k) for k in ("smtp_host", "smtp_port", "smtp_user", "smtp_sender", "notification_hosts")} | {"password_configured": bool(settings.smtp_password)}


def apply(config):
    for key in ("smtp_host", "smtp_port", "smtp_user", "smtp_sender", "notification_hosts"):
        setattr(settings, key, config.get(key, 587 if key == "smtp_port" else ""))
    settings.smtp_password = decrypt(config["password_encrypted"]) if config.get("password_encrypted") else ""


async def load_delivery_settings():
    with tenant_scope(1):
        async with session_scope() as s:
            row = (await s.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind == "notification_platform"))).scalar_one_or_none()
            if row:
                apply(row.config)


@router.get("/admin/notifications")
async def get_settings(user=Depends(administrator)):
    await load_delivery_settings()
    return public_settings()


@router.put("/admin/notifications")
async def save_settings(body: DeliverySettings, session=Depends(get_session, scope="function"), user=Depends(administrator)):
    if bool(body.smtp_host) != bool(body.smtp_sender):
        raise HTTPException(422, "provide both SMTP host and sender, or leave both empty to disable email")
    with tenant_scope(1):
        row = (await session.execute(select(m.WorkspaceSetting).where(m.WorkspaceSetting.kind == "notification_platform").with_for_update())).scalar_one_or_none()
        if row is None:
            row = m.WorkspaceSetting(kind="notification_platform", name="Platform notification delivery", config={})
            session.add(row)
        config = body.model_dump(exclude={"smtp_password", "clear_password"})
        if body.smtp_password:
            config["password_encrypted"] = encrypt(body.smtp_password)
        elif not body.clear_password and row.config.get("password_encrypted"):
            config["password_encrypted"] = row.config["password_encrypted"]
        row.config = config
        audit(session, user, "notification.platform.update", {"password_changed": bool(body.smtp_password) or body.clear_password})
        await session.commit()
    apply(config)
    return {"status": "Saved. No notification was sent.", **public_settings()}


def matches(config, incident):
    ranks = {"info": 0, "low": 0, "warning": 1, "medium": 1, "high": 2, "critical": 3}
    return (not config.get("service") or config["service"] == incident.service) and ranks.get(incident.severity, 0) >= ranks.get(config.get("minimum_severity", "info"), 0)


@router.post("/admin/notifications/test")
async def test_connection(session=Depends(get_session, scope="function"), user=Depends(administrator)):
    import asyncio
    from app.actions.mail import check_connection
    await load_delivery_settings()
    if not settings.smtp_host or not settings.smtp_sender:
        return {"success": False, "status": "Configure and save an email server first."}
    try:
        await asyncio.to_thread(check_connection)
        result = {"success": True, "status": "Secure SMTP connection and authentication verified. No email was sent."}
    except Exception:
        result = {"success": False, "status": "SMTP connection failed. Check the hostname, port and credentials. No email was sent."}
    audit(session, user, "notification.smtp.test", {"success": result["success"]})
    return result
