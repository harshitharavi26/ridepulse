import base64
import binascii
import csv
import json
import math
import os

from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
from flask import Flask, jsonify, request
from google.cloud import storage

app = Flask(__name__)


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


def optional_float(value):
    if value is None or not value.strip():
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Invalid coordinate")
    return number


def database_connection():
    required = (
        "INSTANCE_CONNECTION_NAME",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    )
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(
            "Missing database configuration: " + ", ".join(missing)
        )
    return psycopg.connect(
        host=f"/cloudsql/{os.environ['INSTANCE_CONNECTION_NAME']}",
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=15,
    )


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
        blob = storage.Client().bucket(bucket_name).blob(object_name)

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
                                    optional_float(row.get("start_lat")),
                                    optional_float(row.get("start_lng")),
                                    optional_float(row.get("end_lat")),
                                    optional_float(row.get("end_lng")),
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

                    cur.execute(
                        "INSERT INTO trips "
                        "SELECT * FROM tmp_trips WHERE TRUE "
                        "ON CONFLICT (ride_id) DO NOTHING"
                    )

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
