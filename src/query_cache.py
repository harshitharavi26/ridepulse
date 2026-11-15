"""Optional versioned Redis query cache. SQL remains the source of truth."""
import hashlib
import json
import logging
import os

from fastapi.encoders import jsonable_encoder
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

logger = logging.getLogger("ridepulse.cache")


class QueryCache:
    def __init__(self):
        self.bypass=os.getenv("RIDEPULSE_CACHE_BYPASS","false").lower()=="true"
        self.schema=os.getenv("DB_SCHEMA","public")
        url = os.getenv("REDIS_URL")
        self.client = redis.Redis.from_url(url, socket_connect_timeout=0.25,
                                          socket_timeout=0.25, decode_responses=True,
                                          max_connections=16,
                                          retry=Retry(NoBackoff(), 0)) if url else None

    def close(self):
        if self.client:
            self.client.close()

    def status(self):
        if not self.client:
            return "not_configured"
        try:
            self.client.ping()
            return "available"
        except redis.RedisError:
            return "unavailable"

    def execute(self, conn, city, domains, endpoint, params, ttl, build):
        if not self.client:
            payload = build()
            payload["meta"]["cache"] = "disabled"
            return payload
        versions = conn.execute("""SELECT domain, version, committed_at FROM dataset_versions
            WHERE city = %s AND domain = ANY(%s) ORDER BY domain""", [city.value, domains]).fetchall()
        if len(versions) != len(domains):
            # Do not cache without a complete durable version set.
            payload = build()
            payload["meta"]["cache"] = "unversioned"
            return payload
        if self.bypass:
            payload=build()
            payload["meta"].update(dataset_versions=jsonable_encoder(versions),cache="bypass")
            return payload
        identity = jsonable_encoder(dict(city=city.value, versions=versions,
                                         database={"host":conn.info.host,"database":conn.info.dbname,"user":conn.info.user,"schema":self.schema},
                                         endpoint=endpoint, params=params))
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = f"ridepulse:api-v3:{digest}"
        state = "miss"
        try:
            cached = self.client.get(key)
            if cached:
                payload = json.loads(cached)
                if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("Invalid cached response")
                payload["meta"]["cache"] = "hit"
                logger.info("cache_hit", extra={"json_fields": {"endpoint": endpoint}})
                return payload
        except (redis.RedisError, ValueError, TypeError):
            state = "fallback"
        payload = build()
        payload["meta"].update(dataset_versions=jsonable_encoder(versions), cache=state)
        if state != "fallback":
            try:
                encoded = json.dumps(jsonable_encoder(payload), separators=(",", ":"))
                if len(encoded) <= 1_000_000:
                    self.client.setex(key, ttl, encoded)
            except redis.RedisError:
                payload["meta"]["cache"] = "fallback"
        logger.info("cache_result", extra={"json_fields": {"endpoint": endpoint, "result": payload["meta"]["cache"]}})
        return payload
