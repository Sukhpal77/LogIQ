# LOGIQ — Integration Guide

**Current production path:** see [PRODUCTION.md](PRODUCTION.md) for the durable
SQLite collector, ingestion-only key, HTTPS deployment, and periodic analysis.
The JWT examples below remain useful for manual API testing. Fluent Bit now posts
to `/ingest/logs`; enable the `legacy-collector` Compose profile explicitly.
Prometheus needs a separate adapter to push readings into `/metrics`.

How to connect any application — code, container, cluster, or third-party SaaS — into LOGIQ. Every section explains the wire format, the auth model, the config you have to set on each side, and a copy-pasteable example.

For customer-friendly onboarding with minimum config, start with `CUSTOMER_QUICKSTART.md`.

> Companion docs:
> - `SETUP.md` covers bringing up the LOGIQ stack itself.
> - `CUSTOMER_QUICKSTART.md` is the fastest onboarding path for external users and non-dev operators.

---

## Fast path (choose one integration track)

Use this table to choose the minimum-effort integration path before diving into full details.

| Track | Best for | Required config | App code changes |
|---|---|---|---|
| HTTP logs + metrics | Teams integrating one app quickly | `LOGIQ_URL`, `LOGIQ_TOKEN`, `SERVICE_NAME` | small |
| Fluent Bit (Docker) | Containerized workloads, low-code onboarding | `LOGIQ_INGEST_TOKEN`, backend `Host`/`Port` | none |
| Fluent Bit (Kubernetes) | Cluster-wide log shipping | token secret + DaemonSet env + backend service DNS | none |
| Prometheus + LOGIQ metrics push | Teams already on Prometheus who also need anomaly detection | Prometheus scrape target + `/metrics` push path | optional adapter |
| Outbound actions | Operator-only automation and notifications | action-specific env vars (Slack/webhook/docker/k8s) | none |

### Minimum config contract

Standardize these values for every customer/app onboarding:

```dotenv
LOGIQ_URL=http://<logiq-host>:8000
LOGIQ_TOKEN=<jwt-for-http-api-calls>
LOGIQ_INGEST_TOKEN=<jwt-for-collectors-like-fluent-bit>
SERVICE_NAME=<service-logical-name>
```

For small environments, `LOGIQ_TOKEN` and `LOGIQ_INGEST_TOKEN` can be the same token. For production, issue one token per app/collector and rotate.

### Universal verification checklist

After any integration, verify end-to-end with:

```bash
# health
curl http://<logiq-host>:8000/health

# token
TOKEN=$(curl -s -X POST http://<logiq-host>:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)

# data visibility
curl -H "Authorization: Bearer $TOKEN" "http://<logiq-host>:8000/logs?limit=5"
curl -H "Authorization: Bearer $TOKEN" "http://<logiq-host>:8000/metrics?limit=5"
curl -H "Authorization: Bearer $TOKEN" "http://<logiq-host>:8000/analyze?service=<service>"
```

---

## 0. Mental model

LOGIQ has three integration surfaces:

```
            INBOUND (data into LOGIQ)                       OUTBOUND (LOGIQ acts on you)
  ┌──────────────────────────────────────┐         ┌──────────────────────────────────────┐
  │  POST /logs        (HTTP, JWT)       │         │  notify  → Slack incoming webhook    │
  │  POST /metrics     (HTTP, JWT)       │         │  webhook → generic HTTP POST         │
  │  Fluent Bit → /logs                  │         │  restart → Docker (local socket)     │
  │  Prometheus scrape /metrics-prom     │         │  scale   → Kubernetes apps/v1        │
  │                                      │         │  rollback→ Kubernetes apps/v1 patch  │
  └──────────────────────────────────────┘         └──────────────────────────────────────┘
            ANALYSIS (pull, JWT)
  GET /analyze · GET /incidents · POST /incidents/{id}/feedback · /context/*
```

All HTTP endpoints (except `/health`, `/`, and `/metrics-prom`) require a **JWT bearer token**. Get one with `POST /auth/login` and pass it as `Authorization: Bearer <token>`.

```bash
TOKEN=$(curl -s -X POST http://<logiq-host>:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)
```

Tokens expire after `JWT_EXPIRE_MINUTES` (default 60). For long-running shippers (Fluent Bit, daemons), either re-issue periodically from a sidecar or mint a longer-lived token with a higher `JWT_EXPIRE_MINUTES` and rotate.

