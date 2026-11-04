
import csv
import io
import sqlite3
import tempfile
import zipfile
from pathlib import Path

ZIP_PATH = "data/raw/202604-citibike-tripdata.zip"
BATCH_SIZE = 10000

total_rows = 0
total_duplicates = 0

with tempfile.TemporaryDirectory() as temp_dir:
    db_path = Path(temp_dir) / "ride_ids.db"
    conn = sqlite3.connect(db_path)

    conn.execute("""
        CREATE TABLE ride_ids (
            ride_id TEXT PRIMARY KEY
        )
    """)

    with zipfile.ZipFile(ZIP_PATH) as archive:
        files = sorted(
            name for name in archive.namelist()
            if name.endswith(".csv")
        )

        for filename in files:
            file_rows = 0
            file_duplicates = 0
            batch = []

            with archive.open(filename) as raw:
                reader = csv.DictReader(
                    io.TextIOWrapper(raw, encoding="utf-8-sig")
                )

                for row in reader:
                    batch.append((row["ride_id"],))
                    file_rows += 1

                    if len(batch) == BATCH_SIZE:
                        before = conn.total_changes

                        conn.executemany(
                            "INSERT OR IGNORE INTO ride_ids VALUES (?)",
                            batch
                        )

                        file_duplicates += (
                            len(batch) - (conn.total_changes - before)
                        )
                        batch.clear()

                if batch:
                    before = conn.total_changes

                    conn.executemany(
                        "INSERT OR IGNORE INTO ride_ids VALUES (?)",
                        batch
                    )

                    file_duplicates += (
                        len(batch) - (conn.total_changes - before)
                    )

            conn.commit()
            total_rows += file_rows
            total_duplicates += file_duplicates

            print(
                f"{filename}: {file_duplicates:,} duplicates"
            )

    print("\n--- Duplicate Check Summary ---")
    print(f"Total records: {total_rows:,}")
    print(f"Duplicate ride IDs: {total_duplicates:,}")
    print(f"Unique rides: {total_rows - total_duplicates:,}")

    conn.close()
