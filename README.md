# RidePulse

Distributed bike-share trip and station analytics for CSCI 5253, by Harshitha
Ravishankar and Shivani Dilipkumar Patel.

The implemented ingestion layer collects NYC Citi Bike and Boulder BCycle GBFS
snapshots, archives raw inputs in Google Cloud Storage, and ingests trip batches
and station snapshots through a private Cloud Run worker into PostgreSQL.
FastAPI, Redis caching, the dashboard, and performance experiments remain to be
implemented against the project proposal.

## Verified data

As of November 4, 2026: 3,860,371 April 2026 Citi Bike trips imported into Cloud
SQL from 80 batches, zero rejected rows; duplicate delivery verified; 720 hourly
aggregate rows and 640,056 route aggregate rows. Full results are in
[verification results](docs/ingestion-verification.json).

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
configuration is described in [deployment notes](docs/ingestion-deployment.md).
The current Compose stack contains PostgreSQL only; Redis, API, dashboard, and
Pub/Sub emulator integration remain pending.

## Code map

- src/worker.py: private Pub/Sub-compatible trip ingestion endpoint.
- src/gbfs_ingest.py: GBFS information/status processing.
- scripts/collect_gbfs_cloud.py: cloud collector and optional task publishing.
- scripts/collect_gbfs.py: local collector.
- scripts/ingest_all_trips.py: local historical ingestion.
- scripts/create_citibike_batches.py: split historical CSV files into 50,000 rows.
- sql/: canonical schema, guarded legacy migrations, analysis and aggregate SQL.
- tests/test_worker.py: ingestion validation and failure-behavior tests.
- docs/cloud-deployment-status.md: verified deployment and operating state.
- docs/shivani-handoff.md: resource inventory and ingestion contract.

Cloud SQL is stopped between work sessions, and automatic ingestion is paused.
Do not enable delivery against a stopped database. GitHub never contains .env,
passwords, service-account keys, downloaded datasets, or database dumps.
