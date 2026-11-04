import json
import math
import re
import traceback
from datetime import datetime, timezone

from flask import jsonify
from google.cloud import storage


def nonnegative_int(value):
    if value is None or value == "":
        return None
    number = int(value)
    if number < 0 or number > 2147483647:
        raise ValueError("Station count outside PostgreSQL integer range")
    return number


def optional_coordinate(value, limit):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or abs(number) > limit:
        raise ValueError("Invalid station coordinate")
    return number


def optional_boolean(value):
    if value is None:
        return None
    if value not in (0, 1, True, False):
        raise ValueError("Invalid station boolean")
    return bool(value)


def reported_timestamp(value):
    if value is None:
        return None

    epoch = int(value)

    # Ignore invalid timestamps such as NYC's 86400 sentinel.
    if epoch < 946684800:
        return None

    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def process_gbfs_task(task):
    from worker import EXPECTED_BUCKET, database_connection, reject_task

    city = task.get("city")
    task_type = task.get("task_type")
    object_name = task.get("object")

    feed = {
        "station_status": "status",
        "station_information": "information",
    }.get(task_type)

    if (
        city not in ("nyc", "boulder")
        or task.get("bucket") != EXPECTED_BUCKET
        or not isinstance(object_name, str)
        or feed is None
    ):
        return reject_task("Invalid GBFS task")

    pattern = (
        rf"raw/gbfs/{city}/"
        rf"(\d{{8}}T\d{{12}}Z)_station_{feed}\.json"
    )

    match = re.fullmatch(pattern, object_name)
    if not match:
        return reject_task("Invalid GBFS object path")

    try:
        collected_at = datetime.strptime(
            match.group(1), "%Y%m%dT%H%M%S%fZ"
        ).replace(tzinfo=timezone.utc)

        raw = (
            storage.Client()
            .bucket(EXPECTED_BUCKET)
            .blob(object_name)
            .download_as_bytes()
        )

        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ValueError("Invalid GBFS payload")
        stations = payload["data"].get("stations")

        if not isinstance(stations, list) or not stations:
            raise ValueError("Empty or invalid stations list")

        valid_count = 0
        rejected_count = 0

        with database_connection() as conn:
            with conn.cursor() as cur:

                if task_type == "station_status":
                    columns = (
                        "city, station_id, collected_at, last_reported, "
                        "bikes_available, docks_available, "
                        "is_renting, is_returning, is_installed, "
                        "ebikes_available, bike_types"
                    )

                    cur.execute(
                        "CREATE TEMP TABLE gbfs_stage "
                        "(LIKE station_snapshots) ON COMMIT DROP"
                    )

                    with cur.copy(
                        f"COPY gbfs_stage ({columns}) FROM STDIN"
                    ) as copy:
                        for station in stations:
                            try:
                                if not isinstance(station, dict):
                                    raise ValueError("Invalid station record")
                                station_id = str(
                                    station.get("station_id") or ""
                                ).strip()

                                if not station_id:
                                    raise ValueError("Missing station ID")

                                types = station.get(
                                    "num_bikes_available_types"
                                )

                                if types is not None and not isinstance(types, dict):
                                    raise ValueError("Invalid bike types")

                                ebikes = station.get("num_ebikes_available")

                                if ebikes is None and isinstance(types, dict):
                                    ebikes = types.get("electric")

                                record = (
                                    city,
                                    station_id,
                                    collected_at,
                                    reported_timestamp(
                                        station.get("last_reported")
                                    ),
                                    nonnegative_int(
                                        station.get("num_bikes_available")
                                    ),
                                    nonnegative_int(
                                        station.get("num_docks_available")
                                    ),
                                    optional_boolean(
                                        station.get("is_renting")
                                    ),
                                    optional_boolean(
                                        station.get("is_returning")
                                    ),
                                    optional_boolean(
                                        station.get("is_installed")
                                    ),
                                    nonnegative_int(ebikes),
                                    json.dumps(types)
                                    if types is not None else None,
                                )
                            except (ValueError, TypeError, KeyError, OverflowError) as error:
                                rejected_count += 1
                                if rejected_count <= 5:
                                    print(f"Rejected GBFS station: {error}", flush=True)
                                continue
                            copy.write_row(record)
                            valid_count += 1

                    cur.execute(
                        f"INSERT INTO station_snapshots ({columns}) "
                        f"SELECT {columns} FROM gbfs_stage "
                        "WHERE TRUE "
                        "ON CONFLICT "
                        "(city, station_id, collected_at) DO NOTHING"
                    )

                else:
                    columns = (
                        "city, station_id, station_name, "
                        "latitude, longitude, capacity, updated_at"
                    )

                    cur.execute(
                        "CREATE TEMP TABLE gbfs_stage "
                        "(LIKE stations) ON COMMIT DROP"
                    )

                    with cur.copy(
                        f"COPY gbfs_stage ({columns}) FROM STDIN"
                    ) as copy:
                        for station in stations:
                            try:
                                if not isinstance(station, dict):
                                    raise ValueError("Invalid station record")
                                station_id = str(
                                    station.get("station_id") or ""
                                ).strip()

                                if not station_id:
                                    raise ValueError("Missing station ID")

                                record = (
                                    city,
                                    station_id,
                                    station.get("name"),
                                    optional_coordinate(station.get("lat"), 90),
                                    optional_coordinate(station.get("lon"), 180),
                                    nonnegative_int(
                                        station.get("capacity")
                                    ),
                                    collected_at,
                                )
                            except (ValueError, TypeError, KeyError, OverflowError) as error:
                                rejected_count += 1
                                if rejected_count <= 5:
                                    print(f"Rejected GBFS station: {error}", flush=True)
                                continue
                            copy.write_row(record)
                            valid_count += 1

                    cur.execute(
                        f"INSERT INTO stations ({columns}) "
                        f"SELECT {columns} FROM gbfs_stage "
                        "WHERE TRUE "
                        "ON CONFLICT (city, station_id) "
                        "DO UPDATE SET "
                        "station_name = EXCLUDED.station_name, "
                        "latitude = EXCLUDED.latitude, "
                        "longitude = EXCLUDED.longitude, "
                        "capacity = EXCLUDED.capacity, "
                        "updated_at = EXCLUDED.updated_at "
                        "WHERE stations.updated_at <= EXCLUDED.updated_at"
                    )

                written = cur.rowcount
                if valid_count == 0:
                    raise ValueError("GBFS contains no valid stations")

        print(
            f"Committed GBFS {city}/{task_type}: "
            f"{len(stations)} read, {written} written",
            flush=True,
        )

        return jsonify(
            status="committed",
            city=city,
            task_type=task_type,
            rows_read=len(stations),
            rows_written=written,
            rows_valid=valid_count,
            rows_rejected=rejected_count,
        ), 200

    except (ValueError, TypeError, KeyError) as error:
        print(f"Invalid GBFS input: {error}", flush=True)
        return reject_task("Invalid GBFS input")

    except Exception:
        traceback.print_exc()
        return jsonify(error="GBFS ingestion failed"), 500
