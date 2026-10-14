# Run LogIQ and AsterCart

This release is a working local SaaS showcase. Checkout in the default environment is simulated and charges no money. Customer data and keys are separate from platform administration.

## Start everything on Windows

Open PowerShell in `C:\Users\demo\Downloads\LogIQ` with Docker Desktop running:

```powershell
.\demo-three-tier\setup.ps1
```

The script builds LogIQ, migrates its existing database, seeds fictional customers, creates an application-scoped AsterCart ingestion key and starts the separate shop and three collectors. Existing database volumes remain intact. Re-running creates a replacement ingestion key; revoke unused older keys in the customer portal.

| Site | Address | Local showcase account |
| --- | --- | --- |
| LogIQ customer | http://localhost:3000/login | `aster.owner@example.test` / `LogIQ-showcase-2026!` |
| Platform administrator | http://localhost:3000/admin/login | Your `ADMIN_USER` and original seeded password from `LogIQ/.env` |
| AsterCart operator | http://localhost:8088/account | `demo@astercart.test` / `AsterCart-demo-2026!` |

Changing `ADMIN_PASSWORD` after the account exists does not reset its stored password. Use the account reset flow or the operator recovery procedure below.

Other fictional customer usernames: `northstar.owner@example.test`, `harbor.owner@example.test`, `trial.owner@example.test`, `expired.owner@example.test`, `near.owner@example.test`, `over.owner@example.test`, `pastdue.owner@example.test`, and `canceled.owner@example.test`. All use the local showcase password above. Harbor retains its historical ₹1,299 Starter version; new Starter purchases use ₹1,499. All prices are monthly INR proposals.

## Try the shop and see its logs

1. Browse products, add a product to the bag, sign in and complete sandbox checkout. Orders and inventory persist in AsterCart's own database.
2. As the AsterCart operator, open its scenario lab. Try normal, warning, API error, database error, slow query, critical and synthetic metric scenarios. These are deliberately labeled demo faults.
3. Open LogIQ's log explorer. API telemetry uses `astercart/production/astercart-api`; actual Nginx and PostgreSQL logs use `astercart-web` and `astercart-db` under the same application prefix.
4. Open Metrics, Incidents and AI analysis. Without a configured provider key, investigation uses heuristics and identifies that mode in the result.
5. Check Agents for heartbeats and pending/rejected queue counts. Heartbeats do not prove that a source file is readable; verify real events too.

CPU and memory percentages currently come from the demo API process sampler. Database/web server logs are collected, but full host/container metrics and distributed traces are separate future integrations.

## Connect another application

In its own LogIQ customer workspace, create an application/environment and generate an ingestion key. Save the key once; only its hash is stored. Newly issued keys expire after 90 days by default; the API supports 1–365 days. For rotation, install a new key, verify delivery, then revoke the old one.

Download the independent package from the Setup guide, extract it, and build its image:

```powershell
docker build -t logiq-agent:1.1.0 .
$env:LOGIQ_URL='https://YOUR-LOGIQ-DOMAIN/api'
$env:LOGIQ_INGEST_KEY='YOUR_APPLICATION_KEY'
docker run -d --restart unless-stopped -e LOGIQ_URL -e LOGIQ_INGEST_KEY -v YOUR_LOG_FOLDER:/source:ro -v logiq-agent-data:/data logiq-agent:1.1.0 --log-file /source/app.jsonl --metrics-file /source/metrics.jsonl --service api
```

For local Docker Desktop use `http://host.docker.internal:8000` and additionally set/pass `LOGIQ_ALLOW_LOCAL_HTTP=true`. Remote endpoints require HTTPS. Each independent collector needs its own durable queue volume. Mount only the log sources it should read. PostgreSQL JSON logs and the supplied Nginx JSON format are supported in the demo.

No collector source is copied into AsterCart's application image. Environment variables configure delivery, but the agent still needs access to an existing log/metric file. Arbitrary applications without an accessible source need deployment logging configuration or supported instrumentation. OTLP ingestion and traces are not yet supported.

The queue is bounded to 100,000 events / 256 MiB by default. Full queues stop advancing file cursors. Unread files rotated away during a prolonged outage can still be lost; retain source archives until caught up. Rejected events remain in the local queue for inspection, not silent deletion.

