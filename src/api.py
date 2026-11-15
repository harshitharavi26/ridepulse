"""RidePulse read-only REST service. Run: uvicorn api:app --app-dir src."""
import logging
import os
import time
import uuid
from functools import wraps
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout, TooManyRequests

import analytics as queries
from analytics import City, Granularity, Metric, StationSource, TIMEZONES, date_range, require_trips
from database import connection_settings
from query_cache import QueryCache
from logging_config import configure_logging,log_context,TimedCursor

configure_logging()

logger = logging.getLogger("ridepulse.api")
StationId = Annotated[str | None, Query(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=10000)]


@asynccontextmanager
async def lifespan(app):
    # No connection at module import; liveness works when SQL is paused.
    settings = connection_settings()
    conninfo = settings.pop("conninfo", "")
    database_options=settings.pop("options", "")
    pool = ConnectionPool(conninfo, kwargs={**settings, "row_factory": dict_row,"cursor_factory":TimedCursor,
                                  "options": database_options + " -c timezone=UTC -c default_transaction_read_only=on -c statement_timeout=" + str(int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "20000")))},
                          min_size=0, max_size=int(os.getenv("DB_POOL_SIZE", "4")),
                          timeout=5, max_waiting=32, open=False)
    pool.open()
    app.state.pool = pool
    app.state.cache = QueryCache()
    try:
        yield
    finally:
        app.state.cache.close()
        pool.close()


app = FastAPI(title="RidePulse Analytics", version="0.1.0", lifespan=lifespan,
              description="City-local date filters use an exclusive end_date. Boulder supports station observations only.")


def get_connection(request: Request):
    with request.app.state.pool.connection() as conn:
        # Coverage and results describe the same database snapshot.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield conn


Connection = Annotated[psycopg.Connection, Depends(get_connection)]


@app.middleware("http")
async def timing(request, call_next):
    request_id = uuid.uuid4().hex
    context_token=log_context.set({"request_id":request_id})
    start = time.monotonic()
    response=None
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        logger.info("request_completed", extra={"json_fields": {
            "method": request.method,"path": request.url.path,"status": response.status_code if response else 500,
            "latency_ms": round((time.monotonic() - start) * 1000, 2)}})
        log_context.reset(context_token)


@app.exception_handler(psycopg.Error)
@app.exception_handler(PoolTimeout)
@app.exception_handler(TooManyRequests)
def database_error(request, exc):
    logger.warning("database_unavailable", extra={"json_fields": {"error_type": type(exc).__name__}})
    return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable; retry later."}, headers={"Retry-After": "5"})


def response(city, rows, *, limit=None, offset=0, window=None, **metadata):
    has_more = limit is not None and len(rows) > limit
    return {"data": rows[:limit] if limit else rows,
            "meta": {"city": city.value, "timezone": TIMEZONES[city],
                     "generated_at": datetime.now(timezone.utc),
                     "range": window.metadata() if window else None,
                     "limit": limit, "offset": offset,
                     "next_offset": offset + limit if has_more else None,
                     **metadata}}


def cached(domains, ttl):
    def decorate(func):
        @wraps(func)
        def run(*args, **kwargs):
            request, conn, city = kwargs["request"], kwargs["conn"], kwargs["city"]
            params = {k: v for k, v in kwargs.items() if k not in ("request", "conn")}
            payload = request.app.state.cache.execute(conn, city, domains, func.__name__, params, ttl,
                                                       lambda: func(*args, **kwargs))
            now = datetime.now(timezone.utc)
            payload["meta"]["served_at"] = now
            # Freshness must be recalculated on every hit, including TTL crossings.
            if func.__name__ == "availability":
                queries.availability_freshness(payload["data"], now, kwargs.get("freshness_seconds", 900))
                payload["meta"]["observed_at"] = now
            return payload
        return run
    return decorate


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready(request: Request, conn: Connection):
    conn.execute("SELECT 1").fetchone()
    return {"status": "ready", "database": "available", "cache": request.app.state.cache.status()}


