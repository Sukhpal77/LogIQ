> Current release: customer Automations, container actions and the local executor have been removed and deferred. Historical design references below do not indicate available features.

# LogIQ SaaS and AsterCart implementation plan

Planning date: 6 October 2026. Status: proposal only; no application changes authorized by this document. Demo name: AsterCart. Pricing currency: INR.

## 1. Product outcome

LogIQ becomes a hosted monitoring service with a public website, a customer workspace, and a separate administration area. A customer subscribes, creates an application, receives an ingestion URL and their own key, installs a collector beside their application, and sees only their organization's data. LogIQ administrators manage customers, plan versions, subscriptions, service health, and AI costs. Authorized administrators can inspect customer telemetry through an audited support workflow.

AsterCart becomes a realistic, separate three-tier online shop. Its business features and controlled failure scenarios demonstrate LogIQ without importing LogIQ collector source into the shop.

The project is not production ready simply because its pages look polished. Completion also requires isolation, reliable billing and collection, tested recovery, secure credentials, and measurable operating limits.

## 2. Existing foundation and gaps

Reuse the FastAPI backend, PostgreSQL, Redis, incident analysis, OpenAI/Gemini provider selection, ingestion reliability work, feedback, notification outbox, and Docker deployment. Keep the existing telemetry API compatible during migration.

The current model has shared services and an ingestion key, basic roles and UI, and a small demo shop. It lacks customer organizations, per-customer credentials, subscription state, payment integration, usage accounting, and end-to-end tenant isolation. The demo currently couples to collector code. Authentication, query pagination, fleet management, and deployment validation need further work.

Preserve existing records under a dedicated legacy/demo organization. Back up before database migrations. Existing services must remain usable while the new onboarding path is introduced.

## 3. Design direction

Use one reusable LogIQ design system: clear typography, consistent spacing, restrained colors, accessible contrast, a sidebar for workspaces, a compact top bar, useful status badges, and readable tables and charts. Include mobile layouts, keyboard navigation, loading states, empty states, validation, permissions, and retryable error states. Support light/dark mode after core layouts are stable.

AsterCart gets its own retail identity, product imagery, catalog cards, product detail layouts, cart and checkout flow, order history, and a protected operations panel. Its identity should be distinct from LogIQ.

Every visible control must either perform a working action or clearly describe a capability unavailable on the customer's plan. No fabricated live charts, inactive purchase buttons, or placeholder settings pages. Seeded dashboards must display a demo-data label.

## 4. Page inventory and behavior

### Public LogIQ website

| Page | Required behavior |
| --- | --- |
| Home | Explain the service, show a real product preview, link to pricing and signup. |
| Features | Explain logs, metrics, incidents, AI analysis, notifications, and controlled automation. |
| Pricing | Read published plan versions from the backend; monthly/yearly choice when supported; compare limits. |
| How it works | Show subscribe → install agent → verify connection → monitor. |
| Integrations | Docker, log files/stdout, supported metrics and runtimes; accurately identify supported capabilities. |
| Guides | Installation, keys, service naming, troubleshooting, quotas, alerts, and automation. |
| Signup/login/recovery | Verified email, password recovery, invite acceptance, safe sessions. |
| Checkout/result | Hosted payment flow; pending/success/failure states confirmed by the server. |
| Support/status/legal | Working support submission and delivery; system status; reviewed privacy and service terms before launch. |

### Customer workspace

