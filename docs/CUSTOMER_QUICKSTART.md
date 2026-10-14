# LOGIQ Customer Quickstart (Simple Version)

For a background collector and production setup, use [PRODUCTION.md](PRODUCTION.md).
This page contains manual JWT-based test examples. Current analysis requires a
service name, and the Fluent Bit service requires the `legacy-collector` profile.

This document is written for first-time users.  
Goal: connect your app to LOGIQ in the easiest way, with clear steps.

If LOGIQ server is not running yet, first follow `docs/SETUP.md`.

---

## What is token and why you need it

- A token is a temporary login key.
- LOGIQ checks this key on protected APIs.
- Without token, API returns `401 Unauthorized`.

There are 2 token names in this guide:

- `LOGIQ_TOKEN`: for direct API calls from app/scripts.
- `LOGIQ_INGEST_TOKEN`: for Fluent Bit collector.

In small setups, both can be the same value.

---

## Minimum config (copy this)

```dotenv
LOGIQ_URL=http://<logiq-host>:8000
LOGIQ_TOKEN=<jwt-token>
LOGIQ_INGEST_TOKEN=<jwt-token>
SERVICE_NAME=my-app
```

### Where to add this config

- Direct app integration: add in your app `.env` or deployment env variables.
- Docker Fluent Bit: add `LOGIQ_INGEST_TOKEN` in Fluent Bit container env.
- Kubernetes Fluent Bit: store token in Secret and map to `LOGIQ_INGEST_TOKEN`.

---

## Step 1: Get token

```bash
TOKEN=$(curl -s -X POST http://<logiq-host>:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)
echo "$TOKEN"
```

If token is empty, check:
- username/password
- LOGIQ URL
- backend health: `GET /health`

---

## Step 2A: Direct app integration (fastest)

Use this when your app can call HTTP endpoints.

### Send first log

```bash
curl -X POST http://<logiq-host>:8000/logs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service":"my-app",
    "level":"info",
    "message":"hello from my app",
    "attrs":{"source":"quickstart"}
  }'
```

### Send first metric

```bash
curl -X POST http://<logiq-host>:8000/metrics \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service":"my-app",
    "cpu":35,
    "memory":52,
    "latency":140
  }'
```

### Verify success

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://<logiq-host>:8000/logs?service=my-app&limit=5"

curl -H "Authorization: Bearer $TOKEN" \
  "http://<logiq-host>:8000/metrics?service=my-app&limit=5"
```

If both show data, integration is done.

---

## Step 2B: Docker integration with Fluent Bit (no app code change)

Use this when apps already write logs to container stdout/files.

### Where token goes

- Token is set as env var `LOGIQ_INGEST_TOKEN` on Fluent Bit.
- You do not add token in app code for this mode.

### Start Fluent Bit

```bash
LOGIQ_INGEST_TOKEN="$TOKEN" docker compose -f infra/docker/docker-compose.yml up -d fluent-bit
```

### Verify

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/logs?limit=20"
```

If no data appears, check `infra/fluent-bit/fluent-bit.conf` output host/port.

---

## Step 2C: Kubernetes integration with Fluent Bit DaemonSet (no app code change)

### Where token goes

- Put token into Secret.
- Map Secret to env var `LOGIQ_INGEST_TOKEN` in Fluent Bit DaemonSet.

### Commands

```bash
kubectl -n logiq create secret generic fluent-bit-token \
  --from-literal=token="$TOKEN"

kubectl apply -f infra/k8s/fluent-bit.yaml
kubectl -n logiq get pods -l app=fluent-bit
kubectl -n logiq logs ds/fluent-bit --tail=100
```

### Verify in LOGIQ

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://<logiq-host>:8000/logs?limit=20"
```

---

## Prometheus users (important)

- `GET /metrics-prom` = Prometheus scrapes LOGIQ internal metrics.
- `POST /metrics` = your app metrics for LOGIQ anomaly detection.

If you only scrape `/metrics-prom`, anomaly engine will not receive your app CPU/memory/latency.

---

## Notifications and personal dashboards

1. Sign in as the administrator and open **Notification settings**. Save the SMTP hostname, secure port (587 STARTTLS or 465 TLS), sender, username/password and approved custom webhook hostnames. **Test SMTP connection** checks TLS and authentication without sending email. SMTP credentials are encrypted in the database; a blank password preserves the saved value.
2. Customers open **Notifications** and add an email, Slack or HTTPS webhook destination. Email recipients must be active verified workspace members. Choose an exact service name or leave it empty for all services, and choose the minimum severity.
3. Select **Send alerts automatically for new matching incidents** to enable automatic delivery. Saving configuration sends no test message. A new matching incident queues an alert for that customer's destinations. Existing open incidents are reused, so each log does not generate another notification. **Test delivery** sends a test message only when clicked; failures are shown in the UI.
4. A separate background worker processes notifications approximately every 15 seconds, independently of AI analysis. Failed deliveries retry; interrupted delivery may repeat a message. Email history distinguishes queue handoff from actual SMTP delivery, which administrators can inspect in **Email delivery**.
5. Customers open **My dashboards**, add widgets, choose a chart type/data signal/service, and customize colors, light/dark/minimal card style, width and height. Move cards with the arrow buttons, choose a time range and refresh interval, then save. Up to 10 personal dashboards and 24 widgets per dashboard are supported; layouts and telemetry remain private to that account/workspace. No data is fabricated for empty ranges.

Notification configuration no longer belongs in `.env`. Environment files retain server startup, database, encryption, domain, billing and supported AI bootstrap settings. AsterCart's three collector connection variables are still required.

## Quick troubleshooting

| Problem | Reason | Fix |
|---|---|---|
| `401 Unauthorized` | token missing/expired | get new token from `/auth/login` |
| Token works but no logs | wrong LOGIQ host/port | confirm backend URL and network |
| Logs show wrong service name | `service` missing | send `service` in payload or Fluent Bit filter |
| Prometheus works but no anomaly output | only scrape path configured | push app metrics to `POST /metrics` |
| Actions not executed | `ACTIONS_AUTO_EXECUTE=false` | enable intentionally and validate permissions |

---

## Which path should I choose?

- Need fastest app test now: Direct HTTP (`/logs` + `/metrics`)
- Need no code changes for containers: Fluent Bit (Docker/K8s)
- Already using Prometheus: keep scrape, plus push selected metrics to `/metrics`
- Need only notifications/actions: operator-only mode

For advanced details and all endpoints, read `docs/INTEGRATIONS.md`.
