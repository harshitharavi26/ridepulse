# RidePulse

Distributed bike-share trip and station analytics for CSCI 5253, by Harshitha
Ravishankar and Shivani Dilipkumar Patel.

The implemented ingestion layer collects NYC Citi Bike and Boulder BCycle GBFS
snapshots, archives raw inputs in Google Cloud Storage, and ingests trip batches
and station snapshots through a private Cloud Run worker into PostgreSQL.
FastAPI analytics, versioned Redis caching, a Streamlit dashboard and an isolated
PostgreSQL/Redis integration suite are now
implemented; see [API.md](API.md) for endpoints, date/freshness rules and commands.
The API, dashboard, worker and collector jobs are deployed in the project.
The three web services require authenticated access; Redis is recreated for active sessions. Live authenticated Pub/Sub recovery, duplicate delivery and dead-letter
forwarding have been verified. Owner migration `010` is applied and serving
aggregates reconcile with all trip facts. See [RUNBOOK.md](RUNBOOK.md) for
operations and [EXPERIMENTS.md](EXPERIMENTS.md) for reproducible evaluation.

## Verified data

The original 3,860,371 April 2026 NYC trips are preserved. A bounded seven-day
availability replay committed all 4,067 archived objects with zero rejections;
raw inputs and generation-pinned replay manifests remain in Storage. Boulder
supplies availability observations, not imported historical trips.

## Measured results

