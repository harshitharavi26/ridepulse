"""Rebuild serving aggregates atomically after the owner migrations are applied."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import aggregates
from cloud_support import database


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", choices=["public","ridepulse_benchmark"], default="public")
    args = parser.parse_args()
    start = time.monotonic()
    with database(args.schema) as conn:
        aggregates.refresh(conn)
        print("Aggregate state:",conn.execute("SELECT * FROM aggregate_state ORDER BY city").fetchall())
    print(f"Committed aggregate refresh in {time.monotonic()-start:.2f}s")
