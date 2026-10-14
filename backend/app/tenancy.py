"""Request-local isolation shared by HTTP, analysis and worker sessions."""
from contextvars import ContextVar
from contextlib import contextmanager

tenant_id = ContextVar("logiq_tenant", default=1)


@contextmanager
def tenant_scope(identity):
    token = tenant_id.set(identity)
    try:
        yield
    finally:
        tenant_id.reset(token)


def identity():
    value = tenant_id.get()
    if value is None:
        raise RuntimeError("An explicit customer scope is required")
    return value
