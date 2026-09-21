"""Connection plumbing; no application schema or ingestion SQL."""

import os
from pathlib import Path

import psycopg


def connect():
    # DATABASE_URL overrides local defaults. Never print credentials or the DSN.
    dsn = os.environ.get("DATABASE_URL")
    options = {} if dsn else {
        "host": str(Path(__file__).resolve().parents[1] / ".local" / "socket"),
        "port": 55432,
        "dbname": "telemetry",
        "user": "telemetry",
    }
    # Ingestion establishes an explicit transaction for each replay batch.
    return psycopg.connect(dsn or "", autocommit=True, connect_timeout=5, **options)