@app.get("/v1/cities")
def cities(conn: Connection):
    coverage = queries.trip_coverage(conn, City.nyc)
    return {"data": [{"city": "nyc", "name": "New York City", "timezone": TIMEZONES[City.nyc],
                      "trip_history_supported": True, "trip_coverage": coverage},
                     {"city": "boulder", "name": "Boulder", "timezone": TIMEZONES[City.boulder],
                      "trip_history_supported": False, "trip_coverage": None}]}


@app.get("/v1/stations")
@cached(["trips", "metadata"], 300)
def stations(request: Request, conn: Connection, city: City, source: StationSource = StationSource.availability,
             limit: Limit = 100, offset: Offset = 0):
    return response(city, queries.stations(conn, city, source, limit, offset),
                    limit=limit, offset=offset, station_source=source.value)


@app.get("/v1/trips/leaderboard")
@cached(["trips"], 900)
def leaderboard(request: Request, conn: Connection, city: City, start_date: date, end_date: date,
                metric: Metric = Metric.pickups, station_id: StationId = None,
                limit: Limit = 20, offset: Offset = 0):
    require_trips(city)
    window = date_range(city, start_date, end_date)
    return response(city, queries.leaderboards(conn, city, window, metric, station_id, limit, offset),
                    limit=limit, offset=offset, window=window, metric=metric.value,
                    event_time="started_at" if metric == Metric.pickups else "ended_at",
                    coverage=queries.trip_coverage(conn, city, metric))


@app.get("/v1/trips/routes")
@cached(["trips"], 900)
def routes(request: Request, conn: Connection, city: City, start_date: date, end_date: date,
           start_station_id: StationId = None, end_station_id: StationId = None,
           limit: Limit = 20, offset: Offset = 0):
    require_trips(city)
    window = date_range(city, start_date, end_date)
    return response(city, queries.routes(conn, city, window, start_station_id, end_station_id, limit, offset),
                    limit=limit, offset=offset, window=window, event_time="started_at",
                    excludes_incomplete_station_pairs=True, coverage=queries.trip_coverage(conn, city))


@app.get("/v1/trips/patterns")
@cached(["trips"], 900)
def patterns(request: Request, conn: Connection, city: City, start_date: date, end_date: date,
             granularity: Granularity = Granularity.hour, metric: Metric = Metric.pickups,
             station_id: StationId = None):
    require_trips(city)
    window = date_range(city, start_date, end_date)
    return response(city, queries.patterns(conn, city, window, metric, station_id, granularity, start_date, end_date),
                    window=window, metric=metric.value, granularity=granularity.value,
                    weekday_numbering="ISO Monday=1, Sunday=7" if granularity == Granularity.weekday else None,
                    event_time="started_at" if metric == Metric.pickups else "ended_at",
                    coverage=queries.trip_coverage(conn, city, metric))


@app.get("/v1/availability/latest")
@cached(["availability", "metadata"], 30)
def availability(request: Request, conn: Connection, city: City, station_id: StationId = None,
                 limit: Limit = 100, offset: Offset = 0,
                 freshness_seconds: Annotated[int, Query(ge=60, le=3600)] = 900):
    now = datetime.now(timezone.utc)
    return response(city, queries.latest_availability(conn, city, station_id, limit, offset, now, freshness_seconds),
                    limit=limit, offset=offset, observed_at=now, freshness_seconds=freshness_seconds,
                    description="Latest stored observation per station; upstream timestamps may be unknown.")


@app.get("/v1/availability/low")
@cached(["availability", "metadata"], 60)
def low_availability(request: Request, conn: Connection, city: City, start_date: date, end_date: date,
                     station_id: StationId = None, metric: Literal["bikes", "docks"] = "bikes",
                     threshold: Annotated[int, Query(ge=0, le=100)] = 2,
                     limit: Limit = 100, offset: Offset = 0):
    window = date_range(city, start_date, end_date, max_days=31)
    return response(city, queries.low_availability(conn, city, window, station_id, metric, threshold, limit, offset),
                    limit=limit, offset=offset, window=window, metric=metric, threshold=threshold,
                    description="Fraction of observed known-count samples at installed stations enabled for renting/returning. Gaps are not inferred shortage durations.")
