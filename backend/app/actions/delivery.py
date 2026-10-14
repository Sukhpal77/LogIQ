"""Customer destinations with a restricted outbound host policy."""
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from app.config import settings


async def validate_destination(config):
    kind = config.get("channel", "slack")
    if kind == "email":
        import re
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", config.get("recipient", "")):
            raise HTTPException(422, "valid recipient required")
        if not settings.smtp_host or not settings.smtp_sender:
            raise HTTPException(503, "email delivery is not configured")
        return
    from app.auth.security import decrypt
    try:
        url = urlsplit(decrypt(config["encrypted_url"]) if config.get("encrypted_url") else config.get("url", ""))
        port = url.port
    except (ValueError, TypeError):
        raise HTTPException(422, "use a valid HTTPS destination")
    if kind not in ("slack", "webhook"):
        raise HTTPException(422, "supported channels are slack and webhook")
    hosts = {x.strip() for x in settings.notification_hosts.split(",") if x.strip()}
    if kind == "slack":
        hosts = {"hooks.slack.com"}
    if url.scheme != "https" or url.hostname not in hosts or url.username or url.password or port not in (None, 443):
        raise HTTPException(422, "use an HTTPS destination on an administrator-approved host")


async def deliver(config, message):
    try:
        await validate_destination(config)
    except HTTPException:
        return {"success":False, "detail":"destination configuration is unavailable or no longer approved"}
    if config.get("channel") == "email":
        from app.db.models import MailOutbox
        from app.db.session import session_scope
        from app.auth.security import encrypt
        async with session_scope() as s:
            from sqlalchemy import select
            from app.db.models import User
            from app.tenancy import identity
            member = (await s.execute(select(User.id).where(User.tenant_id == identity(), User.email == config["recipient"], User.active.is_(True), User.email_verified.is_(True)))).first()
            if not member:
                return {"success": False, "detail": "recipient is no longer an active verified workspace member"}
            s.add(MailOutbox(recipient=config["recipient"], subject="LogIQ incident notification", encrypted_body=encrypt(message)))
        return {"success": True, "detail": "queued for email delivery; see delivery history"}
    from app.auth.security import decrypt
    url = decrypt(config["encrypted_url"]) if config.get("encrypted_url") else config["url"]
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(url, json={"text": message})
        return {"success": 200 <= response.status_code < 300, "detail": f"destination returned HTTP {response.status_code}"}
    except httpx.HTTPError:
        return {"success": False, "detail": "destination unavailable; no credentials exposed"}
