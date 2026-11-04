
import csv
import io
import zipfile
from collections import Counter

ZIP_PATH = "data/raw/202604-citibike-tripdata.zip"

total_rows = 0
missing = Counter()
bike_types = Counter()
rider_types = Counter()
expected_columns = None

with zipfile.ZipFile(ZIP_PATH) as archive:
    csv_files = sorted(
        name for name in archive.namelist()
        if name.endswith(".csv")
    )

    for filename in csv_files:
        file_rows = 0

        with archive.open(filename) as raw_file:
            text_file = io.TextIOWrapper(
                raw_file, encoding="utf-8-sig"
            )
            reader = csv.DictReader(text_file)

            if expected_columns is None:
                expected_columns = reader.fieldnames
            elif reader.fieldnames != expected_columns:
                raise ValueError(f"Schema mismatch: {filename}")

            for row in reader:
                file_rows += 1

                for column in expected_columns:
                    if not (row[column] or "").strip():
                        missing[column] += 1

                bike_types[row["rideable_type"]] += 1
                rider_types[row["member_casual"]] += 1

        total_rows += file_rows
        print(f"{filename}: {file_rows:,} trips")

print(f"\nTotal trips: {total_rows:,}")

print("\nMissing values:")
for column in expected_columns:
    print(f"{column}: {missing[column]:,}")

print("\nBike types:")
for bike_type, count in bike_types.items():
    print(f"{bike_type}: {count:,}")

print("\nRider types:")
for rider_type, count in rider_types.items():
    print(f"{rider_type}: {count:,}")
