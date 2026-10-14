> Current release: customer Automations, container actions and the local executor have been removed and deferred. Historical design references below do not indicate available features.

# Production deployment and application collector

The new collector accepts local application telemetry, stores it durably in SQLite,
and delivers batches to LogIQ with retries and stable event IDs. LogIQ deduplicates
retried events in PostgreSQL. Periodic analysis groups findings into one open
incident per service. Slack alerts use a database outbox and retry after failures.
Infrastructure changes require an explicit operator API request, the execution
flag, and a production target allowlist. AI recommendations do not execute them.

## Start a controlled deployment

Use `infra/docker/compose.production.yml` for a single-server deployment. Only the
HTTPS proxy exposes ports; PostgreSQL, Redis, and the backend stay on the private
Compose network. Caddy obtains certificates for your public domain. Configure DNS
and allow ports 80 and 443 before starting it.

Create a private environment file with:

```dotenv
LOGIQ_DOMAIN=logs.example.com
POSTGRES_PASSWORD=<random-database-password>
JWT_SECRET=<random-secret-at-least-32-characters>
ADMIN_PASSWORD=<unique-password-at-least-12-characters>
INGEST_API_KEY=<random-secret-at-least-32-characters>
OPENAI_API_KEY=
ANALYSIS_INTERVAL_SECONDS=60
ANALYSIS_WINDOW_MINUTES=15
RETENTION_DAYS=30
```

Generate independent secrets using a password manager or
`python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Keep this environment file out of source control. Admin passwords must be at most
72 UTF-8 bytes. Production startup rejects weak/default credentials.

From the inner LogIQ directory:

```sh
docker compose --env-file .env.production -f infra/docker/compose.production.yml up --build -d
```

The dashboard is at `https://logs.example.com`; the application API is at
`https://logs.example.com/api`. Check `/api/ready` for dependency readiness.
Database migrations run before the Compose backend starts.

## Run the background collector beside an application

Python 3.12+ is sufficient; the collector has no third-party dependencies.
Set environment variables in the collector process, not in a browser frontend:

```powershell
$env:LOGIQ_URL = "https://logs.example.com/api"
$env:LOGIQ_INGEST_KEY = "<same-value-as-backend-INGEST_API_KEY>"
python .\collector\collector.py --queue .\collector.sqlite3
```

The collector listens on `127.0.0.1:8181`. Remote delivery requires HTTPS, except
for local development and the private `backend` Docker hostname. The ingest key
can only write ingestion endpoints; it cannot read logs or execute actions. Rotate
it by updating the backend and collectors together. This version has one shared
ingestion key, not individual keys or tenant isolation.

Applications can send JSON to local `/logs` and `/metrics`:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8181/logs `
  -ContentType application/json `
  -Body '{"service":"checkout","level":"error","message":"Database unavailable"}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8181/metrics `
  -ContentType application/json `
  -Body '{"service":"checkout","cpu":35,"memory":52,"latency":140}'
```

Each endpoint accepts one object or an array of up to 100 objects. CPU and memory
are percentages; latency is milliseconds. Your app or an existing monitoring
adapter must measure these values. The collector does not fabricate readings or
automatically scrape Prometheus.

Send from a background logging task with a short timeout. A local `202` means the
record was saved to SQLite; the main application should never wait for delivery
to the central server. Preserve normal application logs when local delivery fails.

### Collect an existing structured log file

If your app writes newline-delimited JSON logs, no HTTP logging change is needed:

```powershell
python .\collector\collector.py --queue .\collector.sqlite3 `
  --log-file C:\apps\checkout\app.jsonl --service checkout
```

Each complete line should contain `level` and `message`; `service` defaults to the
command-line value. The cursor and queue writes commit together, so restarts do
not reread queued lines. Partial lines wait for a newline. Invalid complete lines
are quarantined. The collector detects truncation and file replacement, but does
not drain a renamed file: retain files until collected and prefer rotate-after-
drain. Lines must be smaller than 1 MiB; an oversized line requires operator cleanup.

Run it under your host's service manager with automatic restart and a private,
persistent queue directory. A Linux example is in `collector/logiq-collector.service`.
On Windows use your existing service runner to invoke the same Python command.
The loopback receiver trusts processes on the host; do not expose port 8181 publicly.

### Delivery failures and storage

- Connection errors, authentication failures, HTTP 429, and server failures keep
  records queued; retries use exponential backoff with jitter, capped at 60 seconds.
- HTTP 400/413/422 records are isolated and quarantined in SQLite, not discarded.
- Stable UUID event IDs let the backend ignore duplicate deliveries after retries.
- The queue defaults to 100,000 records and 256 MiB of stored payloads. A full queue
  returns 503, and file collection pauses without advancing its cursor.
- `GET http://127.0.0.1:8181/health` reports pending and rejected counts. Monitor
  growing backlog, rejected counts, and filesystem space; SQLite WAL and allocation
  can use more disk than the payload budget. Protect the SQLite file as log data.
- Redis uses persistent AOF and refuses eviction. Its acceptance is the central
  durability boundary; disk loss still requires backups. Acknowledged records are
  removed only after PostgreSQL commits. Malformed backend events are kept in
  `<stream>.dead` for inspection.

## Monitoring and remediation

