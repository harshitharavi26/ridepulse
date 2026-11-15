# RidePulse demonstration

## Prepare

Use the university account and the explicit project flags in RUNBOOK.md. Start
the loopback SQL Auth Proxy, then run:

```sh
.venv/bin/python scripts/session.py start
```

Wait for the collector executions and confirm recent database/API observation
timestamps. A Scheduler trigger is not the capture time: job startup can take
minutes. Once collection finishes, run the preflight below. It checks fresh
readings in both cities and warms the historical query cache, including common
routes, before the audience opens the dashboard:

```sh
.venv/bin/python scripts/verify_serving.py --require-fresh
```

The API's `REDIS_URL` is set by `session.py start` after Redis is ready. Its IP can
change when the disposable cache is recreated. A paused session intentionally has
no running cache/database and stale stored readings; replaying old data does not
make upstream observations current.

Start the authenticated dashboard proxy described in RUNBOOK.md and
open http://localhost:8502. Private services require authorized project access.

## Five-minute walkthrough

1. **NYC trip history:** choose April 1-30, 2026 and pickups. Explain the imported
   April dataset, station ranking, total selected trips and common routes. Change
   the pattern from hour to weekday or day. The API uses city-local dates and an
   exclusive end boundary; the dashboard converts the inclusive date picker.
2. **Arrival versus departure:** switch to dropoffs. Date boundaries now apply
   to arrival times, so trips crossing midnight can enter a different selection.
   Hover a station bar or enable **Show station IDs and details** to find an ID
   for filtering history. Route
   filtering uses the origin, which is stated next to the table.
3. **Station availability:** select NYC, then Boulder. Show station counts,
   freshness warnings and the map. Enable **Show station IDs and details** to see
   collection/upstream timestamps. The map locates
   stations; a point does not guarantee a fresh reading.
4. **Observed shortages:** choose a replayed date, such as November 13, 2026, and
   a low-bike threshold. Explain known/eligible versus missing samples. A low
   fraction describes observed readings, not the duration of a continuous outage.
5. **Data honesty:** select Boulder trip history. The interface explains that
   Boulder supplies station observations, rather than inventing route history.
6. **Distributed design and evidence:** show REPORT.md and its measured plots.
   Point to the raw API/worker results, the real Pub/Sub failure/recovery and
   dead-letter evidence, and the Monitoring dashboard. Distinguish configured
   worker maxima from observed concurrent activity.

## Close

Close dashboard tabs and its local proxy so websockets do not keep instances
active. Apply the selected cost policy:

```sh
.venv/bin/python scripts/session.py stop --delete-cache
.venv/bin/python scripts/session.py status
```

Verify the completed SQL stop operation and activation policy `NEVER`, with Redis
absent. The SQL Admin API may still label the instance `RUNNABLE`. Durable data
and deployments remain; retained storage and raw collection can still incur cost.

## Final recording polish

The dashboard now displays a per-query Redis hit/miss/fallback label and measured API round-trip time. Refreshing the same historical query demonstrates cache reuse. `Hide round trips` creates a distinct route query/cache key and defaults off to preserve the full data view. Shortages default to the latest stored local date for the selected city/station and rank highest observed low-sample fractions before pagination; unknown fractions are last. The map uses observed bikes/(bikes+docks), with red empty, green higher fill and gray stale/unknown, and is collapsed by default with scroll zoom disabled. Tables show 10 returned rows initially with an explicit expansion control to avoid nested vertical scrolling. Some wide detail tables may still need horizontal scrolling.

See PRESENTATION.md for the six-minute narration and required submission checks. Start the full SQL/Redis/ingestion session before recording, run `verify_serving.py --require-fresh`, and warm both the original and hidden-round-trip route queries. A warm cache does not prevent misses after committed data changes or TTL expiry. No new production latency benchmark is claimed for these UI changes.