## Production configuration

Use the existing production compose/Caddy deployment with your domain, HTTPS, private PostgreSQL/Redis, strong admin password and independent random `JWT_SECRET` / `SECRETS_ENCRYPTION_KEY` values of at least 32 characters. Back up the encryption key securely; changing it without a migration makes saved secrets unreadable. The old shared ingestion key is accepted only in development. Production uses application-scoped keys.

Configure `PUBLIC_URL` and explicit `CORS_ORIGINS` in the server environment. Configure SMTP host/port/sender/authentication in **Admin → Notification settings**. Production signup verifies email; reset and staff/team invitations use expiring emailed links. Platform administrators and staff must enroll MFA before receiving full privileged access. SMTP authentication is optional only when your configured relay deliberately allows it. SMTP delivery and notification retries continue when automatic analysis is disabled.

The Admin LLM page changes provider/model routing, adds or replaces encrypted provider keys, and tests connectivity with safe failure details. Blank key fields preserve existing credentials. Server environment keys remain supported; saved admin keys take precedence. Customer browsers never receive these secrets. Credits are bounded to up to four reasoning calls, 24,000 prompt characters per call and 2,048 output tokens per call, with separate embedding usage. Token counters are recorded; currency cost estimates require a validated provider cost catalog.

Platform administration includes customer account status, subscriptions, plan pricing and aggregated usage. Customer workspace impersonation is disabled, including requests using `X-Workspace-Id`. Customer logs, metrics and incidents stay in their own workspace. Administrator email-body preview is also disabled to protect account recovery links. In Plans & pricing, select **Edit price & limits**, adjust the values, and publish a new version for future purchases; existing subscriptions retain their purchased version.

Default billing is `sandbox` in development. Production purchases remain disabled unless `BILLING_PROVIDER=stripe` is configured with `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` and matching monthly INR `stripe_price_id` values for immutable plan versions. Register the signed webhook at `/api/billing/webhook` through the production proxy, enable the supported checkout/subscription/invoice events, and rehearse payment success, cancellation, failure and renewal using the provider's test environment. Verify account eligibility and local commercial requirements before enabling real payments. A browser redirect never activates a paid subscription.

New plan publication preserves the old price and entitlements for existing subscribers. Plan changes are explicit, and a downgrade is rejected while current resources exceed its limits. Sandbox changes start a new simulated 30-day period; live payment proration is handled by the provider. No automatic overage billing is enabled.

## Checks and backup rehearsal

From the workspace root, using the provided local Python environment:

```powershell
.\.venv\Scripts\python.exe -m pytest LogIQ/tests -q
.\.venv\Scripts\python.exe demo-three-tier/smoke_test.py
.\.venv\Scripts\python.exe demo-three-tier/test_queue_recovery.py
.\.venv\Scripts\python.exe demo-three-tier/test_platform_flows.py
.\.venv\Scripts\python.exe demo-three-tier/check_logiq_history.py
.\.venv\Scripts\python.exe demo-three-tier/check_logiq_recurrence.py
.\LogIQ\scripts\verify_backup.ps1
```

The platform-flow harness creates local validation accounts and ingestion keys, checks staff permissions, revocation, duplicate events and quotas. It refuses production. Backup rehearsal restores into a uniquely named temporary database and preserves the snapshot under `.backups`; it never restores over the working database. The default backup script expects the local `logiq` database/user; adapt those parameters for a different deployment.

For an operator recovery after loss of administrator MFA, run `python -m app.admin_recovery --username YOUR_ADMIN --reason "Recovery reason"` inside an interactive backend container terminal. It prompts for the new password, clears MFA, revokes existing sessions and records an audit event. The administrator must enroll MFA again in production. Limit access to this host-level recovery procedure.

See `IMPLEMENTATION_STATUS.md` for verification evidence and remaining launch gates. The local showcase is not a commercial production certification.

## Notifications and personal dashboards