LLM analysis supports `LLM_PROVIDER=openai`, `gemini`, or `disabled`. Set
`GEMINI_API_KEY` and `GEMINI_MODEL` for Gemini. Keys stay in backend environment
variables; `/ai/status` returns only the provider, model, and configuration status.
After editing the environment file, recreate the backend container. The Analyze
page shows the active provider. Embeddings still use OpenAI or the offline hash
fallback; selecting Gemini changes the reasoning provider, not the embedding space.

Analysis runs every 60 seconds by default, separately for each recently active
service, and skips unchanged input. A PostgreSQL lock prevents simultaneous
scheduled cycles across replicas. Another lock serializes incident creation for
each service. Set `ANALYSIS_INTERVAL_SECONDS=0` for manual-only analysis.
`GET /analyze?service=checkout` requires a service name.

Error logs have at least medium severity. Resolved/dismissed incidents suppress
older input, while fresh observations can create a new incident. Feedback updates
retrieval memory; this is retrieval augmentation, not model training. Offline hash
embeddings are only a fallback and do not provide meaningful semantic similarity.

Configure Slack before incidents occur to enqueue alerts. Delivery can duplicate
an alert if the process crashes after Slack accepts it but before the database
records success; alerts include the incident ID. Pending alert counts should be
monitored in `notification_outbox`.

Container recovery actions are deferred. No Docker socket or remote execution interface is provided.

## Notifications and personal dashboards

1. Sign in as the administrator and open **Notification settings**. Save the SMTP hostname, secure port (587 STARTTLS or 465 TLS), sender, username/password and approved custom webhook hostnames. **Test SMTP connection** checks TLS and authentication without sending email. SMTP credentials are encrypted in the database; a blank password preserves the saved value.
2. Customers open **Notifications** and add an email, Slack or HTTPS webhook destination. Email recipients must be active verified workspace members. Choose an exact service name or leave it empty for all services, and choose the minimum severity.
3. Select **Send alerts automatically for new matching incidents** to enable automatic delivery. Saving configuration sends no test message. A new matching incident queues an alert for that customer's destinations. Existing open incidents are reused, so each log does not generate another notification. **Test delivery** sends a test message only when clicked; failures are shown in the UI.
4. A separate background worker processes notifications approximately every 15 seconds, independently of AI analysis. Failed deliveries retry; interrupted delivery may repeat a message. Email history distinguishes queue handoff from actual SMTP delivery, which administrators can inspect in **Email delivery**.
5. Customers open **My dashboards**, add widgets, choose a chart type/data signal/service, and customize colors, light/dark/minimal card style, width and height. Move cards with the arrow buttons, choose a time range and refresh interval, then save. Up to 10 personal dashboards and 24 widgets per dashboard are supported; layouts and telemetry remain private to that account/workspace. No data is fabricated for empty ranges.

Notification configuration no longer belongs in `.env`. Environment files retain server startup, database, encryption, domain, billing and supported AI bootstrap settings. AsterCart's three collector connection variables are still required.

## Kubernetes

Create `logiq-secrets` yourself with `postgres-password`, `jwt-secret`,
`admin-password`, `ingest-api-key`, and `openai-api-key`. The manifests no longer
overwrite secrets with example credentials. Provision PostgreSQL and Redis first,
then run `infra/k8s/migration.yaml` and wait for the migration Job to finish before
applying `backend.yaml`. Use a fresh migration Job name for each release; Kubernetes
Job templates are immutable. Backend replicas start only the server, not concurrent
migrations. Redis now requires a PersistentVolumeClaim.

These manifests are a starting point: supply an HTTPS ingress, your CORS origin,
resource budgets, storage class, backups, and network policies. Action permissions
are scoped to the `logiq` namespace. External application namespaces need their
own narrowly scoped role bindings. Fluent Bit uses `/ingest/logs`; explicitly set
the application's service label and ingest key. The direct SQLite collector is the
recommended first integration; Fluent Bit is an optional legacy Compose profile.

## Release checks and remaining limits

Workspace verification: 41 backend/collector/provider/demo tests passed, the dashboard production
build passed, both Compose configurations validated, the migration chain generated
PostgreSQL SQL successfully, and Python dependency compatibility checks passed.
The separate Demo Shop stack was also exercised against live PostgreSQL, Redis,
and LogIQ: scenario logs and metrics arrived and analysis created an incident.
These checks do not replace production TLS, Kubernetes, or load tests.
The authentication dependency was updated to python-jose 3.5.0 and the dashboard
to Next.js 14.2.35; see the [authentication releases](https://github.com/mpdavis/python-jose/releases)
and [Next.js security update](https://nextjs.org/blog/security-update-2025-12-11).

Run `python -m pytest tests` with `backend/requirements-test.txt` installed.
Before release, run the full Compose stack and verify a real database migration,
collector outage/restart recovery, incident resolution, Slack recovery, backups and
restoration, and your expected load. Pin container digests and review/update the
existing backend/frontend dependencies for supported security releases. This work
does not constitute a full dependency/security audit or high-availability deployment.

Back up PostgreSQL and the collector queues; test restores. Keep database and Redis
ports private. Logs/metrics expire after `RETENTION_DAYS`; incidents, embeddings,
notification history, and dead-letter data currently need an operator retention
policy. Account registration, separate tenant isolation, individual collector keys,
and automatic recovery verification remain outside this
implementation. The included deployment has not been validated against a live
Docker or Kubernetes runtime in this workspace.

The dashboard includes basic CPU/memory/latency charts, registered dependencies,
and explicit operator action controls. Charts show the last 100 samples rather than
a complete historical analytics interface.
