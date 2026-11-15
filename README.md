# RidePulse

Distributed bike-share trip and station analytics for CSCI 5253, by Harshitha
Ravishankar and Shivani Dilipkumar Patel.

The implemented ingestion layer collects NYC Citi Bike and Boulder BCycle GBFS
snapshots, archives raw inputs in Google Cloud Storage, and ingests trip batches
and station snapshots through a private Cloud Run worker into PostgreSQL.
FastAPI analytics, versioned Redis caching, a Streamlit dashboard and an isolated
PostgreSQL/Redis integration suite are now
implemented; see [API.md](API.md) for endpoints, date/freshness rules and commands.
The API, dashboard, Redis cache and collectors are deployed privately in the
project. Live authenticated Pub/Sub recovery, duplicate delivery and dead-letter
forwarding have been verified. Owner migration `010` is applied and serving
aggregates reconcile with all trip facts. See [REPORT.md](REPORT.md),
[RUNBOOK.md](RUNBOOK.md), [DEMO.md](DEMO.md) and [EXPERIMENTS.md](EXPERIMENTS.md)
for evidence, operations and evaluation.

## Verified data

The original 3,860,371 April 2026 NYC trips are preserved. A bounded seven-day
availability replay committed all 4,067 archived objects with zero rejections;
raw inputs and generation-pinned replay manifests remain in Storage. Boulder
supplies availability observations, not imported historical trips.

## Measured results

The current local suite passes 73 correctness tests plus a real emulator push/replay smoke check. GitHub Actions runs the correctness suite and a separate queue smoke job. Across 12 cached API trials, 17,342 requests
completed with zero errors/mismatches; at 20 users median trial p95 was 220 ms.
Repeated 160,000-row worker trials achieved 1.56x speedup at eight observed
workers; a separate 800,000-row pilot slowed down as workers increased. These are
bounded measurements on the existing shared-core SQL tier, not general capacity
claims. REPORT.md includes raw-evidence references, charts and limitations.

The final selected shutdown is verified: Cloud SQL stopped (`NEVER`), disposable
Redis deleted, private Cloud Run deployments retained with minimum instances zero.
Raw archival collection stays scheduled. Use RUNBOOK.md before opening a cloud demo.

## Local development

Use Python 3.12 to match the Docker images, Docker Compose, and PostgreSQL tools.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-local.txt
cp .env.example .env
# Set your own POSTGRES_PASSWORD in .env before starting PostgreSQL.
docker compose up -d postgres
```

For a NEW local database, apply sql/001_create_tables.sql with psql ON_ERROR_STOP
enabled. Do not run legacy migrations against a new or already migrated database.
Local PostgreSQL listens on localhost:5433, database and user ridepulse.
Raw historical data is not in Git; obtain it from the project bucket with the
appropriate GCP permissions or download the published source dataset.

```sh
python -m unittest discover -s tests -v
```

Collector deployment additionally requires requirements-collector.txt. Cloud Run
configuration is shared separately in the team handoff.
Compose includes PostgreSQL, Redis, FastAPI, Streamlit and an isolated test
database. See API.md for the full local stack and integration test commands.
Cloud Pub/Sub reliability is checked with a separate controlled verification
harness; local tests use mocked Storage and real isolated PostgreSQL/Redis.

## Code map

- src/worker.py: private Pub/Sub-compatible trip ingestion endpoint.
- src/gbfs_ingest.py: GBFS information/status processing.
- scripts/collect_gbfs_cloud.py: cloud collector and optional task publishing.
- scripts/collect_gbfs.py: local collector.
- scripts/ingest_all_trips.py: local historical ingestion.
- scripts/create_citibike_batches.py: split historical CSV files into 50,000 rows.
- sql/: canonical schema, guarded legacy migrations, analysis and aggregate SQL.
- tests/test_worker.py: ingestion validation and failure-behavior tests.
- src/api.py and src/analytics.py: read-only REST analytics over canonical facts.
- src/query_cache.py: versioned Redis queries with SQL fallback.
- src/dashboard.py: Streamlit/Plotly API client.
- sql/008_dataset_versions.sql: durable versions advanced by transactional triggers.
- tests/test_api_integration.py and tests/test_worker_integration.py: real SQL
  query and transaction correctness tests, including concurrent duplicate delivery.

The selected between-session policy stops Cloud SQL, removes disposable Redis
and pauses automatic ingestion using `scripts/session.py stop --delete-cache`.
Do not enable delivery against a stopped database. GitHub never contains .env,
passwords, service-account keys, downloaded datasets, or database dumps.

## Demo presentation update

The dashboard now uses full-width categorical station bars, friendly time axes
and compact name-first tables. Show station IDs and details exposes identifiers
and timestamps when needed. Viewer mode hides developer controls. Low-availability
results include metadata-backed station names with cache invalidation after renames.
The updated local suite passes 67 tests; the retained performance evaluation describes
the earlier measured runtime. DEMO.md includes the pre-demo cache/freshness check.

## Local queue pipeline (no cloud credentials)

The optional queue profile runs Google's real Pub/Sub emulator and the normal worker against the disposable `queue_fixture` schema. A read-only fixture directory substitutes for GCS; this is deliberately not an emulation of Storage generations or IAM. It never writes production tables.

```sh
pip install -r requirements-test.txt
docker compose --profile queue up -d --build --wait test-postgres pubsub local-worker
python scripts/verify_local_queue.py
# Stop local queue resources after use:
docker compose --profile queue stop local-worker pubsub test-postgres
```

`POSTGRES_PASSWORD` must still be set in `.env` for Compose interpolation, even when the primary local PostgreSQL service is not selected. Local emulator ports bind loopback only. The verification publishes twice through an actual push subscription and checks exact inserted rows and unchanged cache versions after duplicate delivery.

## Submission

Team: **Harshitha Ravishankar and Shivani Dilipkumar Patel**. The owner should verify access for `elgoodm`, `rezwan09` and `VedMulik` in repository settings. Final report: [REPORT.md](REPORT.md); recording sequence: [PRESENTATION.md](PRESENTATION.md); proposal reconciliation and limitations are explicit in the report. The cloud stack is intentionally paused between work sessions; follow [RUNBOOK.md](RUNBOOK.md) to start it for a demo.

The upload-ready PDF and editable presentation are in [submission/](submission/README.md). The presentation includes a live-demo segment; the team must record/upload the video and verify its accessibility.
