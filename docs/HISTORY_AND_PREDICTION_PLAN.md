# LogIQ failure history, log summaries and prediction plan

Status: failure history and factual summaries are implemented. Phase 3 exact-pattern recurrence checks were added on 7 October 2026. Metric forecasts, predictive notifications and real pilot calibration remain planned.

The current customer page has **Past failures**, **Log summaries** and **Saved digests** tabs. The Early warnings tab now previews sustained recurring errors using explicitly customer-linked historical evidence. Future-failure predictions remain planned. This release records customer-confirmed causes and fixes separately from AI suggestions, counts every retained event in the selected period, and saves hourly/daily UTC digests in a separate background worker. Summaries make no LLM calls and send no notifications. See `RUN_GUIDE.md` for usage and verification.

## Product objective

Help customers answer three questions:

1. What happened previously, and what actually fixed it?
2. What has changed in today's logs and metrics?
3. Is a known problem developing again, or is a measurable limit likely to be reached?

Historical similarity is evidence of recurrence risk, not proof of a future failure. An error already occurring is a current incident. Forecasts estimate future measurable values and must expose uncertainty. Gemini/OpenAI explain evidence; they do not invent measurements, probabilities or confirmed root causes.

## Existing foundation and gaps

The project already stores incidents, feedback, resolution timestamps and incident-memory embeddings. Analysis retrieves up to three previous summaries using compatible embedding versions. Current feedback is free text, and its memory update uses an offline hash vector. This is insufficient for reliable semantic matching or structured learning from verified resolutions.

Current telemetry includes CPU, memory and latency, with logs and usage records. Disk capacity, database pool utilization, deployment events and distributed traces need additional collection support. Confirm whether each metric represents the host, container or application; host-wide measurements must not be presented as one application's resource consumption.

Use the current database initially. Introduce a telemetry-source interface so future Loki, Prometheus and Tempo adapters can supply the same analysis inputs. Standard collector integration can be developed separately without blocking historical summaries.

## Customer experience

Add a customer page named **History & insights**, with three tabs:

| Tab | Contents |
|---|---|
| Past failures | Service/environment timeline, severity, symptoms, suggested and customer-confirmed cause, resolution, successful fix and similar cases. |
| Log summaries | Selected-period totals, error patterns, changes from the previous comparable period, affected services and evidence links. |
| Early warnings | Recurrence risk and metric forecasts, evidence, uncertainty, data freshness, expected horizon and recommended checks. |

An insight detail view links to source incidents, log searches and charts. Labels distinguish **AI suggestion**, **Customer confirmed**, **Recurring pattern**, **Forecast** and **Insufficient data**. Risk levels are descriptive; percentage failure probabilities remain unavailable until calibrated against a suitable labeled dataset.

Customers can mark findings useful, incorrect, resolved or prevented. A prevented failure is recorded separately from a false alarm because intervention can change the outcome. Forecast outcomes are evaluated after the horizon ends, with maintenance/intervention noted.

Add optional dashboard widgets for recurring failures, summary counts and forecast trends. Forecast plots show historical measurements, the forecast boundary and a shaded prediction range.

## Phase 1 — trustworthy failure history

Extend incident records with structured, optional customer feedback:

- Failure category, affected component and deployment version when available.
- Confirmed cause, resolution notes and action taken.
- Whether the action worked; who confirmed it and when.
- Links to supporting evidence and resolution timestamps.

Keep AI-generated cause separate from customer-confirmed cause. Preserve existing incidents and free-text feedback through additive migrations. Closing an incident alone must not label its suggested cause as correct.

Normalize and group error patterns after redacting sensitive values. Replace timestamps, request IDs and changing values while retaining meaningful error codes and message structure. Scope patterns to tenant, application, environment and service. Do not mix unrelated environments by default.

Use exact patterns and keyword matching first. Semantic retrieval needs consistent, meaningful embeddings and a versioned reindex job; use fingerprint matching when only offline hash embeddings are available. Never rank incompatible embedding spaces together.

Acceptance: customers can find prior failures, record a verified fix, correct suggestions and retrieve matching evidence; another user without appropriate workspace access cannot read it, and platform admins cannot access customer telemetry through these new routes.

## Phase 2 — historical log digests

Create hourly/daily aggregates and on-demand summaries using actual totals, not just the small log sample sent to the LLM. Include level counts, top patterns, first/last occurrence, affected services and comparable-period changes. Explain missing data or zero-baseline comparisons instead of showing misleading growth percentages.

Store summary window, source watermark, algorithm version, data coverage and bounded evidence references. Gemini can produce an optional plain-language explanation from that structured result. Deterministic summaries remain available without an LLM key.

Incrementally process new records, tolerate bounded late arrivals and deduplicate source events. Use background jobs, bounded batches and cached summaries so history scans do not slow ingestion. A link to an expired source record must show that the evidence has expired.

Acceptance: summary counts match database aggregates, reruns do not duplicate results, and sparse/missing data is clearly labeled.

## Phase 3 — recurrence warnings

Implemented scope: exact normalized fingerprints, at least three errors in two separate minutes within the last hour, an observation within ten minutes, and a confirmed incident resolved before that hour. Error evidence must be explicitly linked by the customer. The independent worker checks every five minutes; owners/operators can also check now. A tenant/service/fingerprint unique key deduplicates repeated checks, preserving customer assessments. Stale observations are labeled without claiming recovery; changing a past confirmation removes it from visible matches. Scans are bounded to 500 recent confirmed incidents and 100 sustained patterns, and expired linked fingerprints are pruned. Detection of pre-failure symptoms, measured resource correlations and calibrated forecasting remain later work.

Compare current patterns with previous confirmed incidents, using log patterns, error frequency, measured trends and deployment context where available. Display matched incident IDs and the specific similarities and differences.