The current local suite passes **77 correctness tests**, including four recording-
preflight regressions, with real PostgreSQL and Redis. The separate real Pub/Sub
emulator push/replay check also passed. [GitHub Actions](https://github.com/harshitharavi26/ridepulse/actions/workflows/tests.yml)
runs both checks on each push. Across 12 cached API trials, 17,342 requests
completed with zero errors/mismatches; at 20 users median trial p95 was 220 ms.
Repeated 160,000-row worker trials achieved 1.56x speedup at eight observed
workers; a separate 800,000-row pilot slowed down as workers increased. These are
bounded measurements on the existing shared-core SQL tier, not general capacity
claims. [EXPERIMENTS.md](EXPERIMENTS.md) describes the method and limitations;
[experiments/results/](experiments/results/) retains raw evidence and charts.

The last verified shutdown state (November 15, 2026) was: Cloud SQL stopped (`NEVER`), disposable
Redis deleted, private Cloud Run deployments retained with minimum instances zero.
Raw archival collection stays scheduled. Use RUNBOOK.md before opening a cloud demo.

## Local development

Use Python 3.12 to match the Docker images, Docker Compose, and PostgreSQL tools.
Run these commands from the repository root:

```sh
git clone https://github.com/harshitharavi26/ridepulse.git
cd ridepulse
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
cp .env.example .env
# Set your own POSTGRES_PASSWORD in .env, then start the application:
docker compose up -d --build --wait postgres redis api dashboard
```

Open the dashboard at [localhost:8501](http://localhost:8501), API documentation at
[localhost:8080/docs](http://localhost:8080/docs), and liveness at
[localhost:8080/health/live](http://localhost:8080/health/live). PostgreSQL listens
on loopback port 5433; Redis on 56379. Compose applies SQL `001`, `008` and `010`
on the **first initialization of a new PostgreSQL volume**. Existing volumes are
not automatically migrated; an object owner must apply the missing migrations
with `psql -v ON_ERROR_STOP=1`. Do not run the guarded legacy migrations blindly.

Raw data is not in Git. An empty local database shows an explicit empty-history
message; it does not download the cloud dataset automatically. Use the published
Citi Bike source files with `scripts/ingest_trips.py`, or authorized project
Storage inputs, to populate local history. [API.md](API.md) explains local data
configuration and endpoint semantics.

Run the full correctness suite against a separate disposable database:

```sh
docker compose --profile test up -d --wait test-postgres redis
TEST_DATABASE_URL=postgresql://ridepulse_test:local_test_only@localhost:55433/ridepulse_test \
  TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m unittest discover -s tests -v
```

Integration fixtures only use the guarded local `ridepulse_test` database and
Redis database 15. Unit tests mock Storage; the optional queue profile below uses
an actual Pub/Sub emulator. The cloud reliability harness separately verifies
production-style authentication, redelivery and dead lettering.

## Deploy to Google Cloud Run

These commands build and deploy the service into the existing team project;
they do not require a separate handoff file. They can incur Cloud Build, SQL,
Redis and request charges. Run them from the repository root in Bash or Zsh.
For a different project, provision the prerequisites below and adapt the project
constants in `scripts/cloud_support.py` before using the session helpers.

### Infrastructure and permissions

The team project already has the following prerequisites. A deployment into a
new project needs equivalent resources first; this repository is not a complete
Terraform infrastructure provisioner.

| Resource | Required configuration |
| --- | --- |
| Enabled APIs | Cloud Run, Cloud Build, Artifact Registry, Storage, Cloud SQL Admin, Memorystore Redis, Pub/Sub, Scheduler, Secret Manager, Logging and Monitoring |
| Container registry | Docker repository `ridepulse-containers` in `us-central1` |
| Raw/build storage | Bucket `ridepulse-dcs-2026-data-42502534899`; source uploads under `build-source/` |
| PostgreSQL | Instance `ridepulse-postgres`, database `ridepulse`, login `ridepulse_app`; owner applies `sql/001_create_tables.sql`, `sql/008_dataset_versions.sql`, then `sql/010_serving_aggregates.sql` using `ON_ERROR_STOP`. Refresh existing trip aggregates with `scripts/refresh_aggregates.py` after those migrations. |
| Database secret | Secret Manager secret `ridepulse-db-password` whose latest value is the app login's password; never put the value in Git or README commands |
| Network/cache | Regional `default` subnet/network and private Redis instance `ridepulse-cache`, Basic 1 GiB; session start creates it if absent. API uses Direct VPC egress for private ranges. |
| Queue | Topic `ridepulse-tasks`, subscription `ridepulse-worker-push`, dead-letter topic `ridepulse-dead-letter` and inspection subscription `ridepulse-dead-letter-dev`; retain the configured retry/dead-letter policy |
| Runtime identities | `ridepulse-worker@ridepulse-dcs-2026.iam.gserviceaccount.com` for the three services; SQL Client, secret access, and raw-object read permissions; it also needs Run Invoker on the private API for dashboard calls |
| Producer/push identities | Existing collector identity needs object-create and Pub/Sub Publisher access. Push identity `ridepulse-pubsub-push@ridepulse-dcs-2026.iam.gserviceaccount.com` needs Run Invoker on the worker; the Pub/Sub service agent needs token-creation rights and the dead-letter forwarding permissions. |
| Scheduled jobs | `ridepulse-status-collector` every five minutes, metadata collector daily; both write archives while task publication can be paused. Scheduler identity needs permission to execute the jobs. |

The deploying user needs Cloud Run deployment and runtime service-account act-as
permissions, Cloud Build submission/source-upload access, and the necessary
resource-management permissions for session start/stop. The build identity needs
Artifact Registry write access. Cloud IAM access does not grant PostgreSQL DDL
ownership. The separate benchmark schema is only needed for experiments, not to
serve the application. [RUNBOOK.md](RUNBOOK.md) covers migration ownership and
session troubleshooting.

### Authenticate and start the existing backend

```sh
gcloud auth login shpa6505@colorado.edu

# These explicit flags leave unrelated gcloud defaults unchanged.
rp_gcloud() {
  gcloud --account=shpa6505@colorado.edu \
    --project=ridepulse-dcs-2026 --billing-project=ridepulse-dcs-2026 "$@"
}
RP_PROJECT=ridepulse-dcs-2026
RP_REGION=us-central1
RP_BUCKET=ridepulse-dcs-2026-data-42502534899
RP_RUNTIME_SA="ridepulse-worker@${RP_PROJECT}.iam.gserviceaccount.com"
RP_SQL="${RP_PROJECT}:${RP_REGION}:ridepulse-postgres"
RP_IMAGES="${RP_REGION}-docker.pkg.dev/${RP_PROJECT}/ridepulse-containers"
# Choose a new tag for each build; use the same tag in all commands below.
RP_TAG="submission-$(date -u +%Y%m%dT%H%M%SZ)"
```

Install the official [Cloud SQL Auth Proxy](https://docs.cloud.google.com/sql/docs/postgres/connect-auth-proxy)
binary as `cloud-sql-proxy` on PATH. In a **second terminal**, keep it running:

```sh
CLOUDSDK_CORE_ACCOUNT=shpa6505@colorado.edu cloud-sql-proxy \
  --gcloud-auth --quota-project=ridepulse-dcs-2026 --port=5434 \
  ridepulse-dcs-2026:us-central1:ridepulse-postgres
```

Back in the first terminal, resume the already-provisioned application:

```sh
.venv/bin/python scripts/session.py start
RP_REDIS_HOST=$(rp_gcloud redis instances describe ridepulse-cache \
  --region="$RP_REGION" --format='value(host)')
```

The helper starts SQL, waits for Redis, configures its address on the API,
restores authenticated push, enables collector publication and runs collectors.
It expects the existing services/jobs/queue listed above; for an initial project
bootstrap, create SQL/Redis and those queue/job resources before enabling delivery.
It never reimports the historical trips. Provisioning can take several minutes.

### Build and deploy containers

```sh
rp_gcloud builds submit . --config=infra/cloudbuild.yaml \
  --substitutions="_TAG=$RP_TAG" \
  --gcs-source-staging-dir="gs://${RP_BUCKET}/build-source"

rp_gcloud run deploy ridepulse-worker \
  --image="$RP_IMAGES/ridepulse-worker:$RP_TAG" --region="$RP_REGION" \
  --service-account="$RP_RUNTIME_SA" --set-cloudsql-instances="$RP_SQL" \
  --set-secrets=DB_PASSWORD=ridepulse-db-password:latest \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$RP_PROJECT,RIDEPULSE_BUCKET=$RP_BUCKET,INSTANCE_CONNECTION_NAME=$RP_SQL,DB_NAME=ridepulse,DB_USER=ridepulse_app,RIDEPULSE_MAINTAIN_AGGREGATES=true" \
  --cpu=1 --memory=512Mi --concurrency=1 --timeout=300 \
  --min=0 --min-instances=0 --max-instances=8 --no-allow-unauthenticated

rp_gcloud run deploy ridepulse-api \
  --image="$RP_IMAGES/ridepulse-api:$RP_TAG" --region="$RP_REGION" \
  --service-account="$RP_RUNTIME_SA" --set-cloudsql-instances="$RP_SQL" \
  --set-secrets=DB_PASSWORD=ridepulse-db-password:latest \
  --set-env-vars="INSTANCE_CONNECTION_NAME=$RP_SQL,DB_NAME=ridepulse,DB_USER=ridepulse_app,DB_POOL_SIZE=1,DB_STATEMENT_TIMEOUT_MS=20000,REDIS_URL=redis://${RP_REDIS_HOST}:6379/0" \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --execution-environment=gen2 --cpu=1 --memory=512Mi --concurrency=8 \
  --timeout=60 --min=0 --min-instances=0 --max-instances=10 \
  --no-allow-unauthenticated

RP_API_URL=$(rp_gcloud run services describe ridepulse-api \
  --region="$RP_REGION" --format='value(status.url)')
rp_gcloud run deploy ridepulse-dashboard \
  --image="$RP_IMAGES/ridepulse-dashboard:$RP_TAG" --region="$RP_REGION" \
  --service-account="$RP_RUNTIME_SA" \
  --set-env-vars="API_BASE_URL=$RP_API_URL,API_AUTH_AUDIENCE=$RP_API_URL" \
  --cpu=1 --memory=512Mi --concurrency=10 --timeout=300 \
  --min=0 --min-instances=0 --max-instances=2 --no-allow-unauthenticated

# Refresh collector code while preserving each existing identity, feed type,
# schedule and publication setting. These commands do not execute the jobs.
for RP_JOB in ridepulse-status-collector ridepulse-metadata-collector; do
  rp_gcloud run jobs update "$RP_JOB" --region="$RP_REGION" \
    --image="$RP_IMAGES/ridepulse-collector:$RP_TAG"
done
```

Only enable incremental aggregate maintenance when the aggregate watermark is
aligned. The existing team setup is aligned; a new database with imported facts
must refresh first. Keep the dashboard's API audience identical to the private
API URL. The dashboard receives no SQL password. Build context ignores local
secrets, raw datasets and work files.

### Verify, open and pause

Wait for the collectors requested by session start to finish, then:

```sh
.venv/bin/python scripts/verify_serving.py --require-fresh
rp_gcloud run services proxy ridepulse-dashboard --region="$RP_REGION" --port=8502
```

Open [localhost:8502](http://localhost:8502). Verification checks real SQL-backed
answers, fresh observations and cache behavior; it also warms standard historical
queries. A deployment health response alone does not prove ingestion freshness.
For a newly provisioned monitoring setup, run
`.venv/bin/python scripts/setup_monitoring.py` with Logs Configuration Writer and
Monitoring permissions; this upserts the log metrics, alerts and dashboard.

After closing the dashboard/proxy, pause the paid backend:

```sh
.venv/bin/python scripts/session.py stop --delete-cache
.venv/bin/python scripts/session.py status
```

Expected: SQL activation policy `NEVER` and Redis absent. The helper disables
publication and detaches push before stopping SQL; it preserves facts and raw
objects. Cloud Run minimum instances zero does not stop SQL or Redis. Raw archive
collection and retained storage can still incur charges. The budget alert was
explicitly skipped; it is not an active spending cap.

Command references: [Cloud Build submit](https://docs.cloud.google.com/sdk/gcloud/reference/builds/submit),
[Cloud Run deploy](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy),
[Cloud Run jobs](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/update).

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

## Dashboard features

The dashboard now uses full-width categorical station bars, friendly time axes
and compact name-first tables. Show station IDs and details exposes identifiers
and timestamps when needed. Viewer mode hides developer controls. Low-availability
results include metadata-backed station names with cache invalidation after renames.
The retained performance evaluation describes the earlier measured runtime; UI
changes are not new load measurements. Before recording, start the full session
and run `python scripts/verify_serving.py --require-fresh`. This requires fresh
readings for both cities and proves repeated Redis hits for pickup/dropoff rankings
and both route variants. Shortage dates come from each city's latest stored coverage.
Use `--require-cache` for an explicitly historical walkthrough that permits stale
station readings. These checks do not start resources.

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

## Team and grading access

Team: **Harshitha Ravishankar and Shivani Dilipkumar Patel**. Required grader
accounts: `elgoodm`, `rezwan09` and `VedMulik`. The code repository is public.
The written report and presentation/video are submitted separately through the
course submission process. This repository contains the implementation, deployment
instructions, tests and reproducible experiment evidence.
