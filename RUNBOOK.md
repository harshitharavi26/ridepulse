# RidePulse operations

Use the university identity `shpa6505@colorado.edu`, project `ridepulse-dcs-2026`,
region `us-central1`. All scripts in `scripts/cloud_support.py` pass these values
explicitly, including the quota project, so the unrelated Lab 5 default is not
used. Never print or commit secret values.

## Services and data

| Resource | Purpose |
| --- | --- |
| `ridepulse-api` | Private FastAPI service; one SQL connection per instance, up to ten instances |
| `ridepulse-dashboard` | Private Streamlit service; calls only the API using its service identity |
| `ridepulse-worker` | Private, authenticated Pub/Sub handler; concurrency 1, maximum eight instances |
| `ridepulse-status-collector` | Scheduled status archiving and optional task publication |
| `ridepulse-metadata-collector` | Scheduled metadata archiving and optional task publication |
| `ridepulse-postgres` | Durable facts, serving aggregates and transactional versions |
| `ridepulse-cache` | Disposable 1-GiB Basic Memorystore; private VPC address |
| `ridepulse-tasks` / `ridepulse-worker-push` | Production task distribution |
| `ridepulse-dead-letter` / `ridepulse-dead-letter-dev` | Undeliverable-task inspection |
| `gs://ridepulse-dcs-2026-data-42502534899` | Immutable input archives, manifests and source build packages |

The existing worker identity is reused for the new services because this account
has scoped act-as access to existing identities and cannot create new service
accounts. The dashboard receives no SQL password. A future hardening step can
split API and dashboard identities with owner-provisioned accounts and narrow
grants. All three services remain authenticated; no `allUsers` access was added.

## Start a work session

Authenticate with the university account, then start the Cloud SQL Auth Proxy on
loopback port 5434. The reviewed local binary is in ignored `work/`; installation
instructions for a fresh laptop are in the official Cloud SQL Auth Proxy docs.

```sh
gcloud auth login shpa6505@colorado.edu
CLOUDSDK_CORE_ACCOUNT=shpa6505@colorado.edu work/cloud-sql-proxy \
  --gcloud-auth --quota-project=ridepulse-dcs-2026 --port=5434 \
  ridepulse-dcs-2026:us-central1:ridepulse-postgres
```

In another terminal, from the repository:

```sh
.venv/bin/python scripts/session.py start
```

This starts SQL, creates or waits for Redis, updates its potentially changed
private address, restores authenticated push, enables producer publication and
requests fresh collector executions. It never reimports historical trips. SQL
and Redis provisioning can take several minutes. Inspect job execution and
database timestamps, rather than assuming Scheduler trigger time equals capture
time. The five-minute scheduler uses UTC; collector startup has measured delays.

The helper requires the loopback SQL proxy before it starts paid resources. It
checks the version/aggregate schema before enabling their features. For a first
deployment, apply migrations and initialize aggregates before relying on caching.

## Private dashboard access

```sh
gcloud run services proxy ridepulse-dashboard --port=8502 --region=us-central1 \
  --account=shpa6505@colorado.edu --project=ridepulse-dcs-2026 \
  --billing-project=ridepulse-dcs-2026
```

Open http://localhost:8502. The first invocation may install Google's official
Cloud Run proxy component. In a restricted environment, the official component
binary can be downloaded to ignored `work/` and checked against Google's SDK
component manifest; that path was used during verification. The dashboard obtains
short-lived service identity tokens for the private API. Do not paste user tokens
into source or browser URLs.

Cloud Run services have minimum instances zero. An open Streamlit websocket can
keep a request/instance active, so close dashboard tabs and local proxies after
the demo. Minimum instances zero does not make SQL or Redis stop automatically.

## Pause between work sessions

Shivani selected the cost-saving policy: pause SQL and remove the disposable
Redis cache after final experiments/demo checks; retain Cloud Run deployments.

```sh
.venv/bin/python scripts/session.py stop --delete-cache
.venv/bin/python scripts/session.py status
```

The expected result is a completed SQL stop operation, activation policy `NEVER`,
and Redis absent. The Admin API can still report SQL state `RUNNABLE` when the
owner has stopped it; the helper verifies operation completion and policy.
The helper disables producer publication, detaches push, allows a drain interval,
then stops SQL and removes Redis. Raw archive collection remains scheduled.
Durable Storage inputs and SQL facts are retained. No queue is purged. Unprocessed
messages have finite retention, so reconcile archived objects after a long pause.
When resuming, cache addresses can change; the start helper refreshes the API URL.

