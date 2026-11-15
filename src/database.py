"""Shared connection settings. Importing this module never opens a connection."""
import os
import re


def connection_settings():
    schema = os.getenv("DB_SCHEMA", "public")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise RuntimeError("Invalid DB_SCHEMA")
    options = {"options": "-c search_path=" + schema}
    if os.getenv("DATABASE_URL"):
        return {"conninfo": os.environ["DATABASE_URL"], "connect_timeout": 5, **options}
    required = ("DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required if not os.getenv(key, "").strip()]
    instance = os.getenv("INSTANCE_CONNECTION_NAME", "").strip()
    host = os.getenv("DB_HOST", "").strip()
    if not host and not instance:
        missing.append("DB_HOST or INSTANCE_CONNECTION_NAME")
    if missing:
        raise RuntimeError("Missing database configuration: " + ", ".join(missing))
    return dict(host=host or f"/cloudsql/{instance}",
                port=int(os.getenv("DB_PORT", "5432")),
                dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"], connect_timeout=5, **options)
