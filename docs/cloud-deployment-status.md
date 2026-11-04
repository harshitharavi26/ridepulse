# Cloud deployment status — November 4, 2026

Project: ridepulse-dcs-2026. Region: us-central1.

## Deployment

- Cloud Build 8290bd42-3ae7-4f01-b71b-25aa33b9dbfe succeeded.
- Worker revision ridepulse-worker-00008-bc4 is Ready.
- Worker and both collectors use ingestion-fixes-20261104 images.
- All deployments have GOOGLE_CLOUD_PROJECT and RIDEPULSE_BUCKET set.
- Both collector jobs completed successful verification runs.
- Unit tests: 11 passed before deployment.

## Database and ingestion

- Pre-ingestion Cloud SQL backup 1793813191942: SUCCESSFUL.
- All 80 distinct CSV batches committed: 3,860,371 source rows, zero rejected.
- Initial cloud trips: 10,000. Newly inserted: 3,850,371.
- Final cloud trip count: 3,860,371.
- Replaying the first 50,000-row batch inserted zero additional trips.
- Hourly aggregates: 720 rows, totaling all 3,860,371 trips.
- Route aggregates: 640,056 rows, totaling 3,848,169 trips.
- Trips excluded from routes due to incomplete station pairs: 12,202.
- Fresh GBFS information/status snapshots committed for NYC and Boulder,
  with zero rejected stations: 2,518 per NYC feed, 76 per Boulder feed.
- All cloud migration outcomes already existed and were verified read-only;
  no cloud schema migration was run.
- Local database already has canonical city/timestamp columns and 3,860,371
  trips; the legacy local migration was not run.

## Delivery and operating state

- Authenticated Pub/Sub delivery verified with a temporary filtered subscription:
  worker returned HTTP 200 for a permanently invalid test task.
- Temporary validation subscription removed after the test.
- Main subscription push remains disabled, and collector GBFS_PUBLISH_TASKS
  remains false. Scheduled collectors continue uploading raw snapshots.
- Main subscription's dead-letter topic, five-attempt policy, retry backoff,
  dead-letter publisher/subscriber permissions, private worker invocation
  permission, and push-token creation permission were verified.
- Actual repeated infrastructure failure/dead-letter forwarding was not simulated.
- Cloud SQL shutdown completed; verified final state: STOPPED, activation policy NEVER.

## Remaining activation work

Continuous ingestion remains paused. Before enabling it, start Cloud SQL, review
the main subscription backlog, configure its authenticated push endpoint, and
turn on collector task publishing. A stopped database cannot serve the API or
process ingestion tasks. Complete a controlled retry/dead-letter forwarding test
before declaring failure handling fully verified.

See ingestion-verification.json for all batch responses and aggregate totals,
and shivani-handoff.md for resources, schema, and the ingestion contract.
