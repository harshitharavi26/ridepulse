import csv
import io
import math
import os
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "data/raw/202604-citibike-tripdata.zip"

load_dotenv(ROOT / ".env")

NYC_TIMEZONE = ZoneInfo("America/New_York")

COLUMNS = [
    "ride_id", "city", "rideable_type",
    "started_at", "ended_at",
    "start_station_name", "start_station_id",
    "end_station_name", "end_station_id",
    "start_lat", "start_lng", "end_lat", "end_lng",
    "member_casual"
]

SOURCE_COLUMNS = [col for col in COLUMNS if col != "city"]

INSERT_SQL = f"""
INSERT INTO trips ({", ".join(COLUMNS)})
VALUES ({", ".join(["%s"] * len(COLUMNS))})
ON CONFLICT (ride_id) DO NOTHING
"""


def clean_text(value):
    if value is None:
        return None
    return value.strip() or None


def parse_timestamp(value):
    value = clean_text(value)

    if value is None:
        raise ValueError("Missing timestamp")

    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NYC_TIMEZONE)

    return dt


def parse_coordinate(value, column):
    value = clean_text(value)

    if value is None:
        return None

    number = float(value)

    if not math.isfinite(number):
        raise ValueError(f"Invalid {column}")

    limit = 90 if column.endswith("_lat") else 180

    if not -limit <= number <= limit:
        raise ValueError(f"Out-of-range {column}")

    return number


def convert_row(row):
    ride_id = clean_text(row["ride_id"])

    if not ride_id:
        raise ValueError("Missing ride_id")

    started = parse_timestamp(row["started_at"])
    ended = parse_timestamp(row["ended_at"])

    if ended <= started:
        raise ValueError(
            f"Invalid trip duration for {ride_id}"
        )

    return (
        ride_id,
        "nyc",
        clean_text(row["rideable_type"]),
        started,
        ended,
        clean_text(row["start_station_name"]),
        clean_text(row["start_station_id"]),
        clean_text(row["end_station_name"]),
        clean_text(row["end_station_id"]),
        parse_coordinate(row["start_lat"], "start_lat"),
        parse_coordinate(row["start_lng"], "start_lng"),
        parse_coordinate(row["end_lat"], "end_lat"),
        parse_coordinate(row["end_lng"], "end_lng"),
        clean_text(row["member_casual"]),
    )


def validate_columns(reader):
    missing = set(SOURCE_COLUMNS) - set(
        reader.fieldnames or []
    )

    if missing:
        raise ValueError(
            f"Missing CSV columns: {sorted(missing)}"
        )


def main():
    records = []
    rejected = 0

    with zipfile.ZipFile(ZIP_PATH) as archive:
        csv_files = sorted(
            name for name in archive.namelist()
            if name.endswith(".csv")
        )

        with archive.open(csv_files[0]) as raw_file:
            reader = csv.DictReader(
                io.TextIOWrapper(
                    raw_file, encoding="utf-8-sig"
                )
            )
            validate_columns(reader)

            for row in reader:
                try:
                    records.append(convert_row(row))
                except (ValueError, TypeError, KeyError) as exc:
                    rejected += 1
                    if rejected <= 5:
                        print(f"Rejected row: {exc}")

                if len(records) >= 1000:
                    break

    with psycopg.connect(
        host="127.0.0.1",
        port=5433,
        dbname="ridepulse",
        user="ridepulse",
        password=os.environ["POSTGRES_PASSWORD"]
    ) as conn:
        with conn.cursor() as cur:
            if records:
                cur.executemany(INSERT_SQL, records)

            cur.execute("SELECT COUNT(*) FROM trips")
            total = cur.fetchone()[0]

    print(f"Valid records processed: {len(records):,}")
    print(f"Rejected records: {rejected:,}")
    print(f"Total trips in database: {total:,}")


if __name__ == "__main__":
    main()
