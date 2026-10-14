# LOGIQ — Setup & Run Guide

End-to-end instructions to bring up LOGIQ from a fresh checkout: prerequisites, environment variables, the three supported run modes (Docker Compose, local Python dev, Kubernetes), database migrations, first-login + smoke test, and troubleshooting.

> Companion docs:
> - `CUSTOMER_QUICKSTART.md` for minimum-config onboarding (best for external users and non-dev operators).
> - `INTEGRATIONS.md` for full integration reference (Fluent Bit, Prometheus, Slack, Docker, Kubernetes, custom HTTP clients).

---

## 1. Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Docker Engine | 24+ | Compose-based dev stack, container restart actions |
| Docker Compose | v2 plugin | `docker compose` CLI |
| Python | 3.12 | Local backend dev (only if not using Compose) |
| Node.js | 18 LTS or 20 LTS | Local dashboard dev |
| Postgres | 16 + `pgvector` | Storage layer (image `pgvector/pgvector:pg16` is used by Compose) |
| Redis | 7 | Streams + cache |
| `kubectl` + cluster | 1.27+ | K8s deploy + `scale`/`rollback` actions |
| `curl` + `jq` | any | Smoke testing the HTTP API |

OS: tested on Linux, macOS, Windows 11 (WSL2 or Docker Desktop).

---

## 2. Get the code & create the env file

```bash
git clone <repo-url> LogIQ
cd LogIQ
cp .env.example .env
```

`.env` is read by `docker compose` (via `--env-file`) and also by the backend at runtime through `pydantic-settings` (`backend/app/config.py`). Every variable in the file maps directly to a `Settings` field — env names are upper-snake, fields are lower-snake.

### 2.1 Required to set before first run

For local dev the defaults work. For anything beyond your laptop, change at minimum:

```dotenv
JWT_SECRET=<random 32+ char string>
ADMIN_PASSWORD=<strong password>
POSTGRES_PASSWORD=<strong password>
```

### 2.2 Full `.env` reference

| Variable | Default | What it does |
|---|---|---|
| `APP_ENV` | `dev` | Free-form label, surfaced in startup log. |
| `LOG_LEVEL` | `info` | Standard Python log levels (`debug`/`info`/`warning`/`error`). |
| `POSTGRES_HOST` | `postgres` | DB host. Use `localhost` outside Compose. |
| `POSTGRES_PORT` | `5432` | DB port. |
| `POSTGRES_DB` | `logiq` | Database name. |
| `POSTGRES_USER` | `logiq` | DB user. |
| `POSTGRES_PASSWORD` | `logiq` | DB password. **Override in prod.** |
| `REDIS_URL` | `redis://redis:6379/0` | Connection string. Use `redis://localhost:6379/0` outside Compose. |
| `LOG_STREAM` | `logiq.logs` | Redis Stream name for ingested logs. |
| `METRIC_STREAM` | `logiq.metrics` | Redis Stream name for ingested metrics. |
| `CONSUMER_GROUP` | `logiq-workers` | Consumer group used by the ingestion worker. |
| `JWT_SECRET` | `change-me` | HMAC secret signing JWTs. **Must change in prod.** |
| `JWT_ALG` | `HS256` | JWT algorithm. |
| `JWT_EXPIRE_MINUTES` | `60` | Access-token lifetime. |
| `ADMIN_USER` | `admin` | Seed admin username (created on first start if missing). |
| `ADMIN_PASSWORD` | `admin` | Seed admin password. **Override in prod.** |
| `OPENAI_API_KEY` | _empty_ | Enables the LangGraph agents. If empty, deterministic fallbacks run instead. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Chat model used by the agents. |
| `EMBED_MODEL` | `text-embedding-3-small` | Embedding model for incident-memory RAG. |
| `ZSCORE_THRESHOLD` | `2.5` | \|z\| above which a metric point is anomalous. |
| `EWMA_ALPHA` | `0.3` | EWMA smoothing factor. |
| `EWMA_THRESHOLD` | `2.0` | \|x − ewma\| / σ trigger. |
| `RULE_CPU` | `80` | Hard CPU% rule. |
| `RULE_MEM` | `85` | Hard memory% rule. |
| `RULE_LATENCY` | `1000` | Hard latency-ms rule. |

---

## 3. Run mode A — Docker Compose (recommended)

This is the path the README assumes, and it spins up the full stack: Postgres + Redis + backend + Fluent Bit + Prometheus + node-exporter + dashboard.

```bash
cd infra/docker
docker compose --env-file ../../.env up --build
```

The first run takes a few minutes (image build + pip install). On startup the backend will:

