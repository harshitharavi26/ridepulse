# Ingestion deployment and migration notes

## Required deployment configuration

Before deploying the updated images, explicitly set these environment variables
on the Cloud Run worker service and on every GBFS collector job:

| Variable | Value |
| --- | --- |
| GOOGLE_CLOUD_PROJECT | ridepulse-dcs-2026 |
| RIDEPULSE_BUCKET | ridepulse-dcs-2026-data-42502534899 |

The Docker images deliberately contain no project-specific defaults. A local
.env file does not configure Cloud Run. Both entry points now fail at startup
with a message naming missing or blank variables.

Preserve the worker's INSTANCE_CONNECTION_NAME, DB_NAME, DB_USER, and DB_PASSWORD
configuration, Cloud SQL attachment, and service account permissions when updating
the service. Database configuration errors produce HTTP 500 and remain retryable.

Keep Pub/Sub push disabled during deployment and the controlled first-batch test.
Confirm the new worker revision starts successfully before testing one 50,000-trip
batch. Inspect the response status and row counts before processing other batches.
An HTTP 200 alone does not mean data was committed.

## Delivery behavior

Permanent invalid input is logged and acknowledged with HTTP 200 and
`status: rejected`. This includes malformed task messages, disallowed storage
locations, missing CSV columns, invalid GBFS documents, and files with no valid
records. Pub/Sub will not retry those rejected messages; repair the source and
publish a new task if needed. Rejected source objects remain in Cloud Storage.
Infrastructure and database failures return HTTP 500 so delivery can retry.
A dead-letter policy is still useful to bound persistent infrastructure failures;
this change does not create or modify Pub/Sub resources.

For mixed GBFS snapshots, invalid stations are skipped before COPY. Responses
report rows_read, rows_valid, rows_rejected, and rows_written. Up to five station
validation errors per task are logged. Transaction failures roll back the batch.

## Migrations

Run migration files with psql's ON_ERROR_STOP enabled. Each file includes BEGIN
and COMMIT; SET LOCAL and all schema/data changes share one transaction.

The local migration is only for the expected legacy local database and retains
its historical row-count guard. The cloud migration checks for public.trips,
public.stations, public.station_snapshots, public.route_counts,
public.hourly_trip_counts, the route_counts.city column, and the ridepulse_app
role before altering data. Missing prerequisites abort with a named error;
inspect and resolve the schema mismatch before retrying. These checks do not
replace a backup or verify every possible schema difference.

See cloud-deployment-status.md for the live deployment and ingestion results.
The source changes alone do not apply database migrations.
