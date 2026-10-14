> Current release: customer Automations, container actions and the local executor have been removed and deferred. Historical design references below do not indicate available features.

> **SaaS release:** Public pages, customer/admin portals, versioned INR plans, scoped keys, independent agents and the AsterCart showcase are implemented. Start with [RUN_GUIDE.md](docs/RUN_GUIDE.md) and [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md). Older shared-key examples below describe development compatibility; production requires application-scoped credentials.

# LOGIQ — AI-Powered DevOps Brain

Collects logs and metrics, detects anomalies (rules + Z-score + EWMA + Isolation Forest), and suggests likely causes and remediation through a sequential LangGraph flow with retrieval over past incidents. Periodic per-service analysis and a durable background collector are included. Infrastructure actions require an explicit operator request.

**Start here:** [Production deployment and background collector](docs/PRODUCTION.md).
The production guide describes the current integration, security controls, tests,
and remaining limitations. Use the SQLite collector for the first app integration.

**Separate test application:** [Demo Shop](../demo-three-tier/README.md) is a
three-tier Docker app with clickable error, slow-query, CPU, memory, and log-level
scenarios. It connects through the background collector.

**LLM providers:** set `LLM_PROVIDER=openai`, `gemini`, or `disabled`. Gemini uses
`GEMINI_API_KEY` and `GEMINI_MODEL`; see the demo guide for configuration.

> See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full diagram and request flow.

---

## Project layout

```
LogIQ/
├── backend/                  FastAPI service (Python 3.12)
│   ├── app/
│   │   ├── ingestion/        Redis Streams + worker + log normalizer
│   │   ├── anomaly/          rules · zscore · ewma · isolation · detector
│   │   ├── ai/               LangGraph agents · embeddings · LLM helper
│   │   ├── context/          service dependency graph
│   │   ├── actions/          Docker · K8s · Slack · webhook executor
│   │   ├── auth/             JWT + RBAC dependencies
│   │   ├── api/              FastAPI route modules
│   │   ├── db/               SQLAlchemy + pgvector + Redis client
│   │   ├── models/           Pydantic schemas
│   │   ├── config.py         pydantic-settings (.env-aware)
│   │   └── main.py           FastAPI app + lifespan
│   ├── alembic/              DB migrations (init schema + pgvector)
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/dashboard/       Next.js (TypeScript) dashboard
├── infra/
│   ├── docker/docker-compose.yml
│   ├── fluent-bit/           docker + nginx + node parsers, HTTP output
│   ├── prometheus/prometheus.yml
│   └── k8s/                  namespace · postgres · redis · backend · fluent-bit
├── ARCHITECTURE.md
├── .env.example
└── README.md
```

---

## Run locally (dev)

Prereq: Docker + Docker Compose.

```bash
cp .env.example .env
# (optional) set OPENAI_API_KEY=sk-... for real LLM agents
# Configure notification delivery in Admin → Notification settings after login

cd infra/docker
docker compose --env-file ../../.env up --build
```

Services exposed:

| URL                                | Purpose                          |
|------------------------------------|----------------------------------|
| http://localhost:8000              | LOGIQ API (Swagger at `/docs`)   |
| http://localhost:8000/metrics-prom | Prometheus scrape target         |
| http://localhost:9090              | Prometheus UI                    |
| http://localhost:3000              | Dashboard (login: admin / admin) |
| postgres://logiq:logiq@:5432/logiq | Database                         |

Migrations run automatically on backend start (`alembic upgrade head`).

---

## Authenticate

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)
```

---

## Example requests

### Ingest a log
```bash
curl -X POST http://localhost:8000/logs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"service":"auth","level":"error","message":"DB connection failed","timestamp":"2026-04-29T10:00:00"}'
```

### Ingest metrics (baseline + spike)
```bash
for i in $(seq 1 10); do
  curl -s -X POST http://localhost:8000/metrics \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"service":"auth","cpu":30,"memory":50,"latency":200}' > /dev/null
