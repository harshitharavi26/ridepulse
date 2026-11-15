# RidePulse recording script and final submission checklist

Participants: **Harshitha Ravishankar and Shivani Dilipkumar Patel**. Title: **RidePulse - Distributed Bike-Share Trip & Station Analytics Service**.

Target: approximately **6 minutes**, including about **100 seconds of the actual working system**. Rehearse the final edit; the required window is 4-7 minutes. The slides contain presenter notes. Use the live dashboard during the demo rather than leaving the demo agenda slide on screen.

## Recording-day preflight

1. Follow RUNBOOK.md: authenticate the university account, start the SQL proxy, then run `python scripts/session.py start`. Wait for Redis readiness and the fresh collector execution to finish. Do not assume opening a dashboard starts SQL or Redis.
2. Run `python scripts/verify_serving.py --require-fresh` after the collectors finish. This checks real freshness and warms the standard April history queries. Check `cache: available` on readiness.
3. Open the private dashboard through the Run service proxy. Load NYC history once, then toggle **Hide round trips** once to warm that separate cache key. Open both cities' availability pages, shortage view and map before recording. Live availability TTL is only 30 seconds and new snapshots invalidate keys, so an occasional miss remains legitimate.
4. Prepare tabs for one structured worker task log and Monitoring. Log-based metric series began at creation; generate normal real requests/tasks and allow Monitoring propagation. Do not relabel synthetic fixtures or old screenshots as live.
5. Close unrelated tabs and notifications. Use readable browser zoom, frame the plots, and capture microphone audio. Keep secret values, auth tokens and billing details out of the recording.
6. If freshness verification fails, resolve ingestion first. Replaying old snapshots does not make them current. Clearly distinguish stale readings and unknown fractions from empty stations.

## Timed narration

### 0:00–0:25 - Title

We are Harshitha Ravishankar and Shivani Dilipkumar Patel. RidePulse is our distributed bike-share trip and station analytics service for Datacenter Scale Computing. It combines NYC trip history with NYC and Boulder station observations. Our main question is how caching, parallel processing and retries affect both performance and correctness.

### 0:25–0:55 - Goals

Users can find busy pickup and drop-off stations, common routes and local-time patterns across 3.86 million April 2026 NYC trips. They can also inspect bike and dock observations in Boulder and NYC. We built the service around three systems goals: correct results under duplicate delivery, a controlled cache comparison, and a worker scaling experiment with fixed per-instance resources. This is an engineering study of a real service; we do not claim a new bike-share algorithm.

### 0:55–1:55 - Architecture

Cloud Scheduler launches collectors. They archive immutable raw objects in Cloud Storage and publish JSON task references through Pub/Sub. Private Cloud Run workers read the pinned object generation, validate it, stream rows with COPY and commit facts, versions and aggregates together in PostgreSQL. Pub/Sub can redeliver, so primary keys make repeated tasks safe; retryable failures return errors and persistent failures can reach the dead-letter topic. The Streamlit dashboard calls a private FastAPI service. The API checks committed SQL versions before retrieving versioned results from Redis. This means a cache hit is still consistent with SQL, but a paused database cannot be hidden by the cache. Structured logs and Monitoring observe both paths. Managed containers, APIs, queues, database, key-value cache and storage exceed the four-component requirement.

### 1:55–2:45 - Testing and debugging

The services run in us-central1 with one vCPU and 512 MiB per API or worker instance. Workers use concurrency one; the API uses concurrency eight and one SQL connection per instance. SQL is a shared-core db-g1-small instance with 1.7 GiB RAM, and Redis is Basic one GiB. Our current local suite has 73 checks using real PostgreSQL and Redis, plus actual Pub/Sub emulator push and duplicate replay. We tested midnight and daylight-saving boundaries, lost responses after commit, duplicate delivery, schema isolation and aggregate equality. We debugged a timeout on larger station history using indexed station paging and newest-row lookups. A separate cloud worker copy tested real broker recovery and dead lettering. We did not kill workers during the scaling experiment.

### 2:45–3:15 - Cache results

We compared the same API implementation and fixed query mix with Redis enabled or bypassed, across one, five, ten and twenty users, three thirty-second repeats each. All 17,342 cached requests succeeded and matched expected results. At twenty users the median trial p95 was 220 milliseconds and successful throughput was about 105 requests per second. The bypass service had 489 errors across 700 requests; sampled logs identify SQL pool timeouts. This is a warmed, short, laptop-to-region test, not a production capacity guarantee. The chart shows successful throughput, so failures are not counted as useful work.

### 3:15–3:45 - Scaling results