| Page | Required behavior |
| --- | --- |
| Onboarding | Purchased-plan summary, application creation, URL/key, generated collector configuration, connection verification, first event checklist. |
| Overview | Own application health, incident counts, collection status, usage, and relevant next actions. |
| Applications | Create/edit/archive applications; environments and services; enforce purchased limits. |
| Application detail | Dependency map, services, recent events, metrics, incidents, agent health. |
| Logs | Time/service/environment/severity filters, search, pagination, event details, scoped exports. |
| Metrics | Real time ranges, units, trends, missing-data states, service comparison. |
| Incidents | Severity, timeline, evidence, root-cause suggestions, acknowledge/resolve, feedback. |
| AI analysis | Run permitted analysis, show provider/model, evidence, fallback status, and usage impact. |
| Integrations and keys | Install instructions, connection test, create/revoke/rotate scoped credentials, one-time secret display. |
| Agents | Heartbeats, versions, last event, queue pressure, connectivity, upgrade instructions. |
| Notifications | Email/Slack/webhook destinations, test delivery, delivery history and failures. |
| Automations | Rule creation, scope, approvals, enabled state, cooldowns, execution history. |
| Usage | Ingested bytes/events, active services, retention, AI tokens/credits, limits and remaining budget. |
| Billing | Current purchased plan/version, invoices, payment status, upgrade/downgrade/cancellation. |
| Team and settings | Owner/operator/viewer permissions, invitations, timezone, redaction and retention choices. |
| Help | Contextual installation and troubleshooting guides; support request. |

### Administration area

Use a distinct admin login entry and server-enforced platform roles; a different login page alone is insufficient. Require MFA for privileged operations and log sensitive administrative actions.

| Page | Required behavior |
| --- | --- |
| Platform overview | Customers, subscriptions, revenue from verified billing records, collection health and AI costs. |
| Customers | Search, organization details, members, plan, usage, account status, support history. |
| Subscriptions | Trials, active/past-due/canceled states, renewals, audited overrides, reconciliation. |
| Plans and prices | Draft/publish/archive versions, prices, limits, effective dates, new-customer preview. |
| Usage and costs | Customer/model/service breakdown, quota breaches, token costs, storage trends. |
| LLM settings | OpenAI/Gemini/disabled mode, validated models, encrypted credentials, test connection, routing and budgets. |
| Fleet and system health | Agents, ingestion backlog, scheduler, dead letters, notifications, database/Redis health. |
| Customer support view | Explicitly select customer, show a clear customer banner, scoped telemetry and an access audit. |
| Automation controls | Platform kill switch, policy limits, executor health and action audits. |
| Audit and security | Admin changes, price versions, credential operations, access and failed authorization. |
| Support/settings | Support queue, email/payment integrations, retention defaults and operational configuration. |

Separate platform administrator, billing administrator, and support roles. Billing staff need pricing/customer/usage information, not raw application logs. Full administrators may inspect telemetry when authorized; never reveal customer passwords or stored API secrets.

## 5. Customer journey

1. Customer signs up, verifies email, and selects a plan or trial.
2. Payment provider processes checkout. A signed server-side webhook confirms payment; the browser success redirect cannot activate paid access.
3. LogIQ creates the subscription and stores its purchased plan version and limits.
4. Customer creates an application and environment.
5. Onboarding displays the common ingestion endpoint and a customer-specific scoped key. A separate custom hostname is optional, not required for isolation.
6. Customer runs the independent agent with endpoint, key, application/environment labels, and selected log/metrics sources.
7. Connection checks confirm agent heartbeat and actual telemetry, then open the application dashboard.
8. Customer configures notifications and, optionally, explicitly permitted recovery actions.

## 6. Proposed plans and price protection

These are seed/showcase proposals, not validated launch prices. Confirm storage, hosting, AI token costs, expected load, payment fees and applicable tax treatment before publishing commercial prices.

| Plan | Monthly draft price | Applications | Active monitored services | Ingestion per billing period | Retention | AI credits per billing period | Team members |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Trial (14 days) | ₹0 | 1 | 4 | 1 GB total trial | 3 days | 20 total trial | 1 |
| Starter | ₹1,499 | 1 | 4 | 10 GB | 7 days | 100 | 2 |
| Team | ₹4,999 | 3 | 15 | 50 GB | 30 days | 500 | 10 |
| Business | ₹14,999 | 10 | 50 | 200 GB | 90 days | 2,000 | 25 |
| Enterprise | Quoted | Contracted | Contracted | Contracted | Contracted | Contracted | Contracted |