done
curl -X POST http://localhost:8000/metrics \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"service":"auth","cpu":95,"memory":92,"latency":1800}'
```

### Run analysis
```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/analyze?service=auth"
```

Response:
```json
{
  "anomaly": true,
  "severity": "critical",
  "reason": "High CPU (95.0%) detected; Elevated latency (1800 ms)",
  "root_cause": "auth depends on postgres; likely heavy DB query starving CPU",
  "suggestion": "Inspect slow queries; scale auth service horizontally",
  "actions": ["scale:auth", "notify:slack"],
  "incident_id": 42,
  "findings": [...],
  "recent_error_logs": [...]
}
```

### Configure notifications

Use **Admin → Notification settings** for mail delivery and approved hosts. Customers add destinations and enable incident alerts in **Notifications**. Infrastructure actions are deferred.

### Submit feedback (closes the learning loop)
```bash
curl -X POST http://localhost:8000/incidents/42/feedback \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"feedback":"actual cause was deploy of v1.2.3","status":"resolved"}'
```

### List incidents
```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/incidents?status=open"
```

### Add a service to the context graph
```bash
curl -X POST http://localhost:8000/context/services \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"payments","kind":"app"}'
```

---

## Fluent Bit → backend

`infra/fluent-bit/fluent-bit.conf` ships logs from Docker containers, Nginx access logs, and any Node app forwarding to port 24224 — all to the LOGIQ `/logs` HTTP endpoint with a bearer token in `LOGIQ_INGEST_TOKEN`.

Generate an ingest token via `/auth/login` and inject it into Fluent Bit:

```bash
export LOGIQ_INGEST_TOKEN="$TOKEN"
docker compose up fluent-bit
```

---

## Deploy to Kubernetes

```bash
kubectl apply -f infra/k8s/namespace.yaml
kubectl -n logiq create secret generic logiq-secrets \
  --from-literal=postgres-password=...   \
  --from-literal=jwt-secret=...          \
  --from-literal=openai-api-key=sk-...   \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f infra/k8s/postgres.yaml
kubectl apply -f infra/k8s/redis.yaml
kubectl apply -f infra/k8s/backend.yaml
kubectl apply -f infra/k8s/fluent-bit.yaml
```

The backend runs Alembic on startup and exposes `/metrics-prom` for the cluster Prometheus. The bound `ClusterRole` in `backend.yaml` grants the in-cluster `scale` / `rollback` actions only.

---

## Anomaly stack

| Method            | Window     | Triggers when…                                                 |
|-------------------|------------|----------------------------------------------------------------|
| Rule              | latest     | `cpu > 80` / `memory > 85` / `latency > 1000ms` (configurable) |
| Z-score           | last 200   | `\|z\| ≥ 2.5`                                                  |
| EWMA              | last 200   | `\|x_t − ewma_t\| / σ ≥ 2.0` (catches gradual drifts)          |
| Isolation Forest  | last 200   | `predict() == -1` over `(cpu, memory, latency)` vector         |

Severity is `critical` when ≥3 methods agree, `high` when 2, `medium` when 1, `low` otherwise.

---

## Multi-agent flow (LangGraph)

```
Analyzer ─► RootCause ─► Decision ─► Action
                ▲                      │
                └── pgvector RAG ◄─────┘
                    (incident_memory)
```

Each agent calls the configured LLM with strict JSON output. If `OPENAI_API_KEY` is unset (or any call fails), agents fall back to deterministic heuristics so the pipeline always produces a usable answer.

---

## Notifications and personal dashboards

1. Sign in as the administrator and open **Notification settings**. Save the SMTP hostname, secure port (587 STARTTLS or 465 TLS), sender, username/password and approved custom webhook hostnames. **Test SMTP connection** checks TLS and authentication without sending email. SMTP credentials are encrypted in the database; a blank password preserves the saved value.
2. Customers open **Notifications** and add an email, Slack or HTTPS webhook destination. Email recipients must be active verified workspace members. Choose an exact service name or leave it empty for all services, and choose the minimum severity.
3. Select **Send alerts automatically for new matching incidents** to enable automatic delivery. Saving configuration sends no test message. A new matching incident queues an alert for that customer's destinations. Existing open incidents are reused, so each log does not generate another notification. **Test delivery** sends a test message only when clicked; failures are shown in the UI.
4. A separate background worker processes notifications approximately every 15 seconds, independently of AI analysis. Failed deliveries retry; interrupted delivery may repeat a message. Email history distinguishes queue handoff from actual SMTP delivery, which administrators can inspect in **Email delivery**.
5. Customers open **My dashboards**, add widgets, choose a chart type/data signal/service, and customize colors, light/dark/minimal card style, width and height. Move cards with the arrow buttons, choose a time range and refresh interval, then save. Up to 10 personal dashboards and 24 widgets per dashboard are supported; layouts and telemetry remain private to that account/workspace. No data is fabricated for empty ranges.

Notification configuration no longer belongs in `.env`. Environment files retain server startup, database, encryption, domain, billing and supported AI bootstrap settings. AsterCart's three collector connection variables are still required.

Container restart, scale, rollback and customer Automations are deferred and have no executable API in this release.
