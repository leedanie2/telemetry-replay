"""Real PostgreSQL checks. Set TELEMETRY_INTEGRATION=1 to run.

Each test creates/drops its own randomly named schema. Application data is untouched.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from threading import Barrier
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

from telemetry.db import connect
from telemetry.demo import demo
from telemetry.io import Reading, read_csv
from telemetry.service import ingest, initialize, register_device, report

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("TELEMETRY_INTEGRATION") == "1", "PostgreSQL integration opt-in")
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect()
        self.schema = "test_" + uuid4().hex
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        initialize(self.connection)
        register_device(self.connection, "A", "Fixture device A")
        register_device(self.connection, "B", "Fixture device B")

    def tearDown(self):
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.connection.close()

    def count(self, table="measurements"):
        return self.connection.execute(sql.SQL("SELECT count(*) FROM {}").format(
            sql.Identifier(table))).fetchone()[0]

    def reading(self):
        return Reading("A", "b1", 0, datetime(2026, 9, 19, tzinfo=timezone.utc), 1000000000)

    def test_all_fixture_counts_and_repeat_replays(self):
        cases = json.loads((ROOT / "fixtures/cases/expected.json").read_text())["cases"]
        for name, expected in cases.items():
            with self.subTest(case=name), self.connection.transaction(force_rollback=True):
                readings = read_csv(ROOT / "fixtures/cases" / (name + ".csv"))
                self.assertEqual(asdict(ingest(self.connection, readings)), expected["first_pass"])
                self.assertEqual(asdict(ingest(self.connection, readings)), expected["second_pass"])
                self.assertEqual(self.count(), expected["stored_rows"])

    def test_baseline_intervals_and_no_cross_boot_total(self):
        ingest(self.connection, read_csv(ROOT / "fixtures/readings.csv"))
        rows = report(self.connection)
        valid = [row for row in rows if row["energy_j"] is not None]
        self.assertEqual([Decimal(row["energy_j"]) for row in valid], [12, 13, 14, 12])
        self.assertEqual([Decimal(row["average_power_w"]) for row in valid], [12, 13, 14, 12])
        self.assertEqual([row["quality"] for row in rows].count("first_sample"), 2)
        self.assertTrue(all(row["energy_j"] is None for row in rows if row["quality"] == "first_sample"))

    def test_optional_device_filter_preserves_intervals(self):
        ingest(self.connection, read_csv(ROOT / "fixtures/cases/device_isolation.csv"))
        all_rows = report(self.connection)
        self.assertEqual(len(all_rows), 4)
        for device_id in ("A", "B"):
            with self.subTest(device=device_id):
                self.assertEqual(report(self.connection, device_id=device_id),
                                 [row for row in all_rows if row["device_id"] == device_id])
        self.assertEqual(report(self.connection, device_id=None), all_rows)
        for device_id in ("unknown", "", "A' OR TRUE --"):
            with self.subTest(device=device_id):
                self.assertEqual(report(self.connection, device_id=device_id), [])

    def test_gap_and_late_arrival_recompute(self):
        rows = read_csv(ROOT / "fixtures/cases/out_of_order.csv")
        ingest(self.connection, rows[:2])
        last = report(self.connection)[-1]
        self.assertEqual(last["quality"], "gap")
        self.assertEqual(Decimal(last["average_power_w"]), Decimal("12.5"))
        ingest(self.connection, rows[2:])
        self.assertEqual([row["quality"] for row in report(self.connection)], ["first_sample", "ok", "ok"])

    def test_invalid_intervals_never_produce_power(self):
        for name, quality in [("equal_time", "zero_duration"), ("decreasing_counter", "decreasing_counter")]:
            with self.subTest(case=name), self.connection.transaction(force_rollback=True):
                ingest(self.connection, read_csv(ROOT / "fixtures/cases" / (name + ".csv")))
                last = report(self.connection)[-1]
                self.assertEqual(last["quality"], quality)
                self.assertIsNone(last["energy_j"])
                self.assertIsNone(last["average_power_w"])

    def test_sequence_reversal_and_device_isolation(self):
        a = self.reading()
        b = replace(a, sequence=1, event_time=datetime(2026, 9, 19, 0, 0, 1, tzinfo=timezone.utc), energy_uj=a.energy_uj + 12000000)
        ingest(self.connection, [replace(a, sequence=2), b])
        self.assertEqual(report(self.connection)[-1]["quality"], "sequence_reversal")
        self.assertIsNone(report(self.connection)[-1]["average_power_w"])
        with self.connection.transaction(force_rollback=True):
            self.connection.execute("TRUNCATE measurement_conflicts, measurements")
            ingest(self.connection, read_csv(ROOT / "fixtures/cases/device_isolation.csv"))
            valid = [r for r in report(self.connection) if r["energy_j"] is not None]
            self.assertEqual([(r["device_id"], Decimal(r["energy_j"])) for r in valid], [("A", 12), ("B", 20)])

    def test_conflict_is_durable_distinct_and_original_unchanged(self):
        readings = read_csv(ROOT / "fixtures/cases/conflict.csv")
        ingest(self.connection, readings)
        ingest(self.connection, readings)
        self.assertEqual(self.count("measurement_conflicts"), 1)
        stored = self.connection.execute("SELECT energy_uj FROM measurements WHERE sequence=1").fetchone()[0]
        self.assertEqual(stored, 1012000000)
        self.assertTrue(report(self.connection)[-1]["has_conflicting_payload"])
        self.assertEqual(Decimal(report(self.connection)[-1]["energy_j"]), 12)

    def test_changed_timestamp_is_conflict_not_duplicate(self):
        reading = self.reading()
        changed = replace(reading, event_time=datetime(2026, 9, 19, 0, 0, 1, tzinfo=timezone.utc))
        self.assertEqual(asdict(ingest(self.connection, [reading, changed])),
                         {"inserted": 1, "duplicates": 0, "conflicts": 1})

    def test_database_constraints_without_parser(self):
        statement = "INSERT INTO measurements (device_id,boot_id,sequence,event_time,energy_uj) VALUES (%s,%s,%s,%s,%s)"
        valid = ["A", "b1", 0, self.reading().event_time, 1]
        for index, bad, error in [(0,"unknown",psycopg.errors.ForeignKeyViolation),
                                  (1," ",psycopg.errors.CheckViolation),
                                  (2,-1,psycopg.errors.CheckViolation),
                                  (3,"infinity",psycopg.errors.CheckViolation),
                                  (4,-1,psycopg.errors.CheckViolation),
                                  (4,None,psycopg.errors.NotNullViolation)]:
            values = valid.copy()
            values[index] = bad
            with self.subTest(index=index, bad=bad), self.assertRaises(error):
                with self.connection.transaction():
                    self.connection.execute(statement, values)
        self.assertEqual(self.count(), 0)

    def test_failed_batch_rolls_back_readings_and_audit(self):
        reading = self.reading()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            ingest(self.connection, [reading, replace(reading, device_id="unknown")])
        self.assertEqual(self.count(), 0)
        ingest(self.connection, [reading])
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            ingest(self.connection, [replace(reading, energy_uj=3), replace(reading, device_id="unknown")])
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.count("measurement_conflicts"), 0)

    def test_retry_after_reconnection_is_idempotent(self):
        ingest(self.connection, [self.reading()])
        with connect() as other:
            other.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            self.assertEqual(asdict(ingest(other, [self.reading()])), {"inserted":0,"duplicates":1,"conflicts":0})

    def test_two_concurrent_writers(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                self.connection.execute("TRUNCATE measurement_conflicts, measurements")
                barrier = Barrier(2)
                def writer(index):
                    with connect() as other:
                        other.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
                        row = self.reading()
                        if changed and index:
                            row = replace(row, energy_uj=row.energy_uj + 1)
                        barrier.wait(timeout=10)
                        return ingest(other, [row])
                with ThreadPoolExecutor(max_workers=2) as pool:
                    counts = list(pool.map(writer, (0, 1)))
                self.assertEqual(sum(c.inserted for c in counts), 1)
                self.assertEqual(sum(c.conflicts for c in counts), int(changed))
                self.assertEqual(sum(c.duplicates for c in counts), int(not changed))
                self.assertEqual(self.count(), 1)

    def test_init_refuses_existing_schema_without_data_loss(self):
        ingest(self.connection, [self.reading()])
        with self.assertRaises(psycopg.errors.DuplicateTable):
            initialize(self.connection)
        self.assertEqual(self.count(), 1)

    def test_isolated_demo_preserves_existing_data(self):
        ingest(self.connection, [self.reading()])
        result = demo(self.connection)
        self.assertTrue(result["demo_passed"])
        self.assertIsNone(result["full_period_total_j"])
        self.assertEqual(self.count(), 1)
