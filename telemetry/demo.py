"""Executable correctness demo, isolated from existing application tables."""

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from psycopg import sql

from .io import read_csv
from .service import ingest, initialize, register_device, report


def demo(connection):
    schema = "demo_" + uuid4().hex
    # All demo DDL/data is rolled back, including if an assertion fails.
    with connection.transaction(force_rollback=True):
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        initialize(connection)
        register_device(connection, "A", "Synthetic energy meter")
        root = Path(__file__).resolve().parents[1]
        readings = read_csv(root / "fixtures/readings.csv")
        first, second = ingest(connection, readings), ingest(connection, readings)
        rows = report(connection)
        subtotal = sum(Decimal(row["energy_j"]) for row in rows if row["energy_j"] is not None)
        if (asdict(first), asdict(second), len(rows), subtotal) != (
            {"inserted": 6, "duplicates": 1, "conflicts": 0},
            {"inserted": 0, "duplicates": 7, "conflicts": 0}, 6, Decimal(51)
        ):
            raise ValueError("Demo results differ from independently calculated expectations")
        conflict = ingest(connection, read_csv(root / "fixtures/cases/conflict.csv"))
        if asdict(conflict) != {"inserted": 0, "duplicates": 2, "conflicts": 1}:
            raise ValueError("Conflict demo failed")
        return {"demo_passed": True, "first_replay": asdict(first),
                "second_replay": asdict(second), "conflicting_replay": asdict(conflict),
                "valid_interval_subtotal_j": str(subtotal),
                "full_period_total_j": None,
                "limitation": "Energy across the reboot boundary is unknown",
                "report": report(connection), "persistent_data_changed": False}