1. Wait for Postgres + Redis healthchecks (`depends_on: condition: service_healthy`).
2. Run `alembic upgrade head` (initial schema + pgvector extension).
3. Ensure Redis Streams + the consumer group exist.
4. Seed the admin user and an example service-dependency graph.
5. Spawn the asyncio ingestion worker.
6. Start serving on `:8000`.

### 3.1 Endpoints exposed by Compose

| URL | Container | What it is |
|---|---|---|
| http://localhost:8000 | `backend` | LOGIQ HTTP API. |
| http://localhost:8000/docs | `backend` | Swagger UI. |
| http://localhost:8000/health | `backend` | Liveness probe. |
| http://localhost:8000/metrics-prom | `backend` | Prometheus scrape endpoint. |
| http://localhost:9090 | `prometheus` | Prometheus UI. |
| http://localhost:9100/metrics | `node-exporter` | Host metrics. |
| http://localhost:3000 | `dashboard` | Next.js UI (login `admin`/`admin`). |
| postgres://logiq:logiq@localhost:5432/logiq | `postgres` | DB (port forwarded). |
| redis://localhost:6379 | `redis` | Cache + streams. |

### 3.2 Common Compose commands

```bash
# Detached + tail backend logs
docker compose --env-file ../../.env up -d --build
docker compose logs -f backend

# Restart just the backend (after code change without --build, this won't rebuild)
docker compose up -d --build backend

# Stop everything, keep DB volume
docker compose down

# Wipe DB volume (lose all logs/metrics/incidents)
docker compose down -v
```

### 3.3 Fluent Bit

Compose starts Fluent Bit, but it needs a token to authenticate against `/logs`:

```bash
# get a token first
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)

# inject it and restart fluent-bit
LOGIQ_INGEST_TOKEN="$TOKEN" docker compose up -d fluent-bit
```

See `INTEGRATIONS.md` §2 for the long-form integration.

---

## 4. Run mode B — Local Python dev (backend)

Use this when you want hot-reload on the backend and don't mind running Postgres + Redis in Docker.

```bash
# 1. Bring up just the data dependencies
cd infra/docker
docker compose --env-file ../../.env up -d postgres redis

# 2. Point the backend at localhost
cd ../../backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Override the two host-sensitive vars for this shell
export POSTGRES_HOST=localhost
export REDIS_URL=redis://localhost:6379/0
# (or copy .env into backend/ and edit those two lines)

# 4. Apply migrations
alembic upgrade head

# 5. Run with reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The lifespan hook still seeds the admin + example graph on first start.

### 4.1 Local dashboard dev

```bash
cd frontend/dashboard
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
# open http://localhost:3000
```

For prod build:
```bash
npm run build && npm start
```

---

## 5. Run mode C — Kubernetes

Manifests live under `infra/k8s/` and target a namespace called `logiq`.

```bash
kubectl apply -f infra/k8s/namespace.yaml

# Generate secrets (do NOT commit these values)
kubectl -n logiq create secret generic logiq-secrets \
  --from-literal=postgres-password=<strong-pw> \
  --from-literal=jwt-secret=<random-32-chars> \
  --from-literal=openai-api-key=sk-... \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f infra/k8s/postgres.yaml
kubectl apply -f infra/k8s/redis.yaml
kubectl apply -f infra/k8s/backend.yaml      # Deployment + Service + ServiceAccount + ClusterRole/Binding
kubectl apply -f infra/k8s/fluent-bit.yaml
```

What `backend.yaml` provides:

- 2 replicas of the FastAPI app.
- `readinessProbe`/`livenessProbe` on `/health`.
- Prometheus scrape annotations (`prometheus.io/scrape`, path `/metrics-prom`, port `8000`).
- A `ServiceAccount` (`logiq-backend`) bound to a `ClusterRole` (`logiq-actions`) with `get/list/patch/update` on `apps/deployments` and `apps/deployments/scale` — exactly what the `scale`/`rollback` actions need.

Push your image to whatever registry the cluster pulls from and update `image: logiq/backend:latest` in `backend.yaml` accordingly. The container's startup command runs Alembic and then Uvicorn (see `backend/Dockerfile`), so migrations happen on every rollout.

### 5.1 Verify the cluster install

```bash
kubectl -n logiq get pods                        # all should be Running
kubectl -n logiq port-forward svc/backend 8000:8000
curl http://localhost:8000/health
```

---

## 6. Database migrations

Schema changes live under `backend/alembic/versions/`. The backend container runs `alembic upgrade head` automatically on every start, so in normal use you don't run anything manually.

To author a new migration locally:

```bash
cd backend
source .venv/bin/activate
alembic revision -m "describe change" --autogenerate
alembic upgrade head
```

`alembic.ini` reads the DB URL from `app/config.py` (`db_url_sync`), so it picks up `.env` automatically.

---

## 7. First login + smoke test

```bash
# 1. Get a JWT
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)
echo "$TOKEN"

