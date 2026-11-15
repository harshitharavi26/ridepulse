# RidePulse demonstration

## Prepare

Use the university account and the explicit project flags in RUNBOOK.md. Start
the loopback SQL Auth Proxy, then run:

```sh
.venv/bin/python scripts/session.py start
```

Wait for the collector executions and confirm recent database/API observation
timestamps. A Scheduler trigger is not the capture time: job startup can take
minutes. Start the authenticated dashboard proxy described in RUNBOOK.md and
open http://localhost:8502. Private services require authorized project access.

## Five-minute walkthrough

1. **NYC trip history:** choose April 1-30, 2026 and pickups. Explain the imported
   April dataset, station ranking, total selected trips and common routes. Change
   the pattern from hour to weekday or day. The API uses city-local dates and an
   exclusive end boundary; the dashboard converts the inclusive date picker.
2. **Arrival versus departure:** switch to dropoffs. Date boundaries now apply
   to arrival times, so trips crossing midnight can enter a different selection.
   Use a station ID from the displayed trip ranking to filter history. Route
   filtering uses the origin, which is stated next to the table.
3. **Station availability:** select NYC, then Boulder. Show station counts,
   collection/upstream timestamps, stale warnings and the map. The map locates
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