The repeated study inserts 160,000 unique rows from sixteen ten-thousand-row batches. We kept the input and per-instance resources fixed, verified exact row and aggregate totals, and measured actual active workers. Eight workers achieved only 1.56 times the throughput of one; two captured most of the gain. A separate exploratory 800,000-row pilot using fifty-thousand-row batches actually slowed down with extra workers. The shared SQL tier and same-city transactional updates are plausible bottlenecks, but the experiment does not separate CPU, I/O and lock effects. Scaling was driven by authenticated HTTP envelopes; real Pub/Sub reliability was tested separately.

### 3:45–5:25 - Live demonstration

Switch to the running dashboard now. Start with NYC April trip history. Point out the total count, readable station names and local-hour pattern. Refresh the same query and point to the served-from-cache status and measured API round-trip time. Explain that the number is the actual response time, not the load-test percentile. Toggle hide round trips to show a filtered route list. Switch to Boulder availability and show the fresh versus stale counts. Open the station map: red indicates empty, green indicates greater observed fill, and gray means stale or unknown. Open observed low availability: the default date comes from stored data, not the laptop clock; stations are ranked by observed shortage fraction. Briefly show a structured worker task ID and the Monitoring charts. Newly created log metrics only have data after their creation. Explain that pauses are deliberate and missing samples do not represent zero bikes. Keep the demo to about one minute forty seconds; do not spend recording time provisioning Redis.

### 5:25–6:00 - Capabilities and limits

RidePulse preserves 3.86 million trip facts, serves repeatable analytics, and supports safe replay. The application is useful, while the main learning is why caching and bounded worker parallelism matter. Only April is loaded, and snapshot coverage has gaps. The warm-cache benchmark does not establish production capacity. Our reproducible logged-handler estimate for the twelve primary worker trials is about 1.36 cents, excluding startup and other services. SQL plus Redis cost about 8.4 cents per provisioned hour before storage and other charges. We stop SQL and delete Redis between sessions. The budget alert was explicitly skipped; monitoring alerts are deployed. The repository contains run instructions, retained measurements and the detailed report.

## Rubric coverage

| Requirement | Evidence in presentation/report |
| --- | --- |
| Team names and title | Slide 1, report opening and README |
| Goals and overview | Slide 2; report overview/data |
| Specific hardware/software | Slides 3-4; component tradeoffs and hardware sections |
| Architecture/interactions | Editable slide 3; report architecture and detailed component descriptions |
| Testing/debugging | Slide 4 plus retry/schema/DST/correctness cases and debugging account in report |
| Required working demo | Actual dashboard at 3:45-5:25; cache badge, routes, both cities, map, shortage date/rank and task/metrics |
| Workload/capabilities/limits | Slides 5-6 and 8; raw measurements and methodological caveats in report |

## Submission actions the team must finish

- Review both names and the report. The professor's specific final assignment says **one team member submits for the team**; it differs from earlier generic course wording. Use the final assignment unless the instructor clarifies otherwise.
- Record the 4-7 minute presentation with the live demo. This package is not a recorded video.
- Upload to an accessible video host, then have a friend open the final URL without your login. Check audio, duration, resolution and that the demo is visible.
- The owner must verify `elgoodm`, `rezwan09` and `VedMulik` have required repository access. The repository is public, but collaborator inspection was denied to the GitHub integration; accepted invitations were not verified.
- Upload **RidePulse_Submission_Report.pdf** and add the actual video URL plus `https://github.com/harshitharavi26/ridepulse` in submission comments. Replace the video placeholder only after the URL exists.
- Deadline supplied by the assignment: **December 4 at 11:59 p.m.** Confirm the course/Canvas timezone. Avoid the hard-deadline penalty by uploading early.
- After recording, close dashboard tabs/proxies and run `python scripts/session.py stop --delete-cache`; verify SQL policy NEVER and Redis absent.

Suggested submission comment (fill the video URL):

```text
RidePulse - Distributed Bike-Share Trip & Station Analytics Service
Team: Harshitha Ravishankar; Shivani Dilipkumar Patel
Code: https://github.com/harshitharavi26/ridepulse
Video: [paste the verified accessible recording URL]
```

## Deliberate scope choices

Only April 2026 trip history is loaded. The larger pilot is 800,000 rows from April, not another month. Batches are row-bounded (50k imported/pilot, 10k repeated study), rather than day/hour partitions. Broker failure recovery used an isolated worker copy rather than killing workers during scaling. Forecasting was optional and is deferred. The proposal's budget alert was explicitly skipped at Shivani's request; the prepared definition is not an active budget. These are documented limitations, not hidden completion claims.
