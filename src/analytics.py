"""Parameterized analytics over canonical facts; no assumptions about refresh state."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from psycopg import sql
import aggregates


class City(str, Enum):
    nyc = "nyc"
    boulder = "boulder"


TIMEZONES = {City.nyc: "America/New_York", City.boulder: "America/Denver"}


class Metric(str, Enum):
    pickups = "pickups"
    dropoffs = "dropoffs"


class Granularity(str, Enum):
    hour = "hour"
    weekday = "weekday"
    day = "day"


class StationSource(str, Enum):
    trips = "trips"
    availability = "availability"


@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime

    def metadata(self):
        return {"start_inclusive": self.start, "end_exclusive": self.end}


def date_range(city: City, start_date: date, end_date: date, max_days=366):
    days = (end_date - start_date).days
    if days < 1 or days > max_days:
        raise HTTPException(422, f"end_date must be after start_date, by at most {max_days} days")
    tz = ZoneInfo(TIMEZONES[city])
    return DateRange(datetime.combine(start_date, time.min, tz).astimezone(timezone.utc),
                     datetime.combine(end_date, time.min, tz).astimezone(timezone.utc))


def require_trips(city):
    if city != City.nyc:
        raise HTTPException(422, {"code": "trip_history_unavailable",
                                  "message": "Boulder has station observations, not imported trip history."})


def trip_columns(metric):
    # Only internally selected identifiers enter query structure.
    prefix = "start" if metric == Metric.pickups else "end"
    return sql.Identifier(f"{prefix}_station_id"), sql.Identifier(f"{prefix}_station_name"), sql.Identifier("started_at" if prefix == "start" else "ended_at")


def trip_where(city, window, metric, station_id=None):
    station, _, event_time = trip_columns(metric)
    query = sql.SQL("city = %s AND {} >= %s AND {} < %s").format(event_time, event_time)
    params = [city.value, window.start, window.end]
    if station_id is not None:
        query += sql.SQL(" AND {} = %s").format(station)
        params.append(station_id)
    return query, params


def trip_coverage(conn, city, metric=Metric.pickups):
    _, _, event_time = trip_columns(metric)
    row = conn.execute(sql.SQL("SELECT MIN({t}) AS first_event_at, MAX({t}) AS last_event_at FROM trips WHERE city = %s").format(t=event_time), [city.value]).fetchone()
    return row


def leaderboards(conn, city, window, metric, station_id, limit, offset):
    if aggregates.available(conn, city):
        conditions = "city=%s AND metric=%s AND hour_start >= %s AND hour_start < %s AND station_id<>'' AND trip_count>0"
        parameters = [city.value,metric.value,window.start,window.end]
        if station_id:
            conditions += " AND station_id=%s"
            parameters.append(station_id)
        return conn.execute(sql.SQL("""SELECT station_id,MAX(station_name) AS station_name,SUM(trip_count)::bigint AS trip_count
            FROM station_hour_counts WHERE {} GROUP BY station_id
            ORDER BY trip_count DESC,station_id LIMIT %s OFFSET %s""").format(sql.SQL(conditions)), [*parameters,limit+1,offset]).fetchall()
    station, name, _ = trip_columns(metric)
    where, params = trip_where(city, window, metric, station_id)
    query = sql.SQL("""
        SELECT {station} AS station_id, MAX({name}) AS station_name, COUNT(*) AS trip_count
        FROM trips WHERE {where} AND NULLIF(BTRIM({station}), '') IS NOT NULL
        GROUP BY {station} ORDER BY trip_count DESC, station_id LIMIT %s OFFSET %s
    """).format(station=station, name=name, where=where)
    return conn.execute(query, [*params, limit + 1, offset]).fetchall()


def routes(conn, city, window, origin, destination, limit, offset):
    if aggregates.available(conn, city):
        coverage = trip_coverage(conn,city)
        if coverage["first_event_at"] and window.start <= coverage["first_event_at"] and window.end > coverage["last_event_at"]:
            conditions, parameters = "city=%s AND trip_count>0", [city.value]
            if origin:
                conditions += " AND start_station_id=%s"
                parameters.append(origin)
            if destination:
                conditions += " AND end_station_id=%s"
                parameters.append(destination)
            return conn.execute(sql.SQL("""SELECT start_station_id,end_station_id,start_station_name,end_station_name,trip_count
                FROM route_counts WHERE {} ORDER BY trip_count DESC,start_station_id,end_station_id LIMIT %s OFFSET %s""").format(sql.SQL(conditions)), [*parameters,limit+1,offset]).fetchall()
    where, params = trip_where(city, window, Metric.pickups, origin)
    if destination:
        where += sql.SQL(" AND end_station_id = %s")
        params.append(destination)
    # Avoid grouping hundreds of thousands of route-name strings. Rank narrow
    # ID/count groups, then look up names for just the returned routes using the
    # existing origin/time index. MAX names match aggregate and fact semantics.
    conn.execute("SET LOCAL work_mem = '16MB'")
    query = sql.SQL("""
        WITH ranked AS MATERIALIZED (
          SELECT start_station_id, end_station_id, COUNT(*) AS trip_count
          FROM trips WHERE {where}
            AND NULLIF(BTRIM(start_station_id), '') IS NOT NULL
            AND NULLIF(BTRIM(end_station_id), '') IS NOT NULL
          GROUP BY start_station_id, end_station_id
          ORDER BY trip_count DESC, start_station_id, end_station_id LIMIT %s OFFSET %s
        )
        SELECT ranked.*, names.start_station_name, names.end_station_name
        FROM ranked LEFT JOIN LATERAL (
          SELECT MAX(start_station_name) AS start_station_name,MAX(end_station_name) AS end_station_name FROM trips t
          WHERE t.city = %s AND t.start_station_id = ranked.start_station_id
            AND t.end_station_id = ranked.end_station_id
            AND t.started_at >= %s AND t.started_at < %s
        ) names ON TRUE
        ORDER BY trip_count DESC, start_station_id, end_station_id
    """).format(where=where)
    return conn.execute(query, [*params, limit + 1, offset, city.value, window.start, window.end]).fetchall()


def patterns(conn, city, window, metric, station_id, granularity, start_date, end_date):
    _, _, event_time = trip_columns(metric)
    local = sql.SQL("({} AT TIME ZONE %s)").format(event_time)
    expressions = {
        Granularity.hour: sql.SQL("EXTRACT(HOUR FROM {})::int").format(local),
        Granularity.weekday: sql.SQL("EXTRACT(ISODOW FROM {})::int").format(local),
        Granularity.day: sql.SQL("{}::date").format(local),
    }
    where, params = trip_where(city, window, metric, station_id)
    query = sql.SQL("SELECT {bucket} AS bucket, COUNT(*) AS trip_count FROM trips WHERE {where} GROUP BY 1 ORDER BY 1").format(bucket=expressions[granularity], where=where)
    if aggregates.available(conn, city):
        local = sql.SQL("(hour_start AT TIME ZONE %s)")
        aggregate_bucket = {Granularity.hour:sql.SQL("EXTRACT(HOUR FROM {})::int"),
                            Granularity.weekday:sql.SQL("EXTRACT(ISODOW FROM {})::int"),
                            Granularity.day:sql.SQL("{}::date")}[granularity].format(local)
        conditions = "city=%s AND metric=%s AND hour_start >= %s AND hour_start < %s"
        parameters = [TIMEZONES[city],city.value,metric.value,window.start,window.end]
        if station_id:
            conditions += " AND station_id=%s"
            parameters.append(station_id)
        query = sql.SQL("SELECT {} AS bucket,SUM(trip_count)::bigint AS trip_count FROM station_hour_counts WHERE {} GROUP BY 1 ORDER BY 1").format(aggregate_bucket,sql.SQL(conditions))
        observed = {row["bucket"]:row["trip_count"] for row in conn.execute(query,parameters)}
    else:
        observed = {row["bucket"]: row["trip_count"] for row in conn.execute(query, [TIMEZONES[city], *params])}
    if granularity == Granularity.hour:
        buckets = range(24)
    elif granularity == Granularity.weekday:
        buckets = range(1, 8)  # ISO Monday=1.
    else:
        buckets = (start_date + timedelta(days=i) for i in range((end_date - start_date).days))
    return [{"bucket": key, "trip_count": observed.get(key, 0)} for key in buckets]


def stations(conn, city, source, limit, offset):
    if source == StationSource.trips:
        require_trips(city)
        # Historical names remain usable when a station disappears from GBFS.
        return conn.execute("""
            SELECT station_id, MAX(station_name) AS station_name FROM (
                SELECT start_station_id AS station_id, start_station_name AS station_name FROM trips WHERE city = %s
                UNION ALL
                SELECT end_station_id, end_station_name FROM trips WHERE city = %s
            ) history WHERE NULLIF(BTRIM(station_id), '') IS NOT NULL
            GROUP BY station_id ORDER BY station_id LIMIT %s OFFSET %s
        """, [city.value, city.value, limit + 1, offset]).fetchall()
    return conn.execute("""
        SELECT station_id, station_name, latitude, longitude, capacity, updated_at
        FROM stations WHERE city = %s ORDER BY station_id LIMIT %s OFFSET %s
    """, [city.value, limit + 1, offset]).fetchall()


def latest_availability(conn, city, station_id, limit, offset, now, freshness_seconds):
    conditions = "city = %s"
    params = [city.value]
    if station_id:
        conditions += " AND station_id = %s"
        params.append(station_id)
    # Page over index-only station IDs first. DISTINCT ON with SELECT * forces
    # heap reads for historical observations that will immediately be discarded.
    rows = conn.execute(sql.SQL("""
        WITH station_ids AS MATERIALIZED (
            SELECT DISTINCT station_id FROM station_snapshots
            WHERE {conditions} ORDER BY station_id LIMIT %s OFFSET %s
        )
        SELECT l.*, s.station_name, s.latitude, s.longitude, s.capacity,
               s.updated_at AS metadata_updated_at
        FROM station_ids ids CROSS JOIN LATERAL (
            SELECT * FROM station_snapshots
            WHERE city=%s AND station_id=ids.station_id
            ORDER BY collected_at DESC LIMIT 1
        ) l
        LEFT JOIN stations s ON s.city=l.city AND s.station_id=l.station_id
        ORDER BY l.station_id
    """).format(conditions=sql.SQL(conditions)), [*params, limit + 1, offset,city.value]).fetchall()
    return availability_freshness(rows, now, freshness_seconds)


def availability_freshness(rows, now, freshness_seconds):
    def timestamp(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    for row in rows:
        age = (now - timestamp(row["collected_at"])).total_seconds()
        reported_age = (now - timestamp(row["last_reported"])).total_seconds() if row["last_reported"] else None
        clock_skew = age < -60 or (reported_age is not None and reported_age < -60)
        row.update(age_seconds=max(0, int(age)),
                   upstream_age_seconds=max(0, int(reported_age)) if reported_age is not None else None,
                   upstream_timestamp_known=reported_age is not None, clock_skew=clock_skew,
                   is_stale=clock_skew or age > freshness_seconds or (reported_age is not None and reported_age > freshness_seconds))
    return rows


def low_availability(conn, city, window, station_id, metric, threshold, limit, offset):
    column = sql.Identifier("bikes_available" if metric == "bikes" else "docks_available")
    conditions = sql.SQL("city = %s AND collected_at >= %s AND collected_at < %s")
    params = [city.value, window.start, window.end]
    if station_id:
        conditions += sql.SQL(" AND station_id = %s")
        params.append(station_id)
    query = sql.SQL("""
        WITH summary AS (
          SELECT station_id, COUNT(*) AS observed_samples,
            COUNT({column}) FILTER (WHERE eligible) AS eligible_samples,
            COUNT(*) FILTER (WHERE eligible AND {column} <= %s) AS low_samples,
            MIN(collected_at) AS first_observed_at, MAX(collected_at) AS last_observed_at
          FROM (
            SELECT *, is_installed IS TRUE AND {enabled} IS TRUE AS eligible
            FROM station_snapshots WHERE {conditions}
          ) observations GROUP BY station_id ORDER BY station_id LIMIT %s OFFSET %s
        )
        SELECT summary.*,s.station_name FROM summary
        LEFT JOIN stations s ON s.city=%s AND s.station_id=summary.station_id
        ORDER BY summary.station_id
    """).format(column=column, enabled=sql.Identifier("is_renting" if metric == "bikes" else "is_returning"), conditions=conditions)
    rows = conn.execute(query, [threshold, *params, limit + 1, offset,city.value]).fetchall()
    for row in rows:
        row["low_sample_fraction"] = row["low_samples"] / row["eligible_samples"] if row["eligible_samples"] else None
    return rows
