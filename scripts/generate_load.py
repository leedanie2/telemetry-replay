"""Deterministic fixture generator, not an ingestion implementation."""

import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def generate(destination: Path, count: int):
    """Every 10th base reading is retried; every 100th also has a conflict.

    Five devices alternate. Each device reboots every 1000 base readings.
    No random numbers: identical arguments yield identical CSV bytes.
    """
    if count < 1:
        raise ValueError("base row count must be positive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 9, 19, tzinfo=timezone.utc)
    with destination.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["device_id", "boot_id", "sequence", "event_time", "energy_uj"])
        for index in range(count):
            per_device = index // 5
            sequence = per_device % 1000
            row = ["load-{}".format(index % 5), "boot-{}".format(per_device // 1000),
                   sequence, (start + timedelta(seconds=index)).isoformat(),
                   sequence * 12_000_000]
            writer.writerow(row)
            if (index + 1) % 10 == 0:
                writer.writerow(row)
            if (index + 1) % 100 == 0:
                conflict = row.copy()
                conflict[-1] += 1
                writer.writerow(conflict)
    return {"base_rows": count, "input_rows": count + count // 10 + count // 100,
            "expected_first_pass": {"inserted": count, "duplicates": count // 10,
                                    "conflicts": count // 100},
            "expected_second_pass": {"inserted": 0, "duplicates": count + count // 10,
                                     "conflicts": count // 100}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-rows", type=int, default=100_000)
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.base_rows), indent=2))
