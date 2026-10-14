"""Seed a small example service graph and an admin user on first boot."""
from app.auth.jwt import hash_password
from sqlalchemy import select, text

from app.config import settings
from app.context.graph import add_dependency, upsert_service
from app.db import models
from app.db.session import session_scope
from app.models.schemas import DependencyEdge, ServiceNode

async def seed_admin() -> None:
    async with session_scope() as s:
        await s.execute(text("SELECT pg_advisory_xact_lock(7391043)"))
        existing = (
            await s.execute(select(models.User).where(models.User.username == settings.admin_user))
        ).scalar_one_or_none()
        if existing:
            if settings.admin_email and not existing.email:
                existing.email = settings.admin_email
            return
        s.add(
            models.User(
                username=settings.admin_user,
                email=settings.admin_email or None,
                password_hash=hash_password(settings.admin_password),
                role="admin",
            )
        )


async def seed_graph() -> None:
    """Idempotent example topology: auth -> postgres, web -> auth, web -> redis."""
    async with session_scope() as s:
        await s.execute(text("SELECT pg_advisory_xact_lock(7391044)"))
        existing = (await s.execute(select(models.Service))).first()
        if existing:
            return
        for n in [
            ServiceNode(name="web", kind="app"),
            ServiceNode(name="auth", kind="app"),
            ServiceNode(name="postgres", kind="db"),
            ServiceNode(name="redis", kind="cache"),
        ]:
            await upsert_service(s, n)
        for e in [
            DependencyEdge(source="web", target="auth", relation="calls"),
            DependencyEdge(source="auth", target="postgres", relation="reads"),
            DependencyEdge(source="web", target="redis", relation="reads"),
        ]:
            await add_dependency(s, e)


async def seed_all() -> None:
    await seed_admin()
    if settings.app_env != "production":
        await seed_graph()