Roles: the seed user (`ADMIN_USER`) is `admin`. `admin` and `devops` can hit write endpoints (`/action`, feedback, `/context/*` writes); any authenticated user can ingest and read.

---

## 1. Send logs from your app over HTTP

The simplest integration. Any service that can `curl` can ship logs.

### 1.1 Endpoint

```
POST /logs                      single record
POST /logs/bulk                 array of records
Authorization: Bearer <token>
Content-Type: application/json
```

### 1.2 Payload schema (`LogEntry`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `service` | string | yes | Logical service name. Used to group anomalies and incidents. |
| `level` | string | yes | `info` \| `warn` \| `error` \| `debug`. Aliases (`warning`, `err`, `fatal`, `trace`) are normalized. |
| `message` | string | yes | Free-form log line. |
| `timestamp` | ISO 8601 string | no | Defaults to server now. Accepts `Z` suffix. |
| `attrs` | object | no | Arbitrary key/value metadata; everything not recognized is folded into `attrs`. |

### 1.3 Example — single

```bash
curl -X POST http://logiq:8000/logs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "auth",
    "level": "error",
    "message": "DB connection failed",
    "timestamp": "2026-04-29T10:00:00Z",
    "attrs": {"trace_id": "abc-123", "host": "auth-7"}
  }'
```

### 1.4 Example — bulk (better for batching)

```bash
curl -X POST http://logiq:8000/logs/bulk \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '[
    {"service":"auth","level":"info","message":"login ok"},
    {"service":"auth","level":"error","message":"jwt expired"}
  ]'
```

### 1.5 Reference snippets

**Python**
```python
import httpx, datetime

LOGIQ = "http://logiq:8000"
TOKEN = "..."  # cached JWT

httpx.post(
    f"{LOGIQ}/logs",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={
        "service": "checkout",
        "level": "error",
        "message": str(exc),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "attrs": {"order_id": order.id},
    },
    timeout=2.0,
)
```

**Node.js**
```js
await fetch(`${process.env.LOGIQ_URL}/logs`, {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${process.env.LOGIQ_TOKEN}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    service: "api",
    level: "warn",
    message: "rate limit hit",
    attrs: { ip: req.ip },
  }),
});
```

**Go**
```go
body, _ := json.Marshal(map[string]any{
    "service": "billing", "level": "error", "message": err.Error(),
})
req, _ := http.NewRequest("POST", os.Getenv("LOGIQ_URL")+"/logs", bytes.NewReader(body))
req.Header.Set("Authorization", "Bearer "+os.Getenv("LOGIQ_TOKEN"))
req.Header.Set("Content-Type", "application/json")
http.DefaultClient.Do(req)
```

> The endpoint returns `202 Accepted` immediately. Logs are pushed onto Redis Stream `logiq.logs` and persisted asynchronously by the worker — never block your request path on it.

---

## 2. Ship logs with Fluent Bit (recommended for containers)

LOGIQ ships a ready-to-use Fluent Bit config at `infra/fluent-bit/fluent-bit.conf` that:

- Tails Docker container logs from `/var/lib/docker/containers/*/*.log` (parsed with the `docker` JSON parser).
- Tails Nginx access logs from `/var/log/nginx/access.log`.
- Listens on `:24224` for `forward`-protocol pushes from Node/other apps.
- Adds `ingested_by: fluent-bit` to every record.
- Posts everything to `http://backend:8000/logs` with a bearer token.

### 2.1 What you must configure

| Setting | Where | Why |
|---|---|---|
| `LOGIQ_INGEST_TOKEN` | env var of the Fluent Bit container | Used in the `Authorization` header on the `[OUTPUT]` block. Must be a valid JWT issued by `/auth/login`. |
| `Host` / `Port` | `[OUTPUT]` block | DNS name of the LOGIQ backend (`backend` inside Compose; in K8s use `backend.logiq.svc.cluster.local`). |
| Volume mounts | docker-compose / DaemonSet | Mount `/var/lib/docker/containers` and `/var/run/docker.sock` (read-only). |

### 2.2 Compose

`infra/docker/docker-compose.yml` already wires the volumes; just inject the token:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)

