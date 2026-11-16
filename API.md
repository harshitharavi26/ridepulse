# RidePulse analytics API

## Run locally

Use Python 3.12 and Docker. Copy `.env.example` to `.env` if it does not exist, and
set a unique local `POSTGRES_PASSWORD`. Never use the cloud password locally.

```sh
docker compose up -d --build dashboard
```

Open http://localhost:8080/docs for the interactive contract. New local PostgreSQL
volumes initialize the canonical schema automatically; existing volumes are left
intact. Do not apply legacy migrations automatically. This database is separate
from Cloud SQL and starts empty; no synthetic data or cloud reimport occurs.

Open http://localhost:8501 for the Streamlit dashboard. It calls the API only and
has no database credentials. Redis runs locally on loopback port 56379. Existing
databases need the new version migration explicitly applied before enabling
Redis-backed queries (this does not run automatically on existing volumes):

```sh
docker compose exec -T postgres psql -U ridepulse -d ridepulse -v ON_ERROR_STOP=1 < sql/008_dataset_versions.sql
```

Review this migration before applying it to Cloud SQL. It adds a version table
and statement triggers; it does not reimport or rewrite the existing facts. New
Compose databases initialize it automatically. Never run legacy migrations as
part of this step.

To run the API outside Docker, install `requirements-api.txt` and set `DB_HOST`,
`DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`, or use `DATABASE_URL`.
For Cloud Run, preserve `INSTANCE_CONNECTION_NAME`, attach the Cloud SQL instance,
and supply the application password using a Secret Manager reference.

```sh
.venv/bin/uvicorn api:app --app-dir src --host 127.0.0.1 --port 8080
```

The API connection pool defaults to a maximum of four connections per process.
`DB_POOL_SIZE` changes this limit. Queries default to a twenty-second statement timeout (`DB_STATEMENT_TIMEOUT_MS`),
pool acquisition is bounded to five seconds, and API transactions are read-only
with repeatable-read isolation. Budget connections across all API instances and
workers before deploying or load testing. Schema validation is not performed on
every readiness check; readiness verifies database connectivity, not dataset
freshness or ingestion health.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `/health/live` | Process liveness; no SQL required |
| `/health/ready` | SQL connectivity; returns 503 when SQL is unavailable |
| `/v1/cities` | Supported cities and actual NYC trip coverage |
| `/v1/stations` | Paginated station list, with `source=trips` or `availability` |
| `/v1/trips/leaderboard` | `metric=pickups` or `dropoffs`, optionally `station_id` |
| `/v1/trips/routes` | Origin/destination pairs, optional `start_station_id`/`end_station_id` |
| `/v1/trips/patterns` | `granularity=hour`, `weekday`, or `day`; optional metric/station |
| `/v1/availability/latest` | Latest observation per station, with freshness fields |
| `/v1/availability/low` | Observed low-bike or low-dock sample fractions |

```sh
curl 'http://localhost:8080/v1/trips/leaderboard?city=nyc&start_date=2026-04-01&end_date=2026-05-01&metric=pickups'
curl 'http://localhost:8080/v1/trips/patterns?city=nyc&start_date=2026-04-01&end_date=2026-05-01&granularity=weekday'
curl 'http://localhost:8080/v1/availability/latest?city=boulder'
```

## Date, city and missing-data rules

- Dates are city-local; `start_date` is inclusive and `end_date` is exclusive.
  For all of April, use April 1 through May 1. NYC uses `America/New_York`;
  Boulder uses `America/Denver`. UTC response timestamps include an explicit offset.
- Date windows are limited to 366 days for trips and 31 days for availability
  summaries. Result pages allow 1-200 rows and offsets up to 10,000. `next_offset`
  is null on the last page. Pages are stable for an unchanged dataset; there is no
  snapshot token guaranteeing consistency between pages while ingestion changes.
- Pickup date/hour/station filters use trip start time/station. Drop-offs use end
  time/station. Route filters use trip start time; incomplete station pairs are
  excluded from routes. Patterns include trips with unknown station IDs unless
  a station filter is supplied, so their totals can exceed station leaderboards.
- Hour patterns combine all observations of each local hour (0-23), including
  both occurrences of a repeated DST hour. Weekdays use ISO Monday=1/Sunday=7.
  Daily buckets are local dates. Empty buckets are filled with zero, accompanied
  by overall dataset coverage so a missing dataset is not mistaken for observed
  zero activity.
- Boulder trip history requests return 422 with an explicit unsupported-history
  explanation. Availability supports both cities. Historical station names are
  derived from trip facts so removed stations remain queryable. There is no
  assumed mapping between trip station IDs and GBFS station IDs.
- `coverage` reports the city's earliest/latest stored trip event, not continuous
  completeness within that span. Results outside those bounds are empty. Import
  manifests and gap reporting will be added with ingestion versioning.
- Latest availability retains each station's newest stored observation, even if
  it was omitted from a later feed. `is_stale` checks collection age and known
  upstream report age; the default threshold is 900 seconds. Unknown upstream
  times are explicit, and future timestamps beyond 60 seconds flag clock skew.
  Metadata-only stations remain available through the station-list endpoint;
  they do not appear as invented observations in latest availability.
- Low availability uses `metric=bikes|docks` and an inclusive count `threshold`
  (default 2). Only installed stations enabled for renting/returning with known
  counts enter the denominator. All observed samples and first/last observation
  times are also returned. This is a sample fraction, not a duration estimate;
  missing intervals are never treated as continuous shortages.

