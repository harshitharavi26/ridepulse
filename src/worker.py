import base64
import binascii
import csv
import json
import math
import os
import logging
import time
import uuid
import aggregates

from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
from flask import Flask, jsonify, request, g
from google.cloud import storage
from database import connection_settings
from logging_config import configure_logging, log_context

app = Flask(__name__)
configure_logging()
app.logger = logging.getLogger("ridepulse.worker")


@app.before_request
def task_context():
    envelope = request.get_json(silent=True)
    message = envelope.get("message", {}) if isinstance(envelope, dict) else {}
    task_id = message.get("messageId") if isinstance(message, dict) else None
    g.context_token = log_context.set({"task_id": str(task_id)[:256] if task_id else uuid.uuid4().hex})
    g.task_started = time.monotonic()
    app.logger.info("task_started")


@app.after_request
def task_completed(response):
    payload = response.get_json(silent=True) or {}
    app.logger.info("task_completed", extra={"json_fields": {
        "http_status": response.status_code, "result": payload.get("status", "failed"),
        "task_type": payload.get("task_type", getattr(g,"task_type","unknown")), "city": payload.get("city",getattr(g,"city",None)),
        "rows_inserted": payload.get("rows_inserted", payload.get("rows_written", 0)),
        "rows_read": payload.get("rows_read", 0), "rows_valid": payload.get("rows_valid", 0),
        "duplicates_skipped": payload.get("duplicates_skipped", 0),
        "rows_rejected": payload.get("rows_rejected",0),
        "latency_ms": round((time.monotonic() - g.task_started) * 1000, 2)}})
    log_context.reset(g.context_token)
    return response


def required_environment(*keys):
    missing = [key for key in keys if not os.getenv(key, "").strip()]
    if missing:
        raise RuntimeError("Missing required configuration: " + ", ".join(missing))
    return tuple(os.environ[key] for key in keys)


PROJECT_ID, EXPECTED_BUCKET = required_environment(
    "GOOGLE_CLOUD_PROJECT", "RIDEPULSE_BUCKET"
)


def reject_task(reason):
    app.logger.warning("Rejected task: %s", reason)
    return jsonify(status="rejected", error=reason), 200


NYC_TIMEZONE = ZoneInfo("America/New_York")

TRIP_COLUMNS = (
    "ride_id, city, rideable_type, started_at, ended_at, "
    "start_station_name, start_station_id, end_station_name, "
    "end_station_id, start_lat, start_lng, end_lat, end_lng, "
    "member_casual"
)


def parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing timestamp")
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NYC_TIMEZONE)
    return dt


def clean_text(value):
    if value is None:
        return None
    return str(value).strip() or None


def optional_float(value,limit=None):
    if value is None or not value.strip():
        return None
    number = float(value)
    if not math.isfinite(number) or (limit is not None and abs(number)>limit):
        raise ValueError("Invalid coordinate")
    return number


def database_connection():
    return psycopg.connect(**connection_settings())


def storage_blob(task):
    generation = task.get("generation")
    if generation is not None:
        if isinstance(generation, bool) or not str(generation).isdigit() or int(generation) <= 0:
            raise ValueError("Invalid Storage generation")
        return storage.Client().bucket(task["bucket"]).blob(task["object"], generation=int(generation))
    return storage.Client().bucket(task["bucket"]).blob(task["object"])