One application means one business product, such as AsterCart. One monitored service means a component receiving telemetry, identified by application, environment, and service name. Replicas of that component do not multiply the service count. Production and staging count separately when both are monitored. A dependency listed only on a diagram does not consume a monitoring slot. Four Starter slots allow frontend, API, database, and an optional worker.

Basic monitoring, rules, notifications, and manual actions should remain useful without AI. Team adds collaboration and approval workflows. Business adds opt-in automatic recovery, longer retention and stronger operating controls. Enterprise options such as SSO or dedicated infrastructure must be advertised only after implementation.

An AI credit must have a published token/model budget before sale; a complete analysis may involve several model calls. Track actual input/output and embedding tokens separately. Do not promise an unlimited analysis behind a credit. Handle provider failure and credit settlement explicitly. Cost controls must also cover automatic analyses.

Use immutable plan and price versions. A subscription retains the price AND entitlements it purchased. Example: a Starter customer at ₹1,499 remains on that version when the admin publishes ₹1,799 for new customers. Existing customers change only through an explicit upgrade/migration workflow. Archive old versions for new sales while retaining renewal/history references. Discounts, overrides, trial conversions, annual renewals, cancellation and proration need defined rules.

Warn at configurable thresholds, such as 80% and 100%. Default to no surprise overage charges. At hard limits, report rejected/deferred collection clearly and stop new chargeable AI work; retain existing records until normal retention expiry. Downgrades require choosing which services remain monitored or scheduling the change for renewal. Define finite local queue behavior so a prolonged limit cannot fill customer disks.

## 7. Backend foundations

Add organizations, memberships, applications, environments, scoped services, agents, API credentials, plan/price versions, subscriptions, entitlements, usage events, billing events, destinations, automation policies/executions and audit records.

Tenant identity must come from the authenticated session or key, never a trusted body field. Scope database reads/writes and constraints, dependency links, embeddings/RAG, Redis keys, queues, caches, background jobs, outbox deliveries, exports and remote actions. Add database row-level protection where practical as a second layer. Permit two customers to use the same service name safely.

Use secure HttpOnly sessions/cookies with appropriate CSRF protection, session revocation, rate limits, password reset and verified invitations. Hash ingestion keys, display them once, attach scopes/expiry, and support rotation without breaking collection. Migrate the shared key with a bounded compatibility period.

Meter accepted telemetry once despite retries. Use durable usage records and atomic quota reservations; reconcile cached counters. Record billing-period boundaries, model/cost-rate versions, provider usage, and refunds/failed reservations. Collect metric schema and units consistently. Add time-range pagination, safe exports, and permission checks to existing APIs.

Use a payment adapter with one provider first. Choose the live gateway after checking business eligibility and recurring INR support; evaluate Razorpay and Stripe rather than assuming either is immediately available. Start with a sandbox. Verify webhook signatures, deduplicate events, handle delayed/out-of-order events, reconcile provider state, and define payment-failure grace periods. Real payment credentials and commercial decisions are launch dependencies.

## 8. Independent integration and collection

Publish a separately versioned LogIQ agent Docker image and downloadable configuration. Prefer a standard OpenTelemetry Collector distribution with necessary receivers/exporters and a LogIQ gateway adapter. Prove compatibility with Docker logs, JSONL files, available host/container metrics and the current backend before replacing the existing collector.

Keep the durable queue/retry guarantees: bounded disk buffers, restart recovery, explicit rejected-event handling, queue health and duplicate-safe delivery. Package collector functionality separately from customer application images. AsterCart must not import the collector Queue or copy collector.py into its API image.

The usual installation should need deployment/configuration changes rather than business-code changes. Environment variables alone cannot collect arbitrary private application data: an agent needs access to an existing log/metrics source. Deeper traces and business metrics may require supported automatic instrumentation or an SDK. Document these boundaries honestly.

