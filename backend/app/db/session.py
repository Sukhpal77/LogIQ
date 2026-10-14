"""Async SQLAlchemy engine + session factory.

Most code uses `async with session_scope() as s:` to get a Session.
"""
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria
from app.db.models import TenantRecord
from app.tenancy import tenant_id


@event.listens_for(Session, "do_orm_execute")
def isolate_rows(state):
    scope = tenant_id.get()
    if scope is not None and (state.is_select or state.is_update or state.is_delete):
        state.statement = state.statement.options(with_loader_criteria(
            TenantRecord, lambda model: model.tenant_id == scope, include_aliases=True))


@event.listens_for(Session, "before_flush")
def isolate_writes(session, *_):
    scope = tenant_id.get()
    for row in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(row, TenantRecord):
            if row in session.new and row.tenant_id is None:
                row.tenant_id = scope
            if scope is not None and row.tenant_id != scope:
                raise ValueError("Cross-customer write rejected")

engine = create_async_engine(settings.db_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope():
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncSession:
    async with session_scope() as s:
        yield s