@app.post("/")
def process_task():
    envelope = request.get_json(silent=True)

    if not isinstance(envelope, dict):
        return reject_task("Invalid request body")

    message = envelope.get("message")

    if not isinstance(message, dict) or not isinstance(message.get("data"), str):
        return reject_task("Invalid Pub/Sub message")

    try:
        task = json.loads(
            base64.b64decode(message["data"], validate=True)
        )
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return reject_task("Invalid message data")

    if isinstance(task,dict):
        g.task_type=task.get("task_type") if task.get("task_type") in ("trip_batch","station_status","station_information") else "unsupported"
        g.city=task.get("city") if task.get("city") in ("nyc","boulder") else None

    if isinstance(task, dict) and task.get("task_type") in ("station_status", "station_information"):
        from gbfs_ingest import process_gbfs_task
        return process_gbfs_task(task)

    if not isinstance(task, dict) or task.get("task_type") != "trip_batch":
        return reject_task("Unsupported task type")

    bucket_name = task.get("bucket")
    object_name = task.get("object")

    if (
        bucket_name != EXPECTED_BUCKET
        or task.get("city") != "nyc"
        or not isinstance(object_name, str)
        or not object_name.startswith("raw/citibike/")
        or not object_name.endswith(".csv")
    ):
        return reject_task("Invalid storage location")

    try:
        blob = storage_blob(task)

        with blob.open("r", encoding="utf-8") as source:
            reader = csv.DictReader(source)

            required = {
                "ride_id", "started_at", "ended_at",
                "start_station_id", "end_station_id"
            }

            if not required.issubset(reader.fieldnames or []):
                return reject_task("Missing CSV columns")

            with database_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "CREATE TEMP TABLE tmp_trips "
                        "(LIKE trips) ON COMMIT DROP"
                    )

                    copy_sql = (
                        f"COPY tmp_trips ({TRIP_COLUMNS}) FROM STDIN"
                    )

                    row_count = 0
                    valid_count = 0
                    rejected_count = 0

                    with cur.copy(copy_sql) as copy:
                        for row in reader:
                            row_count += 1

                            try:
                                ride_id = clean_text(row.get("ride_id"))
                                if not ride_id:
                                    raise ValueError("Missing ride_id")

                                started = parse_timestamp(row.get("started_at"))
                                ended = parse_timestamp(row.get("ended_at"))

                                if ended <= started:
                                    raise ValueError("Invalid trip duration")

                                record = (
                                    ride_id,
                                    "nyc",
                                    clean_text(row.get("rideable_type")),
                                    started,
                                    ended,
                                    clean_text(row.get("start_station_name")),
                                    clean_text(row.get("start_station_id")),
                                    clean_text(row.get("end_station_name")),
                                    clean_text(row.get("end_station_id")),
                                    optional_float(row.get("start_lat"),90),
                                    optional_float(row.get("start_lng"),180),
                                    optional_float(row.get("end_lat"),90),
                                    optional_float(row.get("end_lng"),180),
                                    clean_text(row.get("member_casual")),
                                )

                            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                                rejected_count += 1
                                if rejected_count <= 5:
                                    app.logger.warning(
                                        "Rejected CSV row %s: %s",
                                        row_count, exc
                                    )
                                continue

                            copy.write_row(record)
                            valid_count += 1

                    if valid_count == 0:
                        raise ValueError("CSV contains no valid trips")

                    insertion_sql = (
                        "INSERT INTO trips "
                        "SELECT * FROM tmp_trips WHERE TRUE "
                        "ON CONFLICT (ride_id) DO NOTHING"
                    )
                    if os.getenv("RIDEPULSE_MAINTAIN_AGGREGATES", "false").lower() == "true":
                        aligned = aggregates.lock_current(conn)
                        cur.execute("CREATE TEMP TABLE inserted_trips ON COMMIT DROP AS WITH new_rows AS (" + insertion_sql + " RETURNING *) SELECT * FROM new_rows")
                        inserted = cur.rowcount
                        if aligned and inserted:
                            aggregates.update_from(conn)
                    else:
                        cur.execute(insertion_sql)
                        inserted = cur.rowcount

        # Reaching here means the database transaction committed.
        app.logger.info(
            "Committed %s trips from %s; %s rows examined",
            inserted, object_name, row_count
        )

        return jsonify(
            status="committed",
            city="nyc",
            rows_read=row_count,
            rows_valid=valid_count,
            rows_rejected=rejected_count,
            rows_inserted=inserted,
            duplicates_skipped=valid_count - inserted,
        ), 200

    except (ValueError, KeyError, TypeError, csv.Error) as exc:
        app.logger.warning("Invalid trip data: %s", exc)
        return reject_task("Invalid trip data")

    except Exception:
        app.logger.exception("Trip ingestion failed")
        return jsonify(error="Trip ingestion failed"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
