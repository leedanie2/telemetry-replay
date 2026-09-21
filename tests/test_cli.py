"""CLI and benchmark contract checks. Real database checks are in test_integration.py."""

from contextlib import redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.generate_load import generate
from telemetry.__main__ import main, validate_counts
from telemetry.io import read_csv
from telemetry.service import ReplayCounts

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_arrival_order_duplicates_and_precision_preserved(self):
        rows = read_csv(ROOT / "fixtures/readings.csv")
        self.assertEqual([r.sequence for r in rows], [0, 1, 1, 3, 2, 0, 1])
        self.assertEqual(rows[1], rows[2])
        self.assertEqual(rows[0].energy_uj, 1_000_000_000)

    def test_timezone_normalization_and_integer_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.csv"
            path.write_text("device_id,boot_id,sequence,event_time,energy_uj\n"
                            "A,b,0,2026-09-19T01:00:00+01:00,9223372036854775807\n")
            row = read_csv(path)[0]
            self.assertEqual(row.event_time.isoformat(), "2026-09-19T00:00:00+00:00")
            self.assertEqual(row.energy_uj, 2**63 - 1)

    def test_bad_inputs_fail_before_any_database_call(self):
        bad_rows = [
            "A,b,0,2026-09-19T00:00:00,1",       # no timezone
            "A,b,0,2026-09-19T00:00:00Z,",      # blank counter
            "A,b,0,2026-09-19T00:00:00Z,-1",
            "A,b,0,2026-09-19T00:00:00Z,1.5",
            "A,b,0,2026-09-19T00:00:00Z,9223372036854775808",
            "A,b,0,2026-09-19T00:00:00Z,1,extra",
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.csv"
            for row in bad_rows:
                with self.subTest(row=row):
                    path.write_text("device_id,boot_id,sequence,event_time,energy_uj\n" + row + "\n")
                    with patch("telemetry.__main__.connect") as connect:
                        self.assertEqual(main(["replay", str(path)]), 2)
                        connect.assert_not_called()

    def test_inspect_is_read_only(self):
        output = io.StringIO()
        with patch("telemetry.__main__.connect") as connect, redirect_stdout(output):
            self.assertEqual(main(["inspect", str(ROOT / "fixtures/readings.csv")]), 0)
            connect.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["input_rows"], 7)

    def test_service_errors_fail_loudly(self):
        for command, hook in [("replay", "ingest"), ("report", "report")]:
            argv = [command]
            if command == "replay":
                argv.append(str(ROOT / "fixtures/readings.csv"))
            with patch("telemetry.__main__.connect"), patch(
                "telemetry.service." + hook, side_effect=NotImplementedError("injected service failure")
            ), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(argv), 3)
                self.assertEqual(output.getvalue(), "")

    def test_failed_benchmark_creates_no_success_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.json"
            with patch("telemetry.__main__.connect"), patch(
                "telemetry.service.ingest", side_effect=NotImplementedError("injected service failure")
            ), redirect_stdout(io.StringIO()) as stdout:
                code = main(["benchmark", str(ROOT / "fixtures/readings.csv"),
                             "--output", str(output), "--state", "empty", "--notes", "test"])
            self.assertEqual(code, 3)
            self.assertFalse(output.exists())
            self.assertEqual(stdout.getvalue(), "")

    def test_benchmark_records_only_returned_counts_and_refuses_overwrite(self):
        # Synthetic collaborators exercise the recorder, not SQL correctness.
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.json"
            argv = ["benchmark", str(ROOT / "fixtures/readings.csv"),
                    "--output", str(output), "--state", "empty", "--notes", "unit test only"]
            with patch("telemetry.__main__.connect") as connect, patch(
                "telemetry.service.ingest", return_value=ReplayCounts(6, 1, 0)
            ) as ingest, patch("telemetry.service.report", return_value=[{"sample": 1}]), \
                    redirect_stdout(io.StringIO()):
                connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = ["test DB"]
                self.assertEqual(main(argv), 0)
                first_output = output.read_bytes()
                self.assertEqual(main(argv), 2)
                self.assertEqual(output.read_bytes(), first_output)
                ingest.assert_called_once()
            result = json.loads(first_output)
            self.assertEqual(result["input_rows"], 7)
            self.assertEqual(result["inserted"], 6)
            self.assertEqual(result["report_rows"], 1)
            self.assertFalse(result["correctness_verified"])
            self.assertEqual(len(result["input_sha256"]), 64)
            for key in ("parse_seconds", "replay_seconds", "query_seconds"):
                self.assertGreaterEqual(result[key], 0)

    def test_counts_must_cover_all_rows(self):
        with self.assertRaises(ValueError):
            validate_counts({"inserted": 3}, 3)
        with self.assertRaises(ValueError):
            validate_counts(ReplayCounts(2, 0, 0), 3)
        with self.assertRaises(ValueError):
            validate_counts(ReplayCounts(-1, 4, 0), 3)
        self.assertEqual(validate_counts(ReplayCounts(2, 1, 0), 3)["duplicates"], 1)

    def test_benchmark_hash_tracks_parsed_input_even_if_file_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.csv", Path(folder) / "result.json"
            payload = (ROOT / "fixtures/readings.csv").read_bytes()
            source.write_bytes(payload)

            def fake_ingest(connection, readings):
                source.write_text("changed after parsing")
                return ReplayCounts(6, 1, 0)

            with patch("telemetry.__main__.connect") as connect, patch(
                "telemetry.service.ingest", side_effect=fake_ingest
            ), patch("telemetry.service.report", return_value=[]), redirect_stdout(io.StringIO()):
                connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = ["test DB"]
                self.assertEqual(main(["benchmark", str(source), "--output", str(output),
                                       "--state", "empty", "--notes", "unit test only"]), 0)
            self.assertEqual(json.loads(output.read_text())["input_sha256"],
                             hashlib.sha256(payload).hexdigest())

    def test_cases_match_static_ingestion_expectations(self):
        # Independent fixture sanity check, not production deduplication logic.
        cases = json.loads((ROOT / "fixtures/cases/expected.json").read_text())["cases"]
        for name, expected in cases.items():
            with self.subTest(case=name):
                rows = read_csv(ROOT / "fixtures/cases" / (name + ".csv"))
                self.assertEqual(len(rows), expected["input_rows"])
                seen = {}
                for phase in ("first_pass", "second_pass"):
                    counts = dict(inserted=0, duplicates=0, conflicts=0)
                    for row in rows:
                        key = (row.device_id, row.boot_id, row.sequence)
                        if key not in seen:
                            seen[key] = row
                            counts["inserted"] += 1
                        elif seen[key] == row:
                            counts["duplicates"] += 1
                        else:
                            counts["conflicts"] += 1
                    self.assertEqual(counts, expected[phase])
                self.assertEqual(len(seen), expected["stored_rows"])

    def test_generator_repeatability_and_expected_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "a.csv", Path(folder) / "b.csv"
            expected = generate(a, 100)
            generate(b, 100)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(len(read_csv(a)), 111)
            self.assertEqual(expected["expected_first_pass"],
                             {"inserted": 100, "duplicates": 10, "conflicts": 1})
            with self.assertRaises(FileExistsError):
                generate(a, 100)


if __name__ == "__main__":
    unittest.main()