1. Sign in as the administrator and open **Notification settings**. Save the SMTP hostname, secure port (587 STARTTLS or 465 TLS), sender, username/password and approved custom webhook hostnames. **Test SMTP connection** checks TLS and authentication without sending email. SMTP credentials are encrypted in the database; a blank password preserves the saved value.
2. Customers open **Notifications** and add an email, Slack or HTTPS webhook destination. Email recipients must be active verified workspace members. Choose an exact service name or leave it empty for all services, and choose the minimum severity.
3. Select **Send alerts automatically for new matching incidents** to enable automatic delivery. Saving configuration sends no test message. A new matching incident queues an alert for that customer's destinations. Existing open incidents are reused, so each log does not generate another notification. **Test delivery** sends a test message only when clicked; failures are shown in the UI.
4. A separate background worker processes notifications approximately every 15 seconds, independently of AI analysis. Failed deliveries retry; interrupted delivery may repeat a message. Email history distinguishes queue handoff from actual SMTP delivery, which administrators can inspect in **Email delivery**.
5. Customers open **My dashboards**, add widgets, choose a chart type/data signal/service, and customize colors, light/dark/minimal card style, width and height. Move cards with the arrow buttons, choose a time range and refresh interval, then save. Up to 10 personal dashboards and 24 widgets per dashboard are supported; layouts and telemetry remain private to that account/workspace. No data is fabricated for empty ranges.

Notification configuration no longer belongs in `.env`. Environment files retain server startup, database, encryption, domain, billing and supported AI bootstrap settings. AsterCart's three collector connection variables are still required.

## Failure history and log summaries

Customers open **History & insights** in the sidebar:

1. **Past failures** filters incidents by service/environment, status, category, search or confirmed cause. Owners/operators select **Record cause & fix** to record the actual cause, action, outcome, component and deployment version. Confirming a cause requires a cause description. AI suggestions remain separately labeled and are not overwritten. Viewers can read but cannot change these records.
2. **Log summaries** shows all retained event counts for the chosen period, severity totals, error patterns, hourly timeline, service totals and changes from the previous equal period. Available history follows the purchased retention limit. Zero baseline events do not produce a percentage. **View evidence** opens a redacted source event; expired evidence is unavailable.
3. **Saved digests** shows completed hourly/daily UTC windows. An independent worker checks every five minutes and updates recent windows for late arrivals. Owners/operators can rebuild older digests while their source logs remain available. Stored digests are removed when their window starts outside the plan's retention period.

These summaries use database calculations, incur no LLM usage and send no email/webhook messages. They describe observed logs; missing events do not prove an application is healthy. Future-failure predictions are not implemented yet. Administrators and other customers cannot access this history or its evidence.

The local history check uses disposable synthetic records, tests totals/redaction/permissions/rebuild, and removes its fixtures. It refuses production and sends no external notifications. A historical confirmed fix may help a later investigation; it does not establish the cause of a new incident.


## Recurrence warning preview

1. In **Log summaries**, choose the service/time range and click **View evidence** for an error. Note its evidence ID. In **Past failures → Record cause & fix**, enter matching error IDs, separated by commas. Confirm the cause you verified and mark the incident resolved. Evidence must be retained, belong to the same service/workspace, and occur between one hour before the incident opened and its closing time. Existing confirmed incidents without linked evidence do not trigger recurrence matches.
2. Open **Early warnings**. Every five minutes, the worker compares sustained errors from the last hour with the selected patterns of previous confirmed incidents that closed before that hour. A pattern needs at least three events across two separate minutes and an event within the last ten minutes. Owners/operators can select **Check patterns now**; viewers can read results.
3. Inspect the current error evidence, previous confirmed cause/action/outcome, matching criteria and uncertainty. **Record assessment** accepts useful, incorrect, resolved or prevented, plus optional notes. These assessments do not hide ongoing observed errors or confirm the current cause.

Repeated checks update the same warning instead of creating duplicates. Expired observations show **No recent supporting observation**; this does not prove recovery or collector health. Historical confirmation changes are reflected immediately when reading warnings. Source retention applies to derived fingerprints and warning records. Scans cover up to 500 recent confirmed incidents and 100 sustained patterns per workspace; no match is not a guarantee of complete coverage.

This preview detects the return of observed errors. It does not predict a future crash, infer a failure probability, verify the current deployment/component, call an LLM, send messages or restart anything. Metric forecasting and opt-in predictive notifications remain later phases. The recurrence validation harness uses disposable synthetic records and checks deduplication, different error codes, stale data, confirmation revocation, redaction and access restrictions.
