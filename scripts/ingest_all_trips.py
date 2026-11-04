import argparse
import csv
import io
import os
import zipfile

import psycopg
from dotenv import load_dotenv

from ingest_trips import (
    ROOT, ZIP_PATH, COLUMNS,
    convert_row, validate_columns
)

load_dotenv(ROOT / ".env")

BATCH_SIZE = 10000
COLUMN_NAMES = ", ".join(COLUMNS)


def insert_batch(conn, records):
    with conn.cursor() as cur:
        with cur.copy(
            f"COPY staging_trips ({COLUMN_NAMES}) FROM STDIN"
        ) as copy:
            for record in records:
                copy.write_row(record)

        cur.execute(f"""
            INSERT INTO trips ({COLUMN_NAMES})
            SELECT {COLUMN_NAMES}
            FROM staging_trips
            WHERE TRUE
            ON CONFLICT (ride_id) DO NOTHING
        """)

        inserted = cur.rowcount

    conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Maximum source rows to scan; 0 means all"
    )

    args = parser.parse_args()

    if args.limit < 0:
        parser.error("--limit must be 0 or greater")

    processed = 0
    valid = 0
    rejected = 0
    inserted = 0
    batch = []

    with psycopg.connect(
        host="127.0.0.1",
        port=5433,
        dbname="ridepulse",
        user="ridepulse",
        password=os.environ["POSTGRES_PASSWORD"]
    ) as conn:

        with conn.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE staging_trips
                (LIKE trips)
                ON COMMIT DELETE ROWS
            """)
        conn.commit()

        with zipfile.ZipFile(ZIP_PATH) as archive:
            files = sorted(
                name for name in archive.namelist()
                if name.endswith(".csv")
            )

            for filename in files:
                with archive.open(filename) as raw:
                    reader = csv.DictReader(
                        io.TextIOWrapper(
                            raw, encoding="utf-8-sig"
                        )
                    )
                    validate_columns(reader)

                    for row in reader:
                        processed += 1

                        try:
                            record = convert_row(row)
                        except (ValueError, TypeError, KeyError) as exc:
                            rejected += 1

                            if rejected <= 5:
                                print(
                                    f"Rejected source row "
                                    f"{processed}: {exc}"
                                )
                        else:
                            batch.append(record)
                            valid += 1

                        if len(batch) >= BATCH_SIZE:
                            inserted += insert_batch(conn, batch)
                            batch.clear()
                            print(
                                f"Scanned {processed:,} rows"
                            )

                        if args.limit and processed >= args.limit:
                            break

                if args.limit and processed >= args.limit:
                    break

        if batch:
            inserted += insert_batch(conn, batch)

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM trips")
            total = cur.fetchone()[0]

    print("\n--- Ingestion Summary ---")
    print(f"Source rows examined: {processed:,}")
    print(f"Valid records: {valid:,}")
    print(f"Rejected records: {rejected:,}")
    print(f"New records inserted: {inserted:,}")
    print(f"Duplicates skipped: {valid - inserted:,}")
    print(f"Total database records: {total:,}")


if __name__ == "__main__":
    main()
