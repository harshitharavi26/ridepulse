
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

SOURCES = {
    "nyc": "https://gbfs.citibikenyc.com/gbfs/gbfs.json",
    "boulder": "https://gbfs.bcycle.com/bcycle_boulder/gbfs.json",
}


def fetch_json(url):
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.json()


def collect(city, discovery_url):
    # Find station endpoints from the GBFS discovery feed
    discovery = fetch_json(discovery_url)
    data = discovery["data"]

    feeds = data.get("feeds")
    if feeds is None:
        feeds = data.get("en", {}).get("feeds", [])

    feed_urls = {
        feed["name"]: feed["url"]
        for feed in feeds
    }

    # Collect both station metadata and current status
    datasets = {}
    for feed_type in ("station_information", "station_status"):
        datasets[feed_type] = fetch_json(feed_urls[feed_type])

        if not isinstance(
            datasets[feed_type]["data"]["stations"], list
        ):
            raise ValueError(f"Invalid {feed_type} response")

    # Timestamp representing when we collected the snapshot
    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    output_dir = ROOT / "data" / "raw" / "gbfs" / city
    output_dir.mkdir(parents=True, exist_ok=True)

    for feed_type, payload in datasets.items():
        output_path = output_dir / f"{timestamp}_{feed_type}.json"

        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(payload, file)

        print(f"Saved: {output_path.relative_to(ROOT)}")

    station_count = len(
        datasets["station_status"]["data"]["stations"]
    )

    print(f"{city}: {station_count} stations collected")
    print(
        "Source last updated:",
        datasets["station_status"].get("last_updated")
    )


def main():
    successful = 0

    for city, url in SOURCES.items():
        try:
            print(f"\nCollecting {city}...")
            collect(city, url)
            successful += 1
        except (requests.RequestException, KeyError, ValueError) as error:
            print(f"Failed to collect {city}: {error}")

    if successful == 0:
        raise SystemExit("No feeds collected successfully")


if __name__ == "__main__":
    main()