# 2. Push a baseline of metrics
for i in $(seq 1 10); do
  curl -s -X POST http://localhost:8000/metrics \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"service":"auth","cpu":30,"memory":50,"latency":200}' > /dev/null
done

# 3. Push a spike
curl -s -X POST http://localhost:8000/metrics \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"service":"auth","cpu":95,"memory":92,"latency":1800}'

# 4. Trigger analysis
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/analyze?service=auth" | jq

# 5. Confirm an incident was created
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/incidents?status=open" | jq
```

You should see `anomaly: true`, a populated `actions` array, and the new incident ID.

Then open the dashboard at http://localhost:3000 and log in with `admin`/`admin`.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend container restarts in a loop | Postgres not yet healthy when backend started, or migration failed | `docker compose logs backend` — re-run `alembic upgrade head` manually if needed |
| `relation "logs" does not exist` | Migrations didn't run | Inside the container: `alembic upgrade head` |
| `pgvector` extension errors | Wrong Postgres image | Confirm image is `pgvector/pgvector:pg16` (not stock `postgres`) |
| 401 on every request | Token expired (`JWT_EXPIRE_MINUTES=60` by default) or `JWT_SECRET` rotated | Re-login |
| Fluent Bit logs `401 Unauthorized` | `LOGIQ_INGEST_TOKEN` empty or expired | Issue a fresh token, restart fluent-bit |
| `/analyze` returns heuristic answers, no LLM | `OPENAI_API_KEY` unset | Set the key, restart backend; fallbacks are by design when the key is missing |
| `scale`/`rollback` fails with `Forbidden` | Pod's ServiceAccount lacks the ClusterRoleBinding | Apply `infra/k8s/backend.yaml` (it ships the binding) |
| Dashboard can't reach API in browser | `NEXT_PUBLIC_API_URL` wrong or CORS blocked | Compose sets it to `http://localhost:8000`; the backend allows all origins via CORSMiddleware |
| Prometheus shows `backend` target down | Wrong network or path | Targets `backend:8000` from inside Compose; path is `/metrics-prom`, not `/metrics` |


---

## 9. Stopping & cleaning up

```bash
# Compose
cd infra/docker
docker compose down            # stop, keep data
docker compose down -v         # stop + drop pgdata volume

# K8s
kubectl delete namespace logiq
```

---

## 10. Where to look next

- Fast customer onboarding: `CUSTOMER_QUICKSTART.md`.
- API reference: open http://localhost:8000/docs once the backend is up — every route is documented from the FastAPI schemas.
- Architecture: `ARCHITECTURE.md`.
- Wiring external systems into LOGIQ: `INTEGRATIONS.md`.

## Notifications and personal dashboards

1. Sign in as the administrator and open **Notification settings**. Save the SMTP hostname, secure port (587 STARTTLS or 465 TLS), sender, username/password and approved custom webhook hostnames. **Test SMTP connection** checks TLS and authentication without sending email. SMTP credentials are encrypted in the database; a blank password preserves the saved value.
2. Customers open **Notifications** and add an email, Slack or HTTPS webhook destination. Email recipients must be active verified workspace members. Choose an exact service name or leave it empty for all services, and choose the minimum severity.
3. Select **Send alerts automatically for new matching incidents** to enable automatic delivery. Saving configuration sends no test message. A new matching incident queues an alert for that customer's destinations. Existing open incidents are reused, so each log does not generate another notification. **Test delivery** sends a test message only when clicked; failures are shown in the UI.
4. A separate background worker processes notifications approximately every 15 seconds, independently of AI analysis. Failed deliveries retry; interrupted delivery may repeat a message. Email history distinguishes queue handoff from actual SMTP delivery, which administrators can inspect in **Email delivery**.
5. Customers open **My dashboards**, add widgets, choose a chart type/data signal/service, and customize colors, light/dark/minimal card style, width and height. Move cards with the arrow buttons, choose a time range and refresh interval, then save. Up to 10 personal dashboards and 24 widgets per dashboard are supported; layouts and telemetry remain private to that account/workspace. No data is fabricated for empty ranges.

Notification configuration no longer belongs in `.env`. Environment files retain server startup, database, encryption, domain, billing and supported AI bootstrap settings. AsterCart's three collector connection variables are still required.
