"""CSV parsing only: preserve arrival order; never deduplicate or compute energy."""

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List

FIELDS = ["device_id", "boot_id", "sequence", "event_time", "energy_uj"]


@dataclass(frozen=True)
class Reading:
    device_id: str
    boot_id: str
    sequence: int
    event_time: datetime
    energy_uj: int


def read_csv(path: Path) -> List[Reading]:
    return parse_csv(path.read_bytes())


def parse_csv(payload: bytes) -> List[Reading]:
    """Parse an immutable snapshot so benchmark hash matches ingested content."""
    readings = []
    with io.StringIO(payload.decode("utf-8"), newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise ValueError("CSV header must be: " + ",".join(FIELDS))
        for line, row in enumerate(reader, start=2):
            try:
                if None in row or any(value is None or not value.strip() for value in row.values()):
                    raise ValueError("missing/extra fields or blank value")
                stamp = datetime.fromisoformat(row["event_time"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("event_time needs an explicit UTC offset")
                sequence, energy = int(row["sequence"]), int(row["energy_uj"])
                if not (0 <= sequence <= 2**63 - 1 and 0 <= energy <= 2**63 - 1):
                    raise ValueError("sequence and energy_uj must fit nonnegative BIGINT")
                readings.append(Reading(row["device_id"], row["boot_id"], sequence,
                                        stamp.astimezone(timezone.utc), energy))
            except (ValueError, OverflowError) as exc:
                raise ValueError("CSV line {}: {}".format(line, exc)) from exc
    return readings
