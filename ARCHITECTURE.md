> Current release: customer Automations, container actions and the local executor have been removed and deferred. Historical design references below do not indicate available features.

# LOGIQ — Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        DATA COLLECTION                                  │
│                                                                         │
│  Docker / Node / Nginx ──► Fluent Bit ──► HTTP /logs                    │
│  Prometheus + node-exporter ──► /metrics-prom (scrape)                  │
│  App SDK ──► HTTP /metrics                                              │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    INGESTION & STREAM PROCESSING                        │
│                                                                         │
│   FastAPI (POST /logs, /metrics) ─► Redis Streams (logiq.logs/metrics)  │
│                                              │                          │
│   Background asyncio Worker ◄────────────────┘                          │
│        ├── normalize (Fluent Bit / Node / Nginx variants)               │
│        └── persist to Postgres                                          │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        STORAGE LAYER                                    │
│                                                                         │
│   Postgres + pgvector                                                   │
│      logs · metrics · services · dependencies · incidents · memory      │
│   Redis                                                                 │
│      streams · cache · consumer groups                                  │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│   CONTEXT ENGINE          ANOMALY DETECTION                             │
│   ─────────────────       ──────────────────                            │
│   service graph           rules (CPU/mem/lat thresholds)                │
│   neighborhood lookup     z-score                                       │
│                           EWMA                                          │
│                           Isolation Forest                              │
│                                                                         │
│                                    │                                    │
│                                    ▼                                    │
│    ┌────────────────────── AI DECISION ENGINE ────────────────────┐     │
│    │   LangGraph (or linear fallback)                             │     │
│    │   Analyzer → RootCause → Decision → Action                   │     │
│    │   ↑ uses pgvector RAG over incident_memory                   │     │
│    └─────────────────────────────┬────────────────────────────────┘     │
│                                  ▼                                      │
│    ┌────────────────────── ACTION ENGINE ─────────────────────────┐     │
│    │  restart (Docker) · scale (k8s) · rollback (k8s)             │     │
│    │  notify (Slack)   · webhook                                  │     │
│    │  guarded by ACTIONS_AUTO_EXECUTE flag                        │     │
│    └─────────────────────────────┬────────────────────────────────┘     │
└──────────────────────────────────┼──────────────────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│   API (FastAPI, JWT + RBAC) · Prometheus /metrics-prom · Dashboard      │
└─────────────────────────────────────────────────────────────────────────┘
```

## Module map

```
backend/app/
├── ingestion/   normalizer · stream (Redis) · worker
├── anomaly/     rules · zscore · ewma · isolation · detector (orchestrator)
├── ai/          analyzer · embeddings · agents (LangGraph)
├── context/     graph · seed
├── actions/     docker · k8s · notify · executor
├── auth/        jwt · rbac
├── api/         routes_auth · routes_logs · routes_metrics ·
│                routes_analyze · routes_incidents · routes_actions · routes_context
├── db/          base · session · models (with pgvector) · redis_client
└── models/      pydantic schemas
```

## Request flow — `GET /analyze`

1. Pull last 200 metrics + last 10 error logs from Postgres (filtered by service).
2. Run rules / z-score / EWMA / Isolation Forest. Aggregate severity.
3. Look up upstream/downstream services from the dependency graph.
4. RAG: embed the current situation, query `incident_memory` (pgvector L2) for top-3 similar past incidents.
5. Run the LangGraph agents: Analyzer → RootCause → Decision → Action.
6. Persist a new `incidents` row + write a memory embedding (closes the learning loop).
7. Return `AnalysisResponse` with `actions: ["scale:web", "notify:slack", ...]`.

## Security

- Every write endpoint requires a JWT.
- `/action`, `/incidents/*/feedback`, and `/context/*` writes additionally require role `admin` or `devops`.
- Destructive actions (restart/scale/rollback) only run when `ACTIONS_AUTO_EXECUTE=true`; otherwise they return `success=false, detail="suggested only"`.

## Learning loop

- `incident_memory` stores embeddings of past incidents.
- Operators submit `/incidents/{id}/feedback` to mark resolved/dismissed and add a note.
- On next analysis, similar past incidents are retrieved and fed into the agents' state — so corrected explanations bias future reasoning.