Example: “Database timeout messages and rising latency resemble incident #42. That incident was confirmed as connection pool exhaustion. Current pool usage is unavailable; check it before accepting this explanation.”

Require repeated supporting observations before a warning. Use cooldowns and deduplication. Distinguish the return of an existing fault from a warning based on symptoms that historically preceded a fault. Initial matching should be transparent rules, not a trained failure classifier.

Acceptance: known recurring patterns are identified, unrelated errors are excluded, and similarity is never presented as a guaranteed future crash.

## Phase 4 — measurable forecasts

Start with subscription ingestion usage, then sustained memory growth and latency threshold crossing after verifying metric semantics. Add disk-full forecasting only after available bytes and capacity are collected. CPU seasonality, traffic forecasting and generalized failure prediction are later experiments requiring more representative history.

Each forecast records the entity, metric/unit, data window, cutoff time, forecast horizon, predicted range, threshold, method/version and data quality. Begin with simple statistical baselines; compare them with more complex methods before adoption.

Gate each target on sampling coverage, freshness, enough independent observations and stability. Detect resets, deployments and regime changes; invalidate stale forecasts. Trial plans with short retention may receive “insufficient history.” Do not manufacture observations or apply one universal minimum-history rule to every target.

Backtest using only information available at the forecast's creation time. Include healthy periods and failed cases. Evaluate error by horizon, prediction-range coverage, false warnings, missed events and useful lead time. Retrospective summaries are not forecast features unless they were already available at that time.

Acceptance: compare against a simple baseline, publish measured pilot results and agree operational false-alert limits before enabling automatic forecast alerts. No promised accuracy or calibrated failure probability at this stage.

## Phase 5 — controlled notifications

Extend the existing notification UI with independently selectable event types: current incidents, recurrence warnings, forecast warnings and daily digests. Existing incident notification preferences retain their behavior; new types start disabled.

Customers choose services, warning thresholds, digest timezone/schedule and cooldown. Saving settings sends no test message. Generate predictive alerts only after validation and sustained evidence; include expiry and an evidence link. Resolve/update warnings when the trend recovers or data becomes stale.

Support prediction/summary references in the outbox rather than creating fake incidents for every forecast. Track delivery per destination to avoid resending successful destinations when another fails. Retain background retries and distinguish queued, delivered, skipped and failed outcomes.

Acceptance: opt-out works, schedules use the selected timezone, duplicates are suppressed, expired warnings are skipped and messages contain only the intended customer's evidence.

## Data and platform changes

| Record | Purpose |
|---|---|
| Structured incident resolution | Customer-confirmed cause/fix and verification audit. |
| Log pattern and time bucket | Redacted fingerprint, counts, service identity and coverage. |
| Historical summary | Window, factual digest, optional AI explanation and source references. |
| Insight/forecast | Evidence, method/version, cutoff, horizon, predicted range, expiry and state. |
| Insight outcome | Subsequent measurement, user feedback and intervention context. |
| Notification event/destination state | Correct reference type, deduplication, retry and delivery status. |

All customer records and source queries are tenant-scoped. Give indexes to tenant/service/time queries. Add bounded customer APIs for history, summaries, insights and feedback; admin APIs expose configuration and aggregate cost only. Add a separate background insights worker with concurrency control and idempotent job identifiers.

Plan limits can bound enabled services, summary frequency, forecast targets and AI explanations. Use immutable subscription versions for future commercial changes; do not silently change current customers' limits.

## Privacy, retention and cost

Redact credentials and sensitive identifiers before persistence in summaries, embeddings or LLM requests. Treat log content as untrusted input, never instructions to the model. Retrieve only bounded relevant evidence and record model usage without logging private prompts.

Derived summaries and embeddings are customer data. Apply documented retention and deletion to raw data, summaries, forecasts and embeddings, including account deletion and backup policy. Do not retain sensitive summaries longer simply because raw logs have expired. Configure aggregation retention explicitly within purchased limits.

Cache unchanged explanations and meter their costs separately. Deterministic summaries and forecasts must continue when AI is disabled, unavailable or over budget. Investigations remain read-only; container restart and automatic recovery stay deferred.

## Phase 6 — AsterCart validation and pilot

Use clearly labeled synthetic replay data with timestamps, expected causes and failure onset times. Cover:

1. Repeated database timeout with a previously confirmed fix.
2. Different error text with no relevant history.
3. Gradual memory growth before a threshold crossing.
4. Brief CPU spike that should not produce a future-failure warning.
5. Missing/out-of-order telemetry and a deployment that changes the baseline.
6. Growing ingestion usage, renewal/reset and an intervention that reduces growth.
7. Disabled notifications, partial destination failure and another customer's matching service name.

Use simulated historical data to test mechanics, then evaluate real pilot data separately. Start predictive alerts in preview mode with no external messages. Record measured accuracy and false-alert results before enabling customer opt-in delivery.

## Recommended release order

Release A: structured failure history and factual log summaries.

Release B: transparent recurrence matching and evidence views.

Release C: validated usage/resource forecasts and dashboard widgets.

Release D: customer-enabled predictive alerts and scheduled digests.

The first implementation milestone is Phase 1 plus the deterministic part of Phase 2. It is useful immediately and creates better historical evidence for later prediction work.

## Research basis

- [Prometheus forecast function](https://prometheus.io/docs/prometheus/latest/querying/functions/#predict_linear): simple linear forecasting of gauge time series is an available baseline; it is not a general failure predictor.
- [Time series cross-validation](https://otexts.com/fpp3/tscv.html): evaluate forecasts using prior observations only, at rolling cutoff times.
- [Prediction intervals](https://otexts.com/fpp3/prediction-intervals.html): show forecast uncertainty rather than a single exact future value.