LOGIQ_INGEST_TOKEN="$TOKEN" docker compose -f infra/docker/docker-compose.yml up -d fluent-bit
```

### 2.3 Kubernetes (DaemonSet)

`infra/k8s/fluent-bit.yaml` runs Fluent Bit as a DaemonSet. Stash the token in a secret and reference it:

```bash
kubectl -n logiq create secret generic fluent-bit-token \
  --from-literal=token="$TOKEN"
```

Make sure the DaemonSet's container env contains:

```yaml
env:
  - name: LOGIQ_INGEST_TOKEN
    valueFrom:
      secretKeyRef:
        name: fluent-bit-token
        key: token
```

### 2.4 Adding more inputs

To pull from a new file, append a `[INPUT]` block in `fluent-bit.conf`:

```conf
[INPUT]
    Name              tail
    Tag               app.myservice
    Path              /var/log/myservice/*.log
    Parser            json
    DB                /tmp/flb_myservice.db
    Mem_Buf_Limit     20MB

[FILTER]
    Name              modify
    Match             app.myservice
    Add               service myservice          # <-- crucial: sets LogEntry.service
```

The normalizer (`backend/app/ingestion/normalizer.py`) maps these fields automatically:

| Inbound key | LogEntry field |
|---|---|
| `service` / `kubernetes_container_name` / `tag` | `service` |
| `level` / `severity` / `log_level` | `level` |
| `message` / `log` / `msg` | `message` |
| `timestamp` / `time` / `@timestamp` | `timestamp` |
| anything else | folded into `attrs` |

So if your app already emits structured JSON with those keys, no transformation is needed.

---

## 3. Send metrics from your app

Two paths: push (`POST /metrics`) or pull via Prometheus.

### 3.1 Push — `POST /metrics`

```bash
curl -X POST http://logiq:8000/metrics \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service":"auth","cpu":42.0,"memory":61.0,"latency":230,"timestamp":"2026-04-29T10:00:00Z"}'
```

Schema (`MetricEntry`): `service` (defaults to `"default"`), `cpu` (% 0–100), `memory` (%), `latency` (ms), optional `timestamp`. Returns `202`.

The four anomaly methods (rules / z-score / EWMA / Isolation Forest) operate on the last 200 metric points per service, so for the detectors to be useful, push at a **steady cadence** (e.g., every 10–30s per service).

### 3.2 Pull — Prometheus `/metrics-prom`

LOGIQ exposes its own request-count metrics via `prometheus_client` at `GET /metrics-prom` (no auth — it's the standard scrape pattern). The bundled `infra/prometheus/prometheus.yml` already scrapes:

```yaml
scrape_configs:
  - job_name: logiq-backend
    metrics_path: /metrics-prom
    static_configs:
      - targets: ["backend:8000"]

  - job_name: node-exporter
    static_configs:
      - targets: ["node-exporter:9100"]
```

To add your own services, append a `scrape_configs:` entry. In Kubernetes, the backend Deployment ships these annotations so cluster Prometheus auto-discovers it:

```yaml
prometheus.io/scrape: "true"
prometheus.io/path: /metrics-prom
prometheus.io/port: "8000"
```

> Note: Prometheus scraping captures LOGIQ's own request volume. To get *your* service's CPU/memory/latency into the anomaly engine, push them to `POST /metrics` (or run a small adapter that scrapes Prometheus and reposts).

---

## 3.3 Platform track: Docker Compose onboarding

Use this when you run LOGIQ and workloads with Docker Compose and want minimum per-app setup.

### Required config

- `LOGIQ_INGEST_TOKEN` in the Fluent Bit container environment.
- Fluent Bit output points to the backend service (`Host backend`, `Port 8000` in Compose network).

### Bring-up

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)

LOGIQ_INGEST_TOKEN="$TOKEN" docker compose -f infra/docker/docker-compose.yml up -d fluent-bit
```

### Verify

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/logs?limit=20"
```

---

## 3.4 Platform track: Kubernetes onboarding

Use this for cluster-wide, low-touch shipping where app teams should not edit code.

### Required config

- Secret with collector token.
- DaemonSet env var `LOGIQ_INGEST_TOKEN` reading from that secret.
- Backend service DNS reachable from Fluent Bit (`backend.logiq.svc.cluster.local:8000` in this repo docs).

### Bring-up

```bash
kubectl -n logiq create secret generic fluent-bit-token \
  --from-literal=token="$TOKEN"

kubectl apply -f infra/k8s/fluent-bit.yaml
```

### Verify

```bash
kubectl -n logiq get pods -l app=fluent-bit
kubectl -n logiq logs ds/fluent-bit --tail=100
curl -H "Authorization: Bearer $TOKEN" "http://<logiq-host>:8000/logs?limit=20"
```

---

## 3.5 Platform track: Prometheus users

If your organization already uses Prometheus, treat integration as two lanes:

1. **Prometheus lane**: scrape LOGIQ internal service metrics from `GET /metrics-prom`.
2. **LOGIQ anomaly lane**: push app service telemetry to `POST /metrics`.

Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: logiq-backend
    metrics_path: /metrics-prom
    static_configs:
      - targets: ["backend:8000"]
```

If your app metrics already exist in Prometheus, add a small adapter job that reads required metrics and reposts normalized `cpu`/`memory`/`latency` points into `POST /metrics`.

---

## 4. Build the service-dependency graph

The Context Engine uses a small graph (`services` + `dependencies` tables) to enrich root-cause reasoning ("auth depends on postgres → maybe heavy DB query"). Define it once, update when topology changes.

```bash
# Add a service
curl -X POST http://logiq:8000/context/services \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"payments","kind":"app","metadata":{"team":"billing"}}'

# Add a dependency edge (payments calls postgres)
curl -X POST http://logiq:8000/context/dependencies \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source":"payments","target":"postgres","relation":"calls"}'

# Look up neighborhood (used internally during /analyze)
curl -H "Authorization: Bearer $TOKEN" \
  http://logiq:8000/context/services/payments/neighborhood
```

The seed routine populates a small example graph on first start, but for real usage replace it with your topology (often via a CI job that calls these endpoints from your service catalog).

---

## Notifications and personal dashboards

1. Sign in as the administrator and open **Notification settings**. Save the SMTP hostname, secure port (587 STARTTLS or 465 TLS), sender, username/password and approved custom webhook hostnames. **Test SMTP connection** checks TLS and authentication without sending email. SMTP credentials are encrypted in the database; a blank password preserves the saved value.
2. Customers open **Notifications** and add an email, Slack or HTTPS webhook destination. Email recipients must be active verified workspace members. Choose an exact service name or leave it empty for all services, and choose the minimum severity.
3. Select **Send alerts automatically for new matching incidents** to enable automatic delivery. Saving configuration sends no test message. A new matching incident queues an alert for that customer's destinations. Existing open incidents are reused, so each log does not generate another notification. **Test delivery** sends a test message only when clicked; failures are shown in the UI.
4. A separate background worker processes notifications approximately every 15 seconds, independently of AI analysis. Failed deliveries retry; interrupted delivery may repeat a message. Email history distinguishes queue handoff from actual SMTP delivery, which administrators can inspect in **Email delivery**.
5. Customers open **My dashboards**, add widgets, choose a chart type/data signal/service, and customize colors, light/dark/minimal card style, width and height. Move cards with the arrow buttons, choose a time range and refresh interval, then save. Up to 10 personal dashboards and 24 widgets per dashboard are supported; layouts and telemetry remain private to that account/workspace. No data is fabricated for empty ranges.

Notification configuration no longer belongs in `.env`. Environment files retain server startup, database, encryption, domain, billing and supported AI bootstrap settings. AsterCart's three collector connection variables are still required.

Container and Kubernetes recovery actions are deferred; `/action` is unavailable.

## 9. Closing the learning loop — incident feedback

After acting on an incident, post feedback so the embedding store learns. On the next similar event, RAG over `incident_memory` retrieves past resolutions and biases the agents.

```bash
curl -X POST http://logiq:8000/incidents/42/feedback \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"feedback":"actual cause was deploy of v1.2.3","status":"resolved"}'
```

`status` accepts `resolved` or `dismissed`. Setting either stamps `resolved_at`. Requires the `admin` or `devops` role.

---

## 10. Pulling analysis programmatically

Anything that can poll JSON can build dashboards or alert routes off `/analyze` and `/incidents`:

```bash
# Run on demand for one service
curl -H "Authorization: Bearer $TOKEN" \
  "http://logiq:8000/analyze?service=auth"

# Open incidents (paginatable)
curl -H "Authorization: Bearer $TOKEN" \
  "http://logiq:8000/incidents?status=open&service=auth&limit=50"

# Single incident
curl -H "Authorization: Bearer $TOKEN" \
  "http://logiq:8000/incidents/42"
```

`AnalysisResponse` shape:

```jsonc
{
  "anomaly": true,
  "severity": "critical",        // low | medium | high | critical
  "reason": "High CPU (95%); ...",
  "root_cause": "...",
  "suggestion": "...",
  "actions": ["scale:auth", "notify:slack"],   // verb:target shorthand
  "findings": [{"field":"cpu","value":95,"score":3.2,"method":"zscore","flagged":true}],
  "recent_error_logs": [/* LogEntry[] */],
  "incident_id": 42
}
```

---

## 11. Security checklist for production integrations

- [ ] `JWT_SECRET` is at least 32 random bytes, stored in a secret manager.
- [ ] `ADMIN_PASSWORD` rotated, not the default `admin`.
- [ ] Each shipper (Fluent Bit, Prometheus exporter sidecar, app SDK) gets its **own** JWT — don't share the admin token. Issue them via a small token-broker if you have many.
- [ ] CORS in `app/main.py` is locked down (it currently allows `*` — fine for dev, tighten for shared deployments).
- [ ] TLS terminates in front of LOGIQ (ingress / load balancer) so bearer tokens aren't on the wire in cleartext.
- [ ] Backend's Docker socket mount is removed unless you explicitly want `restart` to work.

---

## 12. External customer handoff pack

Use this template when you provide LOGIQ as a service to other teams/customers.

### 12.1 What to send customers

1. Service endpoint (`LOGIQ_URL`)
2. Token issuance flow (or pre-issued token + expiry policy)
3. Chosen integration track (HTTP / Docker / Kubernetes / Prometheus / Operator-only)
4. Copy-paste onboarding commands for that track
5. Verification commands (`/logs`, `/metrics`, `/analyze`)
6. Support contact and expected response SLA

### 12.2 Standard verification bundle

```bash
# auth check
TOKEN=$(curl -s -X POST http://<logiq-host>:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)

# read checks
curl -H "Authorization: Bearer $TOKEN" "http://<logiq-host>:8000/logs?service=<service>&limit=10"
curl -H "Authorization: Bearer $TOKEN" "http://<logiq-host>:8000/metrics?service=<service>&limit=10"
curl -H "Authorization: Bearer $TOKEN" "http://<logiq-host>:8000/analyze?service=<service>"
```

### 12.3 Integration troubleshooting mini-table

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Expired or invalid JWT | Re-issue token via `/auth/login`, retry with fresh bearer header |
| Collector ships but no logs visible | Wrong backend host/port in output | Validate network path and backend DNS name |
| Logs grouped under wrong/unknown service | Missing `service` field | Set `service` in payload or add Fluent Bit `modify` filter |
| Prometheus is green but no anomalies | Only `/metrics-prom` scrape is configured | Push app telemetry into `POST /metrics` |

---

## 13. Quick reference — every endpoint at a glance

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/login` | none | Exchange username+password for JWT |
| POST | `/logs` | bearer | Ingest one log |
| POST | `/logs/bulk` | bearer | Ingest many logs |
| GET | `/logs` | bearer | Query logs (filters: `service`, `level`, `limit`) |
| POST | `/metrics` | bearer | Ingest one metric point |
| GET | `/metrics` | bearer | Query metrics (filters: `service`, `limit`) |
| GET | `/analyze` | bearer | Run anomaly + agents for `?service=...` |
| GET | `/incidents` | bearer | List incidents |
| GET | `/incidents/{id}` | bearer | Get single incident |
| POST | `/incidents/{id}/feedback` | admin/devops | Submit feedback |
| POST | `/action` | admin/devops | Manually trigger restart/scale/rollback/notify/webhook |
| POST | `/context/services` | admin/devops | Add a service node |
| POST | `/context/dependencies` | admin/devops | Add a dependency edge |
| GET | `/context/services` | bearer | List services |
| GET | `/context/services/{name}/neighborhood` | bearer | Lookup graph neighborhood |
| GET | `/metrics-prom` | none | Prometheus scrape target |
| GET | `/health` | none | Liveness |
| GET | `/` | none | Banner / endpoint index |

For exact request/response models, the live source of truth is **Swagger** at `http://<logiq-host>:8000/docs`.