Plan OTLP ingestion, standard resource attributes, metadata discovery and Prometheus metric adaptation as staged capabilities. Do not claim traces are supported before the full storage/query/UI path works. Keep arbitrary customer containers and Docker daemons inaccessible from the central LogIQ server.

## 9. Notifications and controlled recovery

Build reliable email, Slack and webhook delivery with retry history, redaction, throttling, incident deduplication, recovery messages and escalation rules. Delivery is at-least-once unless the destination supports deduplication; do not promise exactly-once external messages.

Distinguish Docker's restart policy for exited processes from LogIQ recovery for application failures. The first is configured in deployment; the second requires explicit customer policies and a local executor.

Roll out recovery in order: suggestions → manual actions → approved actions → opt-in automatic actions. Use a separate optional executor with outbound authenticated command polling, expiry, replay protection, target allowlists, rate limits, cooldowns, maximum attempts and health checks. Separate its permissions from passive collection. Restrict Docker socket access to the local executor deployment and describe its host privileges. AI may recommend an action but cannot bypass a customer's policy.

Start with restart of explicitly allowlisted stateless containers. Exclude databases and other stateful workloads by default. Prevent restart loops, retain execution evidence, provide a kill switch, and notify after success or failure. Cross-customer targets must be impossible to select. Kubernetes scaling/rollback can follow when policy and identity are verified end to end.

## 10. LLM administration

Keep OpenAI, Gemini and disabled/rules-only modes. Admin can configure validated provider/model routes, encrypted secrets, connectivity tests, request limits, timeouts, fallback and per-customer/platform spend caps. Customers see available AI capabilities and their usage; they never receive platform provider secrets.

Separate reasoning-model configuration from embedding configuration. Existing deterministic offline embeddings are a fallback, not equivalent to semantic embeddings. A provider/dimension change needs an embedding version and a reindex/migration strategy. Incident explanations must show evidence and identify provider failures or fallback results accurately.

## 11. AsterCart scope

Build catalog/search/filter, product detail, cart, sandbox checkout, order confirmation/history, account pages and an operations dashboard for inventory/orders. Use persistent data, validated forms, correct totals and inventory rules. Demo checkout must not charge money.

Add a protected scenario lab: normal traffic; DEBUG/INFO/WARN/ERROR/CRITICAL logs; API failure; database unavailable; slow query; timeout; CPU/memory pressure; stopped/crashed container; notification failure; collector offline and later replay. Mark synthetic signals visibly. Avoid mounting host-control permissions in a public shop endpoint; recovery scenarios belong in the protected local demo harness.

Extend telemetry to all three tiers where supported, label application/environment/service consistently, and correlate an order request to its incident. Demonstrate two independent customer organizations running services with identical names to prove isolation. Make store startup independent of LogIQ availability.

## 12. Seeded showcase data

Provide an idempotent seed command for a dedicated demo environment, blocked by default in production. Seed fictional customers and users, Starter/Team/Business/trial subscriptions, old/new price versions, service graphs, realistic logs/metrics, incidents and feedback, token/usage records, agent states, notification histories and action histories.

Include near-limit, exceeded-limit, expired-trial, past-due and canceled examples. A price-change example must show an existing subscriber retaining the old price and a new subscriber receiving the new version. Seed AsterCart products, stock, users and orders. Mark simulated payments/usage clearly; do not contact real payment or notification services from seeding. Local demo passwords must not become deployed production credentials.

Use relative timestamps so charts remain useful. Allow resetting only explicitly designated demo data, preserving unrelated records. Seed progressively as features become available rather than waiting until the final phase.

## 13. Implementation order and completion gates