The API, versioned Redis cache and Streamlit dashboard are deployed privately.
Station/hour and route aggregates are used only when their source watermark
matches the durable trip version; other filters and stale aggregates fall back
to fact queries. Trip insertion maintains aligned aggregates transactionally.
Availability pages select station IDs before fetching each newest row to avoid
reading discarded historical observations. Cloud reconciliation and correctness
evidence are recorded in EXPERIMENTS.md and experiments/results/.

## Cache and version behavior

`dataset_versions` tracks trips, metadata and availability independently per city.
Statement triggers advance versions inside the same transaction as actual fact
changes. Duplicate inserts and identical metadata updates do not advance them;
rollbacks also roll back the version. Updates/deletes invalidate affected data;
administrative truncation invalidates the corresponding domain for known cities.
Versions are change tokens, not observation timestamps. Source freshness still
comes from actual trip/observation timestamps.

Each cache key includes endpoint, canonical filters/pagination, city, and every
relevant version plus its commit timestamp. That timestamp also prevents old keys
from a recreated database colliding with a new generation numbered zero. Version
reads and cache-miss queries share a repeatable-read transaction. Results are
consistent as of that transaction snapshot; a concurrent later commit is visible
on the next request. Metadata dependencies are included where names/coordinates
are used. Responses include cache outcome, versions, original generation time and
current serving time. Availability age/stale flags are recalculated on each hit.

Historical query TTL is 900 seconds, station directory TTL is 300 seconds,
latest availability TTL is 30 seconds and low-availability TTL is 60 seconds.
Redis connection/read timeouts are 250 ms with no automatic retries; errors or
malformed cached payloads fall back to SQL. SQL is still required to check durable
versions; the cache never disguises a paused database as fresh. Readiness reports
degraded cache availability without failing when SQL still works. If `REDIS_URL`
is absent, caching is disabled. Redis data is disposable and not persisted locally.

The current trigger is invoker-security: workers need SELECT/INSERT/UPDATE on
`dataset_versions` as well as the fact tables. The migration grants those rights
to an existing `ridepulse_app` role, and an administrator must grant appropriate
rights for any additional deployment role. Version triggers introduce a brief
per-city/domain write lock; measure contention during worker scaling trials.
The migration's changed-city iteration uses a stable order to reduce deadlocks.

API and worker logs emit JSON request/task IDs, outcomes and timings. A native
Cloud Monitoring dashboard covers service latency, instances, queue health, SQL
and Redis. Enhanced log-based metrics require additional configuration access;
see RUNBOOK.md.

## Verification

```sh
.venv/bin/pip install -r requirements-test.txt
docker compose --profile test up -d --wait test-postgres redis
TEST_DATABASE_URL=postgresql://ridepulse_test:local_test_only@localhost:55433/ridepulse_test \
  TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m unittest discover -s tests -v
```

The test database is isolated, uses tmpfs, and is restricted by the integration
test guard to a local database named `ridepulse_test`. Integration fixtures clear
only that dedicated database's tables. Never point tests at project data. The
test-only password is public and intended only for the loopback-bound disposable
container. Tests exercise real SQL filtering, DST, pagination, stale observations,
COPY, duplicates, concurrent delivery, rollback/retry, and commit followed by a
lost response. Storage is mocked in the local suite. Authenticated cloud Pub/Sub recovery,
duplicate delivery and dead-letter forwarding have also passed a separate
controlled cloud experiment (`work/cloud-reliability-final.json`).

Real cache tests reserve Redis database 15 on localhost, check hit/invalidation,
duplicate and rollback stability, corruption/outage fallback, metadata replay,
and freshness recalculation on hits. Streamlit AppTest covers empty history,
unsupported Boulder trip history, stale readings and API unavailability. The
GitHub Actions workflow runs these tests with disposable PostgreSQL and Redis;
check the repository Actions tab for its remote execution status.

If `TEST_DATABASE_URL` is omitted, integration tests are explicitly skipped.
Stop the test container with `docker compose --profile test stop test-postgres`.
The next container start initializes fresh tmpfs data.

## Dashboard presentation

Station rankings use horizontal categorical bars. Pattern axes use local hour,
weekday names or date, with Trips as the measure. Default tables emphasize station
names and counts; the sidebar's Show station IDs and details option exposes IDs,
UTC timestamps and operating flags. Developer toolbar controls use Streamlit viewer
mode. Low-availability results include an optional metadata station_name; its cache
also depends on the metadata version, so renamed labels are invalidated correctly.

## Final demo additions

`GET /v1/availability/coverage?city=nyc&station_id=...` returns `meta.latest_collected_at` and `meta.latest_local_date` for the selected city/station, independent of the current page. With no observations they are null. The dashboard defaults shortage dates to that stored date. `/v1/availability/low` ranks all matching stations by low-sample fraction descending, known sample count descending, then station ID; unknown fractions are last. Ranking occurs before limit/offset.

`/v1/trips/routes` accepts `hide_round_trips=true` (default false). Filtering happens in SQL before ranking/pagination in both fact and aligned-aggregate paths. The flag is included in versioned cache keys. Cache namespace v3 prevents old shortage ordering from being reused. `X-Response-Time-Ms` measures server request time; dashboard labels separately measure its full API round trip and show `meta.cache` status. These are not page-render time or worker benchmark measurements.
