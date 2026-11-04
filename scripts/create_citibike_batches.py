import csv
import io
import zipfile
from pathlib import Path

ARCHIVE = Path("data/raw/202604-citibike-tripdata.zip")
OUTPUT = Path("data/processed/citibike/2026-04/batches")
BATCH_SIZE = 50_000

OUTPUT.mkdir(parents=True, exist_ok=True)

if list(OUTPUT.glob("*.csv")):
    raise SystemExit("Batch files already exist. No files overwritten.")

total_rows = 0
total_batches = 0

with zipfile.ZipFile(ARCHIVE) as archive:
    members = sorted(
        name for name in archive.namelist()
        if name.endswith(".csv")
    )

    for member in members:
        part_rows = 0
        part_batches = 0
        output_file = None

        try:
            with archive.open(member) as binary:
                with io.TextIOWrapper(
                    binary, encoding="utf-8-sig", newline=""
                ) as text:
                    reader = csv.reader(text)
                    header = next(reader)

                    for row in reader:
                        if part_rows % BATCH_SIZE == 0:
                            if output_file:
                                output_file.close()

                            part_batches += 1
                            filename = (
                                f"{Path(member).stem}"
                                f"-batch-{part_batches:03d}.csv"
                            )
                            output_file = open(
                                OUTPUT / filename,
                                "w",
                                newline="",
                                encoding="utf-8"
                            )
                            writer = csv.writer(output_file)
                            writer.writerow(header)

                        writer.writerow(row)
                        part_rows += 1
        finally:
            if output_file:
                output_file.close()

        total_rows += part_rows
        total_batches += part_batches
        print(f"{member}: {part_rows:,} rows, {part_batches} batches")

print(f"\nTOTAL: {total_rows:,} rows in {total_batches} batches")

if total_rows != 3_860_371:
    raise SystemExit("WARNING: Row count differs from expected dataset!")