| Phase | Work | Completion gate |
| --- | --- | --- |
| 1 | Confirm this scope; AsterCart naming/branding; LogIQ design system and page prototypes; baseline and backup | Reviewable designs, page/action inventory, migration/recovery plan. |
| 2 | Organizations, identity, sessions, roles, tenant migration and scoped APIs | Two customers cannot read/write each other's data, including background/RAG/export paths; existing data preserved. |
| 3 | Versioned plans, entitlements, usage ledger, sandbox payment/subscription lifecycle | Verified payment activates access; retries do not double count; new prices leave existing subscribers unchanged. |
| 4 | Independent agent package, scoped keys, onboarding and connection checks | A separate app sends data without copied collector source; offline queue survives restart and drains correctly. |
| 5 | Public website and all customer pages integrated with real APIs | Signup → subscription → guide → agent → logs/metrics/incidents → usage/billing works end to end. |
| 6 | Admin pages, provider configuration, cost controls, support access and audits | Admin manages plans/customers safely; billing-only role cannot read telemetry; all privileged changes audited. |
| 7 | Notification workflows, policy engine, optional executor, staged auto-recovery | Tests show correct targets, safe limits, failure handling, recovery messages and kill switch. |
| 8 | Full AsterCart business pages and protected scenario lab | Shop/order flow works; realistic failures appear in the correct customer workspace; no LogIQ business-code coupling. |
| 9 | Full seed showcase, operational hardening, documentation and release rehearsal | Restore drill, load/capacity checks, isolation/billing/security regression checks, clean install and upgrades pass. |

Each phase should be a reviewable change with a working demonstration and checks appropriate to its risk. Naming and visual design happen first; production-sensitive functionality follows its dependencies. Maintain an updated checklist rather than declaring the whole system complete after a visual redesign.

## 14. Pending production work and acceptance tests

Upgrade frontend/runtime dependencies to supported releases after checking compatibility; scan dependencies and images. Keep pinned deployable image versions, non-root containers, HTTPS, separate migrations, secret management, backup schedules and restore verification. Add health/readiness, graceful shutdown, queue/dead-letter visibility, retention jobs, storage budgeting and measured load limits. Consider partitioning/archive storage when measurements justify it rather than replacing PostgreSQL immediately.

Required tests cover tenant isolation and identical service names; key rotation; customer/admin permissions; checkout cancellation and delayed/replayed webhooks; quota boundaries and concurrent AI work; grandfathered prices/entitlements; renewal and downgrade; model outage and token accounting; collector/backend/network interruption; duplicate events; action replay and restart limits; notification retries; log redaction; backup restoration; seeded demo reproducibility; and keyboard/mobile UI flows.

Publish installation, upgrades, supported integrations, expected limits and incident-response guides. Do not promise a commercial SLA until actual availability and recovery have been measured. Include customer data export/deletion and retention behavior in the product, with any invoice/audit retention exceptions explicitly reviewed before launch.

Later extensions: traces, SSO, multi-region resilience, richer Kubernetes recovery and dedicated deployments. Keep these out of initial sales claims until working.

## 15. Research supporting the architecture

- OWASP multi-tenant guidance informs tenant-bound authorization and isolation across storage, caches and background work: https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html
- OpenTelemetry documents agent-to-gateway collection: https://opentelemetry.io/docs/collector/deploy/other/agent-to-gateway/
- Its resiliency guidance describes sending queues and persistent storage for restart recovery: https://opentelemetry.io/docs/collector/resiliency/
- Docker documents protecting privileged daemon access: https://docs.docker.com/engine/security/protect-access/
- Stripe documents creating price records and restrictions on changing existing price fields, supporting versioned catalog design: https://docs.stripe.com/products-prices/manage-prices
- Subscription state should be maintained from verified provider events: https://docs.stripe.com/billing/subscriptions/webhooks
- Check the official support policy before selecting a Next.js upgrade target: https://nextjs.org/support-policy

These sources support the technical patterns. They do not validate the proposed commercial prices or certify this project as production ready.
