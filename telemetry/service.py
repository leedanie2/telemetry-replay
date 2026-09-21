"""Transactional replay and SQL reporting."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import logging
from pathlib import Path
from typing import List

from psycopg.rows import dict_row

from .io import Reading

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
LOG = logging.getLogger("telemetry")


@dataclass(frozen=True)
class ReplayCounts:
    inserted: int
    duplicates: int
    conflicts: int


def ingest(connection, readings: List[Reading]) -> ReplayCounts:
    """One transaction per file. Exact retries ignore arrival time.

    The first committed reading wins under concurrent writers, not necessarily
    the first network arrival. PostgreSQL resolves uniqueness conflicts; the
    separate SELECT gets a fresh READ COMMITTED snapshot after a competing commit.
    The service is append-only. External concurrent updates/deletes are unsupported.
    """
    inserted = duplicates = conflicts = 0
    with connection.transaction():
        for reading in readings:
            key = (reading.device_id, reading.boot_id, reading.sequence)
            result = connection.execute(
                "INSERT INTO measurements "
                "(device_id, boot_id, sequence, event_time, energy_uj) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (device_id, boot_id, sequence) DO NOTHING RETURNING sequence",
                (*key, reading.event_time, reading.energy_uj),
            ).fetchone()
            if result is not None:
                inserted += 1
                continue
            stored = connection.execute(
                "SELECT event_time, energy_uj FROM measurements "
                "WHERE device_id = %s AND boot_id = %s AND sequence = %s", key,
            ).fetchone()
            if stored is None:
                raise RuntimeError("Reading disappeared; concurrent deletion is unsupported")
            if stored == (reading.event_time, reading.energy_uj):
                duplicates += 1
            else:
                conflicts += 1
                connection.execute(
                    "INSERT INTO measurement_conflicts "
                    "(device_id, boot_id, sequence, incoming_event_time, incoming_energy_uj) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (*key, reading.event_time, reading.energy_uj),
                )
    if conflicts:
        LOG.warning("%d conflicting deliveries; retained original readings and audited payloads", conflicts)
    return ReplayCounts(inserted, duplicates, conflicts)


def report(connection, device_id=None) -> list:
    """Fetch SQL results; exact decimal quantities are serialized as strings."""
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            (SQL_DIR / "report.sql").read_text(),
            {"device_id": device_id},
        ).fetchall()
    return [{key: value.isoformat() if isinstance(value, datetime)
             else str(value) if isinstance(value, Decimal) else value
             for key, value in row.items()} for row in rows]


def initialize(connection):
    """One-time schema creation; a repeat invocation fails without resetting data."""
    with connection.transaction():
        connection.execute((SQL_DIR / "001-schema.sql").read_text())


def register_device(connection, device_id, label):
    connection.execute("INSERT INTO devices (device_id, label) VALUES (%s, %s)",
                       (device_id, label))
