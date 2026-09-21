"""Repeatable benchmark for scripts/generate_load.py output; isolated schemas.

Run from project root: python -m scripts.measure data/generated/load.csv ...
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
from time import perf_counter
from uuid import uuid4

from psycopg import sql

from telemetry.db import connect
from telemetry.io import parse_csv
from telemetry.service import ingest, initialize, register_device, report


def timed(call):
    start = perf_counter()
    result = call()
    return result, perf_counter() - start


def measure(source, output, base_rows, repeats, hardware):
    if output.exists():
        raise ValueError("Choose a new output path; existing results are never overwritten")
    if base_rows < 30000 or repeats < 1:
        raise ValueError("Use at least 30000 base rows and at least one repetition")
    start = perf_counter()
    payload = source.read_bytes()
    readings = parse_csv(payload)
    parse_seconds = perf_counter() - start
    expected_first = {"inserted": base_rows, "duplicates": base_rows // 10,
                      "conflicts": base_rows // 100}
    expected_second = {"inserted": 0, "duplicates": base_rows + base_rows // 10,
                       "conflicts": base_rows // 100}
    if len(readings) != sum(expected_first.values()):
        raise ValueError("Input count does not match the specified generator configuration")
    runs = []
    with connect() as connection:
        version = connection.execute("SELECT version()").fetchone()[0]
        for iteration in range(repeats):
            schema = "bench_" + uuid4().hex
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            try:
                connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                initialize(connection)
                for i in range(5):
                    register_device(connection, "load-{}".format(i), "Synthetic load device")
                first, first_s = timed(lambda: ingest(connection, readings))
                second, second_s = timed(lambda: ingest(connection, readings))
                rows, report_s = timed(lambda: report(connection))
                stored = connection.execute("SELECT count(*) FROM measurements").fetchone()[0]
                audited = connection.execute("SELECT count(*) FROM measurement_conflicts").fetchone()[0]
                if asdict(first) != expected_first or asdict(second) != expected_second:
                    raise ValueError("Replay counts failed independent generator expectations")
                if stored != base_rows or audited != base_rows // 100 or len(rows) != base_rows:
                    raise ValueError("Stored/audit/report counts failed")
                for row in rows:
                    if row["quality"] == "first_sample":
                        if row["energy_j"] is not None:
                            raise ValueError("Boot boundary incorrectly assigned energy")
                    elif row["quality"] != "ok" or Decimal(row["energy_j"]) != 12 or Decimal(row["average_power_w"]) != Decimal("2.4"):
                        raise ValueError("Generated constant-power interval check failed")

                connection.execute("ANALYZE measurements")
                query = ("SELECT event_time, energy_uj FROM measurements "
                         "WHERE device_id=%s AND boot_id=%s AND event_time >= %s AND event_time < %s "
                         "ORDER BY event_time, sequence")
                epoch = datetime(2026, 9, 19, tzinfo=timezone.utc)
                params = ("load-3", "boot-5", epoch + timedelta(seconds=28000), epoch + timedelta(seconds=28500))
                connection.execute("DROP INDEX measurements_event_order")
                baseline_rows = connection.execute(query, params).fetchall()
                before = connection.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, params).fetchone()[0][0]
                connection.execute("CREATE INDEX measurements_event_order ON measurements (device_id,boot_id,event_time,sequence)")
                indexed_rows = connection.execute(query, params).fetchall()
                after = connection.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, params).fetchone()[0][0]
                if baseline_rows != indexed_rows or len(indexed_rows) != 100:
                    raise ValueError("Index comparison changed answers or fixture selection")
                runs.append({"iteration": iteration + 1, "first_replay_seconds": first_s,
                             "second_replay_seconds": second_s, "report_fetch_seconds": report_s,
                             "first_counts": asdict(first), "second_counts": asdict(second),
                             "stored_rows": stored, "audited_payloads": audited,
                             "report_rows": len(rows), "index_baseline_plan": before,
                             "index_added_plan": after, "correctness_checks_passed": True})
                print("Repetition {}: first {:.3f}s; repeated {:.3f}s; report {:.3f}s".format(
                    iteration + 1, first_s, second_s, report_s), flush=True)
            finally:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
        "input_sha256": hashlib.sha256(payload).hexdigest(), "input_rows": len(readings),
        "base_rows": base_rows, "hardware": hardware,
        "python": platform.python_version(), "platform": platform.platform(), "postgresql": version,
        "parse_once_seconds": parse_seconds,
        "conditions": "Fresh isolated schema per repetition; reused running server/OS caches, no cold-cache claim. "
                      "One transaction per replay. Timers include DB commit for replay and fetch/conversion for report. "
                      "Parsing, connections, schema setup, hashing, JSON writing excluded from replay/report times. "
                      "Index comparison uses warmed range query; primary-key index remains in baseline.",
        "runs": runs,
        "median_seconds": {field: statistics.median(run[field] for run in runs)
                           for field in ("first_replay_seconds", "second_replay_seconds", "report_fetch_seconds")},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-rows", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--hardware", required=True)
    args = parser.parse_args()
    measure(args.csv, args.output, args.base_rows, args.repeats, args.hardware)
