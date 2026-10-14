# Independent LogIQ agent

Build: `docker build -t logiq-agent:1.1.0 .`

Set LOGIQ_URL and LOGIQ_INGEST_KEY in your deployment environment. Mount structured JSONL logs read-only at /source and a persistent writable volume at /data. Run the image with `--log-file /source/app.jsonl --service api`; optionally supply `--metrics-file /source/metrics.jsonl`.

Remote endpoints require HTTPS. For Docker Desktop local testing, host.docker.internal is accepted only with LOGIQ_ALLOW_LOCAL_HTTP=true. Do not enable this setting on remote deployments.

Log records contain service, level, message, timestamp and optional attrs. Metrics contain service, cpu (%), memory (%), latency (ms) and timestamp. Business applications can use their existing log-file output; they do not import this collector.

The durable queue buffers 100,000 events or 256 MiB by default. Persistent file cursors, retries and event IDs protect against collector restarts and duplicate delivery. A source-file rotation must retain unread files until collection catches up; this version does not scan rotated archives. Rejected records are preserved for inspection. Quota and authentication failures retain queued data for operator action.
