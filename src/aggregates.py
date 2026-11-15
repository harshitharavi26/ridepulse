"""Transactionally aligned serving aggregates over immutable trip inserts."""
from psycopg import sql


def available(conn, city):
    # Older databases continue serving correct fact queries during rollout.
    if conn.execute("SELECT to_regclass('aggregate_state') AS relation").fetchone()["relation"] is None:
        return False
    row = conn.execute("""SELECT a.valid AND a.source_version=v.version AS valid
        FROM aggregate_state a JOIN dataset_versions v USING(city)
        WHERE a.city=%s AND v.domain='trips'""", [city.value]).fetchone()
    return bool(row and row["valid"])


def lock_current(conn, city="nyc"):
    # Lock order matches the trip-version trigger; fact/aggregate commits serialize
    # only after independent parsing/COPY staging has finished.
    version = conn.execute("SELECT version FROM dataset_versions WHERE city=%s AND domain='trips' FOR UPDATE", [city]).fetchone()
    state = conn.execute("SELECT source_version,valid FROM aggregate_state WHERE city=%s", [city]).fetchone()
    return bool(version and state and state[1] and state[0] == version[0])


def update_from(conn, relation="inserted_trips", city="nyc", additive=True):
    source = sql.Identifier(relation)
    station_update = sql.SQL("station_hour_counts.trip_count + EXCLUDED.trip_count") if additive else sql.SQL("EXCLUDED.trip_count")
    route_update = sql.SQL("route_counts.trip_count + EXCLUDED.trip_count") if additive else sql.SQL("EXCLUDED.trip_count")
    hour_update = sql.SQL("hourly_trip_counts.trip_count + EXCLUDED.trip_count") if additive else sql.SQL("EXCLUDED.trip_count")
    station_name = sql.SQL("GREATEST(station_hour_counts.station_name,EXCLUDED.station_name)") if additive else sql.SQL("EXCLUDED.station_name")
    start_name = sql.SQL("GREATEST(route_counts.start_station_name,EXCLUDED.start_station_name)") if additive else sql.SQL("EXCLUDED.start_station_name")
    end_name = sql.SQL("GREATEST(route_counts.end_station_name,EXCLUDED.end_station_name)") if additive else sql.SQL("EXCLUDED.end_station_name")
    conn.execute(sql.SQL("""
        INSERT INTO station_hour_counts(city,metric,station_id,hour_start,station_name,trip_count)
        SELECT city,event.metric,COALESCE(event.station_id,''),date_trunc('hour',event.event_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
          MAX(event.station_name),COUNT(*) FROM {source}
        CROSS JOIN LATERAL (VALUES
          ('pickups',start_station_id,start_station_name,started_at),
          ('dropoffs',end_station_id,end_station_name,ended_at)
        ) event(metric,station_id,station_name,event_at)
        WHERE city=%s GROUP BY 1,2,3,4
        ON CONFLICT(city,metric,station_id,hour_start) DO UPDATE SET
          trip_count={update}, station_name={station_name}
    """).format(source=source, update=station_update,station_name=station_name), [city])
    conn.execute(sql.SQL("""
        INSERT INTO route_counts(city,start_station_id,end_station_id,start_station_name,end_station_name,trip_count)
        SELECT city,start_station_id,end_station_id,MAX(start_station_name),MAX(end_station_name),COUNT(*)
        FROM {source} WHERE city=%s AND NULLIF(BTRIM(start_station_id),'') IS NOT NULL
          AND NULLIF(BTRIM(end_station_id),'') IS NOT NULL GROUP BY 1,2,3
        ON CONFLICT(city,start_station_id,end_station_id) DO UPDATE SET trip_count={update},
          start_station_name={start_name},end_station_name={end_name}
    """).format(source=source,update=route_update,start_name=start_name,end_name=end_name), [city])
    conn.execute(sql.SQL("""
        INSERT INTO hourly_trip_counts(city,hour_start,trip_count)
        SELECT city,date_trunc('hour',started_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',COUNT(*)
        FROM {source} WHERE city=%s GROUP BY 1,2
        ON CONFLICT(city,hour_start) DO UPDATE SET trip_count={update}
    """).format(source=source,update=hour_update), [city])
    conn.execute("""INSERT INTO aggregate_state(city,source_version,refreshed_at,valid)
        SELECT city,version,clock_timestamp(),TRUE FROM dataset_versions WHERE city=%s AND domain='trips'
        ON CONFLICT(city) DO UPDATE SET source_version=EXCLUDED.source_version,refreshed_at=EXCLUDED.refreshed_at,valid=TRUE""", [city])


def refresh(conn, city="nyc"):
    conn.execute("SET LOCAL statement_timeout='300s'")
    conn.execute("SET LOCAL work_mem='32MB'")
    conn.execute("SELECT version FROM dataset_versions WHERE city=%s AND domain='trips' FOR UPDATE", [city]).fetchone()
    conn.execute("DELETE FROM station_hour_counts WHERE city=%s", [city])
    # Existing application grants intentionally do not include DELETE on these
    # legacy tables. Zeroing obsolete keys produces the same serving semantics.
    conn.execute("UPDATE route_counts SET trip_count=0 WHERE city=%s", [city])
    conn.execute("UPDATE hourly_trip_counts SET trip_count=0 WHERE city=%s", [city])
    update_from(conn,"trips",city,additive=False)