At reviewed Iowa list prices, SQL `db-g1-small` compute is $0.035/hour and Basic
1-GiB Redis is $0.049/hour, approximately $2.016/day combined while provisioned.
This excludes SQL storage/backups, Storage, Run, networking and other usage; it
is not an invoice or a spending cap. Retained storage and collector jobs can
still incur charges while SQL is paused. Credits do not switch accounts
automatically. [Cloud SQL pricing](https://cloud.google.com/sql/pricing),
[Memorystore pricing](https://cloud.google.com/memorystore/docs/redis/pricing).

## Schema and aggregate changes

`sql/001_create_tables.sql` initializes new databases. Never rerun legacy
migrations against a migrated database. `008_dataset_versions.sql` installs
transactional versions, and `010_serving_aggregates.sql` creates serving state and
station/hour counts. These need an object owner/migration identity, not just
Cloud IAM access. Keep the application role out of the migration role.

Once the owner setup is present:

```sh
caffeinate -i .venv/bin/python scripts/finish_cloud_setup.py --run-experiments
```

This refreshes aggregates atomically when their watermark is stale, enables versioned Redis queries and
transactional worker maintenance, then runs the controlled experiments. If
setup is missing it exits before changing cloud services. Use `--refresh-aggregates` to force a rebuild. Full refreshes hold
the trip-version row lock; schedule them outside measured trials.

Historical inserts maintain aggregates only when their prior watermark matches
the durable trip version. External corrections invalidate them. Queries fall
back to facts until an atomic refresh catches up; stale aggregates are not served
as current. Full refresh zeroes obsolete legacy keys because the application
role intentionally lacks DELETE on those legacy tables; serving excludes zero
counts. New station/hour aggregates can be rebuilt with their explicit grants.

## Reconcile archived snapshots

```sh
.venv/bin/python scripts/replay_gbfs.py --date=YYYY-MM-DD --city=both \
  --feed=station_information --max-objects=2 \
  --manifest=work/metadata-replay.json --execute --upload-manifest
```

Use a separate manifest for station status, and larger bounded windows when
backfilling observation history. `--date` is a UTC archive date. Plans select the
latest matching objects and pin Storage generations. Successful/rejected tasks
are checkpointed; retryable failures stop for safe resume with the same manifest.
Raw archives are never rewritten. Immutable uploads use a generation precondition.

Do not treat GBFS observations as historical trips. Snapshot replay may add old
observations but cannot make an old upstream reading current. Freshness uses both
collection time and known upstream report time. Missing timestamps and sample
gaps remain explicit. Metadata for disappeared stations is retained.

## Monitoring and failure investigation

The project Monitoring dashboard is **RidePulse pipeline and serving**. Its
definition is tracked under `infra/monitoring/`. Standard metrics cover Run p95
latency/instances, queue age/backlog, SQL CPU/connections and Redis hits/misses.
API and worker logs are JSON with request/task IDs, outcomes, row counts and
timings. `sql_completed` records SQL duration without logging query parameters.

`scripts/setup_monitoring.py` also defines enhanced log-based counters and
distributions. Their creation currently requires Logs Configuration Writer access
that this university identity does not have; standard metrics and application
logs remain available. Do not infer a successful insert from HTTP 200 alone:
permanent data rejections are acknowledged with `status=rejected`.

The existing dead-letter inspection subscription includes controlled verification
messages marked with `verification_id`. The verification harness uses real old
metadata, deliberately fails a separate worker's database configuration, observes
same-message recovery and forwarding, and restores production subscription
settings. It never injects synthetic public trips. Do not purge inspection/dev
queues blindly. Only temporary test resources created by the harness are removed.

## Monitoring and selected budget exception

`python scripts/setup_monitoring.py` idempotently upserts seven log metrics, the university email channel, two alert policies and the existing dashboard. Requires Logs Configuration Writer and Monitoring permissions. Definitions are in `infra/monitoring/`; deployment receipts are retained separately. Series are prospective; no data while paused is not proof of a cache fault. API per-endpoint p95 >1,000 ms and worker retryable error ratio >5% must persist for five minutes before alerting. The cache ratio denominator is logged cache hit/result attempts, excluding disabled/unversioned requests.

Shivani chose to skip the proposal's budget alert. The API is enabled and `infra/monitoring/budget.json` contains the reviewed USD 10 monthly project-only notification plan, but no budget was created. `scripts/setup_budget.py` is an optional owner-side setup tool; budget notifications do not cap spending. Do not rerun it as part of ordinary start/stop.

## Recording preflight

`python scripts/verify_serving.py --require-fresh` now requires both fresh city observations and an available Redis cache with actual repeated historical-query hits. It warms pickup/dropoff rankings, the normal and hidden-round-trip route variants, and hourly/daily patterns. Shortage dates are discovered per city from the coverage API instead of a hard-coded November date. `--require-cache` checks warm caching while allowing archived/stale station readings; the default command keeps SQL-fallback verification possible without requiring Redis. All variants reconcile exact public facts/aggregates and never start resources themselves.
