# Reproducible RidePulse evaluation

Run the correctness suite before measurements. Keep the source manifest, deployed
image digests, service revisions, per-instance CPU/memory, concurrency, database
tier, timestamps and raw results. Experiments use fixed April 2026 inputs;
the optional forecast is outside the core scope.

## Worker scaling

```sh
caffeinate -i .venv/bin/python scripts/benchmark_workers.py \
  --manifest=experiments/results/worker-inputs-10k.json \
  --output=experiments/results/workers.json --repeats=3 --batches=16 \
  --image=us-central1-docker.pkg.dev/ridepulse-dcs-2026/ridepulse-containers/ridepulse-worker:completion-20261114-v6
```

The repeated study pins sixteen immutable 10,000-row CSV generations, with
**160,000 unique rows counted from the derived files**. `prepare_benchmark_inputs.py`
selects the first 10,000 records from each original 50,000-row source batch and
retains source generations and content checksums. It never edits the originals.
The original sixteen 50,000-row inputs and an 800,000-row pilot sweep are retained
in `worker-inputs.json` and `worker-large-pilot.json`. The pilot has one run per
limit and is reported separately, without repeated-run statistics. Its longer
runs motivated the bounded workload for the repeated study.

Every trial resets only `ridepulse_benchmark` and initializes its aggregate/version
baseline. The schema is owned by the application role. Its trigger functions bind
to that schema; public versions are separate. Fresh insertions are used throughout,
with no synthetic or duplicate-only workload substituted.

Limits are 1/2/4/8 with concurrency one, 1 vCPU and 512 MiB per instance. Producer
concurrency is fixed at sixteen. Trial order rotates across repeats. The harness
uses authenticated direct HTTP delivery of normal Pub/Sub envelopes to isolate
worker/SQL scaling from Pub/Sub dispatch ramp-up. This is explicitly an HTTP-driven
worker experiment, not a measurement of queue dispatch performance. Actual
Pub/Sub authentication, retries and dead-letter behavior are tested separately.

Report elapsed makespan, unique committed rows/sec, retry statuses, exact final
fact/hourly totals, observed peak concurrently active instances, distinct
instances, speedup and efficiency. Observed concurrency is reconstructed from
paired task start/end logs and Cloud Run instance IDs; the configured maximum is
never presented as observed scale. Retain per-run raw logs. If telemetry cannot
establish actual instance activity, the harness fails rather than inventing it.

The SQL instance is shared-core and can become the bottleneck. Trip-version and
aggregate updates serialize per city inside each commit; the lock can cover much
of a batch's database work. Record this limitation and any lack of
linear speedup as a result. Do not upgrade resources between compared trials.
Keep background collection/processing settings fixed and record any maintenance
or restart; discard interrupted trials.

## Cache comparison with Locust

Install `requirements-load.txt`. `finish_cloud_setup.py --run-experiments` creates
a private bypass service with the same API image, SQL pool, CPU, memory,
concurrency and instance limits as the cached API. Both read the same durable
versions; bypass mode skips Redis get/set and computes SQL results.

```sh
.venv/bin/python scripts/benchmark_api.py --cached-url=CACHED_PRIVATE_URL \
  --bypass-url=BYPASS_PRIVATE_URL --output-dir=experiments/results/api \
  --users 1 5 10 20 --seconds=30 --repeats=3
```

The query mix covers pickups, drop-offs, routes, hourly and daily patterns.
Before loading, responses must match exactly across services, including durable
versions. A non-empty dataset and the intended cache modes are required. Every
response is checked against its fixed-data fingerprint/version. Counter files
record cache outcomes, HTTP errors, invalid responses, mismatches and version
drift. Locust produces raw per-endpoint and aggregate CSVs, including p50/p95,
throughput and failure counts. Mode order alternates across repeats.

Initial observations record whether each request was actually a miss or a hit;
do not label a warmed request a cold-cache trial. The sustained comparison uses
a warmed cache. Reference-data mismatches are not automatically called stale
cache: investigate version drift or query correctness first. Fixed-data matching
responses provide the correctness/stale-result check during each run.

The short local Locust smoke check validates the harness only. Its empty local
database timings are not project performance results.

## Reliability evidence

`scripts/verify_cloud_reliability.py` verifies private authenticated Pub/Sub push,
same-message recovery after a controlled database failure, duplicate replay and
dead-letter forwarding. A dedicated failing worker is used. Where new dead-letter
IAM bindings are unavailable, the explicit `--reuse-paused-main-path` mode checks
that publishing is disabled, the main push endpoint is already paused and its
backlog is zero; it records and restores the original push/retry/dead-letter
configuration. No original queue is purged. Attempt limits are approximate.

Local real-PostgreSQL tests additionally establish rollback before commit,
commit followed by lost response, concurrent duplicate safety, and exact
fact/aggregate/version alignment. Mocked Storage tests are distinguished from
real cloud delivery evidence.

## Retained artifacts and plots

`experiments/results/environment.json` records non-secret deployment settings
and immutable image digests. `workers.json` retains every trial, source generation,
observed instance activity and delivery retry statuses. API CSVs/counters are under
`experiments/results/api/`. Reliability and availability-replay evidence are saved
alongside them. Successful worker trials remove their temporary service and reset
only the benchmark schema after results are saved.

Install `requirements-evaluation.txt` to reproduce the Matplotlib figures:

```sh
.venv/bin/python scripts/summarize_experiments.py
```

The tables summarize medians across three repeats. Error bars show observed
min/max across those repeats, not confidence intervals. Do not interpret a median
of trial p95 values as the percentile of all requests pooled together. Client
measurements include the laptop-to-region network path and configured user wait
times. A short closed-loop trial is a bounded demonstration, not a production
capacity guarantee. HTTP errors remain in the raw measurements; result/version
mismatches cause the harness to stop.

Final summary tables use Locust's terminal summary printed after shutdown, checked
against the full response-validation counters. Periodic CSV snapshots can omit a
few final responses; they remain available as supporting diagnostics. Final
throughput/average values inherit Locust's printed precision. Locust latency
percentiles use its response-time buckets, so they are approximations; see the
[Locust statistics source](https://docs.locust.io/en/stable/_modules/locust/stats.html).

On macOS, keep the client awake with `caffeinate -i` while measuring. This prefix
is unnecessary on a server without idle suspension. Worker dispatch refreshes
expired identity tokens, has a 20-minute budget per trial, and rejects timing
when wall/monotonic elapsed time indicates client suspension. An interrupted
initial worker attempt is retained separately in `invalid-worker-attempt.json`
and excluded from measured summaries. Valid API trials completed before that
interruption and are retained.

Cloud Run instance targets can be exceeded temporarily during traffic spikes or
deployment transitions. The large pilot observed five active instances at target
four; retain that observation rather than clamping it to the setting. See
[Google's maximum-instance documentation](https://docs.cloud.google.com/run/docs/configuring/max-instances).
