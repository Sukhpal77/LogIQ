"""FastAPI entrypoint for LOGIQ.

Lifespan:
  - Ensure Redis Streams + consumer groups exist.
  - Seed admin user + example service graph if missing.
  - Spawn the background ingestion worker.

Routes:
  /auth/login                      - issue JWT
  POST /logs, GET /logs            - log ingestion + listing
  POST /metrics, GET /metrics      - metric ingestion + listing
  GET /analyze                     - run anomaly + AI agents
  GET /incidents, POST feedback    - persisted incidents + feedback loop
  /context/*                       - service dependency graph
  /metrics-prom                    - Prometheus scrape target
  /health                          - liveness
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from starlette.responses import Response

from app.api import (
    routes_analyze,
    routes_auth,
    routes_context,
    routes_incidents,
    routes_logs,
    routes_metrics,
)
from app.config import settings
from app.context.seed import seed_all
from app.db.redis_client import close_redis
from app.ingestion.stream import ensure_groups
from app.ingestion.worker import run as worker_run
from app.monitor import run as monitor_run
from app.monitor import run_notifications
from app.db.session import engine
from app.db.redis_client import get_redis
from sqlalchemy import text
from fastapi import Depends
from app.auth.rbac import current_user
from app.auth.middleware import workspace_middleware
from app.api import routes_workspace
from app.api import routes_resources
from app.api import routes_billing
from app.api import routes_identity
from app.api import routes_explorer
from app.api import routes_dashboards, routes_notification_config
from app.api import routes_insights
from app.insights.worker import run as insights_run

logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("logiq")

REQUEST_COUNT = Counter("logiq_http_requests_total", "Total HTTP requests", ["method", "path", "status"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting LOGIQ backend (env=%s)", settings.app_env)
    await ensure_groups()
    await seed_all()
    await routes_workspace.seed_catalog()
    await routes_workspace.load_llm_settings()
    await routes_notification_config.load_delivery_settings()

    worker_task = asyncio.create_task(worker_run(), name="ingestion-worker")
    monitor_task = asyncio.create_task(monitor_run(), name="monitor")
    notification_task = asyncio.create_task(run_notifications(), name="notification-delivery")
    insights_task = asyncio.create_task(insights_run(), name="historical-summaries")
    try:
        yield
    finally:
        insights_task.cancel()
        await asyncio.gather(insights_task, return_exceptions=True)
        notification_task.cancel()
        await asyncio.gather(notification_task, return_exceptions=True)
        worker_task.cancel()
        if monitor_task:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        await close_redis()
        await engine.dispose()


app = FastAPI(
    title="LOGIQ — AI DevOps Brain",
    description="Logs + metrics → anomaly detection → multi-agent AI analysis → investigation and alerts.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.middleware("http")(workspace_middleware)


@app.middleware("http")
async def prom_middleware(request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > 1048576):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=413, content={"detail": "maximum request size is 1 MiB"})
    response = await call_next(request)
    REQUEST_COUNT.labels(request.method, getattr(request.scope.get("route"), "path", "/unmatched"), str(response.status_code)).inc()
    return response


app.include_router(routes_auth.router)
app.include_router(routes_logs.router)
app.include_router(routes_metrics.router)
app.include_router(routes_analyze.router)
app.include_router(routes_incidents.router)
app.include_router(routes_context.router)
app.include_router(routes_workspace.router)
app.include_router(routes_resources.router)
app.include_router(routes_billing.router)
app.include_router(routes_identity.router)
app.include_router(routes_explorer.router)
app.include_router(routes_dashboards.router)
app.include_router(routes_notification_config.router)
app.include_router(routes_insights.router)
from app.limits import BodyLimitMiddleware
app.add_middleware(BodyLimitMiddleware)


@app.get("/")
def root() -> dict:
    return {
        "name": "LOGIQ",
        "version": "1.0.0",
        "endpoints": [
            "POST /auth/login",
            "POST /logs",
            "POST /metrics",
            "GET /analyze",
            "GET /incidents",
            "GET /context/services",
            "GET /metrics-prom",
            "GET /health",
        ],
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ai/status")
def ai_status(_user=Depends(current_user)):
    return {"provider": settings.llm_provider, "model": settings.llm_model,
            "configured": settings.llm_configured,
            "embedding_provider": "openai" if settings.openai_api_key else "offline-hash"}


@app.get("/ready")
async def ready():
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await get_redis().ping()
        return {"status": "ready"}
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "unavailable"})


@app.get("/metrics-prom")
def prom() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
