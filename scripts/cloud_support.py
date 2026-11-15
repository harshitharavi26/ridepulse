"""CLI-only helpers: explicit university identity, no credential files or stdout."""
import os
import re
import subprocess

import psycopg

PROJECT = "ridepulse-dcs-2026"
ACCOUNT = "shpa6505@colorado.edu"
BUCKET = "ridepulse-dcs-2026-data-42502534899"
GCLOUD = ["gcloud", f"--account={ACCOUNT}", f"--project={PROJECT}", f"--billing-project={PROJECT}"]


def gcloud(*args):
    return subprocess.check_output([*GCLOUD, *args], text=True).strip()


def token(identity=False):
    return gcloud("auth", "print-identity-token" if identity else "print-access-token")


def database(schema="public"):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Invalid schema")
    password = gcloud("secrets", "versions", "access", "latest", "--secret=ridepulse-db-password")
    return psycopg.connect(host="127.0.0.1", port=int(os.getenv("CLOUD_SQL_PROXY_PORT", "5434")),
                           dbname="ridepulse", user="ridepulse_app", password=password,
                           connect_timeout=15, options="-c search_path=" + schema + " -c timezone=UTC")
