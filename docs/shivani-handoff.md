# RidePulse infrastructure handoff

## Resources

Project: ridepulse-dcs-2026. Region: us-central1.

| Resource | Name |
| --- | --- |
| Cloud SQL PostgreSQL instance | ridepulse-postgres |
| Database | ridepulse |
| Application database role | ridepulse_app |
| Cloud SQL connection name | ridepulse-dcs-2026:us-central1:ridepulse-postgres |
| Storage bucket | ridepulse-dcs-2026-data-42502534899 |
| Private worker | ridepulse-worker |
| Worker URL | https://ridepulse-worker-qkkoqlp5ra-uc.a.run.app |
| Station status collector job | ridepulse-status-collector |
| Station metadata collector job | ridepulse-metadata-collector |
| Task topic | ridepulse-tasks |
| Worker subscription | ridepulse-worker-push |
| Dead-letter topic | ridepulse-dead-letter |
| Database password secret | ridepulse-db-password |

Use granted IAM permissions and Secret Manager to access the database password;
no password is stored in this document. The worker requires authentication.
Cloud SQL Auth Proxy provides a local database connection; it does not replace
the PostgreSQL username and password. The local Docker database uses port 5433,
so use a different local port for the cloud proxy.

## Application data

- trips: historical NYC Citi Bike trips. started_at and ended_at are TIMESTAMPTZ;
  interpret naive source timestamps in America/New_York.
- stations: current station metadata, keyed by (city, station_id).
- station_snapshots: GBFS status history, keyed by
  (city, station_id, collected_at). Cities are nyc and boulder.
- route_counts: NYC route counts keyed by city and both station IDs.
  Trips missing either station ID are excluded.
- hourly_trip_counts: counts keyed by city and an absolute hour_start timestamp.

Use sql/005_route_aggregates.sql and sql/007_hourly_aggregates.sql to refresh NYC
aggregates after importing additional trips. Both upsert counts from the full
current NYC trip set. They do not remove obsolete aggregate keys if source trips
are deleted; a deletion workflow needs explicit aggregate cleanup.

## Ingestion contract

POST a Pub/Sub-compatible JSON envelope to the private worker using an
appropriate Google identity token. message.data is a base64-encoded JSON task:

```json
{
  "task_type": "trip_batch",
  "city": "nyc",
  "bucket": "ridepulse-dcs-2026-data-42502534899",
  "object": "raw/citibike/2026-04/batches/202604-citibike-tripdata-part1-batch-001.csv"
}
```

GBFS task types are station_information and station_status; their object paths
are raw/gbfs/{city}/{UTC timestamp}_{task_type}.json. Collector uploads already
use this format. Always inspect the response status: committed indicates a
successful database commit; rejected acknowledges permanently invalid input
without asking Pub/Sub to retry. Infrastructure failures return HTTP 500.
Trip ingestion is idempotent on ride_id; repeating a batch skips existing trips.

## Operating state and remaining work

Consult cloud-deployment-status.md for verified counts and the current running
state. During controlled testing, collector task publishing and Pub/Sub push
remain disabled. Scheduled collectors can still upload raw files without writing
to the database. A stopped Cloud SQL instance must be started before invoking
any database ingestion or using the application.

Authenticated delivery was tested through a temporary filtered subscription:
Pub/Sub reached the private worker and received HTTP 200 for a permanently
invalid task. The temporary subscription was removed. The push service account
is ridepulse-pubsub-push@ridepulse-dcs-2026.iam.gserviceaccount.com. Its Cloud Run
invocation permission, Pub/Sub token creation permission, and dead-letter
publisher/subscriber grants were verified. This does not simulate repeated
infrastructure failures or verify actual dead-letter forwarding.

Before enabling continuous ingestion, confirm Cloud SQL availability and review
the main subscription's backlog. Enable collector GBFS_PUBLISH_TASKS and the
push endpoint together only when continuous database operation is intended.
Do not enable delivery while Cloud SQL is stopped.

Shivani's application work remains: FastAPI analytics endpoints, Redis caching,
Streamlit dashboard, scalability experiments, and application documentation.
Infrastructure credentials and user access still require the project's normal
IAM setup; this handoff does not grant anyone access.
