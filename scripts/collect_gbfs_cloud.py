import os

import json
from datetime import datetime, timezone

required = ("GOOGLE_CLOUD_PROJECT", "RIDEPULSE_BUCKET")
missing = [key for key in required if not os.getenv(key, "").strip()]
if missing:
    raise RuntimeError("Missing required configuration: " + ", ".join(missing))

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
BUCKET_NAME = os.environ["RIDEPULSE_BUCKET"]

import requests
from google.cloud import storage, pubsub_v1

SOURCES = {
    "nyc": "https://gbfs.citibikenyc.com/gbfs/gbfs.json",
    "boulder": "https://gbfs.bcycle.com/bcycle_boulder/gbfs.json",
}

storage_client = storage.Client(project=PROJECT_ID)


def fetch_json(url):
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.json()


def collect_city(city, discovery_url):
    discovery = fetch_json(discovery_url)

    data = discovery["data"]
    feeds = data.get("feeds") or data.get("en", {}).get("feeds", [])

    feed_urls = {
        feed["name"]: feed["url"]
        for feed in feeds
    }

    collected_at = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    bucket = storage_client.bucket(BUCKET_NAME)

    for feed_type in (os.getenv("GBFS_FEED_TYPE", "station_status"),):
        payload = fetch_json(feed_urls[feed_type])

        stations = payload["data"]["stations"]
        if not isinstance(stations, list):
            raise ValueError(f"Invalid {feed_type} response")

        object_name = (
            f"raw/gbfs/{city}/"
            f"{collected_at}_{feed_type}.json"
        )

        blob = bucket.blob(object_name)
        blob.upload_from_string(
            json.dumps(payload),
            content_type="application/json"
        )

        print(
            f"Uploaded {city} {feed_type}: "
            f"{len(stations)} stations"
        )
        print(f"gs://{BUCKET_NAME}/{object_name}")

        if os.getenv("GBFS_PUBLISH_TASKS", "false").lower() == "true":
            task = {
                "task_type": feed_type,
                "city": city,
                "bucket": BUCKET_NAME,
                "object": object_name,
            }

            publisher = pubsub_v1.PublisherClient()
            topic = publisher.topic_path(PROJECT_ID, "ridepulse-tasks")

            message_id = publisher.publish(
                topic,
                json.dumps(task).encode("utf-8")
            ).result(timeout=30)

            print(
                f"Published {city}/{feed_type} "
                f"processing task: {message_id}",
                flush=True
            )
        else:
            print("Pub/Sub publishing disabled", flush=True)


def main():
    failures = []

    for city, url in SOURCES.items():
        try:
            print(f"\nCollecting {city}...")
            collect_city(city, url)
        except Exception as error:
            print(f"Failed to collect {city}: {error}")
            failures.append(city)

    if failures:
        raise SystemExit(
            f"Collection failed for: {', '.join(failures)}"
        )

    print("\nAll snapshots uploaded successfully!")


if __name__ == "__main__":
    main()
