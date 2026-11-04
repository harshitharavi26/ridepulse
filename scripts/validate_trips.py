
import csv
import io
import zipfile
from datetime import datetime

ZIP_PATH = "data/raw/202604-citibike-tripdata.zip"

total = 0
invalid_timestamps = 0
invalid_durations = 0
long_trips = 0
outside_april = 0

earliest = None
latest = None

with zipfile.ZipFile(ZIP_PATH) as archive:
    files = sorted(
        name for name in archive.namelist()
        if name.endswith(".csv")
    )

    for filename in files:
        with archive.open(filename) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig")
            reader = csv.DictReader(text)

            for row in reader:
                total += 1

                try:
                    start = datetime.fromisoformat(row["started_at"])
                    end = datetime.fromisoformat(row["ended_at"])
                except ValueError:
                    invalid_timestamps += 1
                    continue

                duration = (end - start).total_seconds()

                if duration <= 0:
                    invalid_durations += 1

                if duration > 24 * 60 * 60:
                    long_trips += 1

                if start.year != 2026 or start.month != 4:
                    outside_april += 1

                if earliest is None or start < earliest:
                    earliest = start

                if latest is None or end > latest:
                    latest = end

        print(f"Validated: {filename}")

print("\n--- Validation Summary ---")
print(f"Total records: {total:,}")
print(f"Invalid timestamps: {invalid_timestamps:,}")
print(f"Zero or negative durations: {invalid_durations:,}")
print(f"Trips over 24 hours: {long_trips:,}")
print(f"Trips starting outside April: {outside_april:,}")
print(f"Earliest start: {earliest}")
print(f"Latest end: {latest}")
