from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bench.queue_contention.diagnose import (
    Invocation,
    Variant,
    _compare,
    _execute,
    _digest,
    _invocation,
    _prepare_output,
    _process,
    _perf_report,
    _read_raw,
    _trace,
    _trace_tool,
)
from bench.queue_contention.diagnose_analysis import raw_problems as _raw_problems
from bench.queue_contention.diagnose_analysis import summarize as _summarize
from bench.queue_contention.trial import Condition, Trial

ROOT = Path(__file__).resolve().parents[1]


class QueueDiagnoseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = ROOT / ".local/runs/queue-diagnose-tests" / uuid.uuid4().hex
        self.directory.mkdir(parents=True)

    def test_invalid_worker_metrics_refuse_comparison(self) -> None:
        invalid = (None, True, "0", -1, float("inf"), float("nan"), 10**400)
        for phase in ("preflight", "measured"):
            for field in ("latency_s", "busy", "retry_sleep_s", "error"):
                for value in ("missing", *invalid):
                    if field == "error" and value is None:
                        continue
                    with self.subTest(phase=phase, field=field, value=value):
                        records = self._records()
                        record = next(r for r in records if r["phase"] == phase)
                        worker = record["raw"]["workers"][0]
                        if value == "missing":
                            del worker[field]
                        else:
                            worker[field] = value
                        original = copy.deepcopy(record["raw"])
                        self.assertEqual(
                            _summarize(records, [1], 3)["status"], "incomparable"
                        )
                        self.assertEqual(repr(record["raw"]), repr(original))
            for operation in ("put", "claim", "ack", "outbox", "empty_claim"):
                for value in (
                    None,
                    "samples",
                    [True],
                    [-1],
                    [float("inf")],
                    [float("nan")],
                ):
                    with self.subTest(phase=phase, operation=operation, value=value):
                        records = self._records()
                        record = next(r for r in records if r["phase"] == phase)
                        record["raw"]["workers"][0]["latency_s"][operation] = value
                        self.assertEqual(
                            _summarize(records, [1], 3)["status"], "incomparable"
                        )

    def test_different_conditions_and_wrong_engines_refuse_comparison(self) -> None:
        for phase in ("preflight", "measured"):
            for field, value in (
                ("jobs", 2),
                ("payload_bytes", 512),
                ("workload", "drain"),
                ("engine", "sqlite-wal"),
            ):
                with self.subTest(phase=phase, field=field):
                    records = self._records()
                    record = next(
                        r
                        for r in records
                        if r["phase"] == phase and r["variant"] == "candidate"
                    )
                    if field == "engine":
                        record[field] = record["raw"][field] = value
                    else:
                        record["condition"][field] = value
                        if field == "jobs":
                            raw = record["raw"]
                            raw["completed_jobs"] = raw["audit"]["rows"] = 2
                            raw["audit"]["items"].append([11, 1, "hash2"])
                            raw["workers"][0]["acked"].append([11, 1, 1, "hash2"])
                    self.assertEqual(
                        _summarize(records, [1], 3)["status"], "incomparable"
                    )

    def test_missing_operation_and_boolean_counts_refuse_comparison(self) -> None:
        for phase in ("preflight", "measured"):
            for field in (
                "put",
                "claim",
                "ack",
                "outbox",
                "empty_claim",
                "elapsed_s",
                "completed_jobs",
                "rows",
            ):
                with self.subTest(phase=phase, field=field):
                    records = self._records()
                    raw = next(r["raw"] for r in records if r["phase"] == phase)
                    if field in raw["workers"][0]["latency_s"]:
                        del raw["workers"][0]["latency_s"][field]
                    elif field == "rows":
                        raw["audit"]["rows"] = True
                    else:
                        raw[field] = True
                    self.assertEqual(
                        _summarize(records, [1], 3)["status"], "incomparable"
                    )

    def test_final_library_failure_is_terminal_before_summary_publication(self) -> None:
        for failure in ("missing", "unreadable", "changed"):
            with self.subTest(failure=failure):
                library = self.directory / f"{failure}.so"
                library.write_bytes(b"original")
                args = self._args(library, self.directory / failure)
                args.jobs = 1
                count = 0

                def success(invocation: Invocation) -> dict:
                    nonlocal count
                    count += 1
                    if count == 6:
                        if failure == "missing":
                            library.unlink()
                        elif failure == "changed":
                            library.write_bytes(b"changed")
                    return self._record(invocation.variant.name, invocation.phase, 0, 1)

                def digest(path: Path) -> str:
                    if failure == "unreadable" and count == 6 and path == library:
                        raise PermissionError("fixture unreadable library")
                    return _digest(path)

                with (
                    patch(
                        "bench.queue_contention.diagnose._execute", side_effect=success
                    ),
                    patch(
                        "bench.queue_contention.diagnose._digest", side_effect=digest
                    ),
                ):
                    result = _compare(args)
                self.assertEqual(result["status"], "incomparable")
                self.assertNotIn(
                    "paired_throughput_candidate_over_baseline",
                    result["conditions"]["1"],
                )
                manifest = json.loads((args.output / "manifest.json").read_text())
                self.assertEqual(manifest["status"], "incomparable")
                self.assertIn("finished_at", manifest)
                self.assertIn("reason", manifest)
                self.assertTrue((args.output / "records.json").exists())
                self._verify_hashes(args.output)

    def _verify_hashes(self, output: Path) -> None:
        for line in (output / "SHA256SUMS").read_text().splitlines():
            checksum, name = line.split("  ", 1)
            self.assertEqual(_digest(output / name), checksum)

    def test_collection_exception_finalizes_only_owned_output(self) -> None:
        library = self.directory / "library.so"
        library.write_bytes(b"fixture")
        for index, error in enumerate(
            (
                FileNotFoundError("missing stdout"),
                ValueError("analysis failed"),
                KeyError("workers"),
            )
        ):
            with self.subTest(error=error):
                args = self._args(library, self.directory / f"failure-{index}")

                def fail(invocation: Invocation) -> dict:
                    directory = invocation.spec.evidence_directory
                    (directory / "stdout.txt").write_bytes(b"partial stdout")
                    invocation.spec.directory.mkdir()
                    (invocation.spec.directory / "queue.db").write_bytes(b"original db")
                    raise error

                with patch(
                    "bench.queue_contention.diagnose._execute", side_effect=fail
                ):
                    with self.assertRaises(type(error)):
                        _compare(args)
                manifest_path = args.output / "manifest.json"
                original = manifest_path.read_bytes()
                manifest = json.loads(original)
                self.assertEqual(manifest["status"], "failed")
                self.assertIn("finished_at", manifest)
                self.assertIn(type(error).__name__, manifest["reason"])
                self.assertEqual(
                    next(args.output.rglob("queue.db")).read_bytes(), b"original db"
                )
                self._verify_hashes(args.output)
                with self.assertRaises(FileExistsError):
                    _compare(args)
                self.assertEqual(manifest_path.read_bytes(), original)

    def test_library_check_precedes_comparable_summary_write(self) -> None:
        from bench.queue_contention.diagnose import _write

        library = self.directory / "library.so"
        library.write_bytes(b"fixture")
        args = self._args(library, self.directory / "ordering")
        args.jobs = 1
        checked = False
        initial = True

        def digest(path: Path) -> str:
            nonlocal checked
            if path == library and not initial:
                checked = True
            return _digest(path)

        def success(invocation: Invocation) -> dict:
            nonlocal initial
            initial = False
            return self._record(invocation.variant.name, invocation.phase, 0, 1)

        def write(path: Path, value: object) -> None:
            if path.name == "summary.json":
                self.assertTrue(checked)
            _write(path, value)

        with (
            patch("bench.queue_contention.diagnose._execute", side_effect=success),
            patch("bench.queue_contention.diagnose._digest", side_effect=digest),
            patch("bench.queue_contention.diagnose._write", side_effect=write),
        ):
            self.assertEqual(_compare(args)["status"], "comparable")
        self._verify_hashes(args.output)

    def test_perf_postprocessing_preserves_report_and_script_independently(
        self,
    ) -> None:
        library = self.directory / "library.so"
        library.write_bytes(b"fixture")
        for index, (report_exit, script_exit, empty) in enumerate(
            ((0, 0, False), (1, 0, False), (0, 1, False), (0, 0, True))
        ):
            with self.subTest(
                report_exit=report_exit, script_exit=script_exit, empty=empty
            ):
                invocation = _invocation(
                    self.directory,
                    Variant(str(index), "totedb", library),
                    "perf",
                    0,
                    Condition("mixed", 1, 256, 1),
                    5,
                )

                def process(
                    command: list[str],
                    directory: Path,
                    selected: Path,
                    timeout: float,
                    prefix: str,
                ) -> dict:
                    is_script = command[1] == "script"
                    (directory / f"{prefix}stdout.txt").write_text(
                        ""
                        if empty
                        else ("native callstack" if is_script else "symbols")
                    )
                    (directory / f"{prefix}stderr.txt").write_text("diagnostics")
                    return {
                        "command": command,
                        "exit_code": script_exit if is_script else report_exit,
                        "timed_out": False,
                    }

                with patch(
                    "bench.queue_contention.diagnose._process", side_effect=process
                ) as run:
                    result = _perf_report("/test/perf", invocation)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(
                    result["status"],
                    "ok"
                    if report_exit == script_exit == 0 and not empty
                    else "unavailable",
                )
                directory = invocation.spec.evidence_directory
                for name, code in (("report", report_exit), ("script", script_exit)):
                    saved = json.loads(
                        (directory / f"{name}-process.json").read_bytes()
                    )
                    self.assertEqual(saved["exit_code"], code)
                    self.assertEqual(saved["empty_stdout"], empty)
                self.assertEqual(
                    result["script"]["command"],
                    ["/test/perf", "script", "-i", str(directory / "perf.data")],
                )

    def test_existing_output_is_rejected_without_changing_evidence(self) -> None:
        evidence = self.directory / "stdout.txt"
        evidence.write_text("partial evidence")
        with self.assertRaises(FileExistsError):
            _prepare_output(self.directory)
        self.assertEqual(evidence.read_text(), "partial evidence")

    def test_malformed_stdout_is_preserved_and_rejected(self) -> None:
        for index, content in enumerate(
            (b'{"status":', b"error text", b"[]", b'{"elapsed_s":NaN}')
        ):
            directory = self.directory / str(index)
            directory.mkdir()
            (directory / "stdout.txt").write_bytes(content)
            raw, error = _read_raw(directory)
            self.assertIsNone(raw)
            self.assertIsNotNone(error)
            self.assertEqual((directory / "stdout.txt").read_bytes(), content)

    def test_timeout_kills_descendants_and_preserves_partial_evidence(self) -> None:
        sentinel = self.directory / "child-survived"
        child = f"import time,pathlib;time.sleep(.6);pathlib.Path({str(sentinel)!r}).touch()"
        script = (
            "import subprocess,sys,time,pathlib;"
            f'pathlib.Path({str(self.directory / "queue.db")!r}).write_bytes(b"db");'
            f'subprocess.Popen([sys.executable,"-c",{child!r}]);'
            'print("partial stdout",flush=True);print("partial stderr",file=sys.stderr,flush=True);time.sleep(10)'
        )
        result = _process(
            [sys.executable, "-c", script], self.directory, Path("/test/library"), 0.2
        )
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["exit_code"], -9)
        self.assertEqual(
            (self.directory / "stdout.txt").read_text(), "partial stdout\n"
        )
        self.assertIn("partial stderr", (self.directory / "stderr.txt").read_text())
        self.assertEqual((self.directory / "queue.db").read_bytes(), b"db")
        time.sleep(0.7)
        self.assertFalse(sentinel.exists())

    def test_library_is_explicitly_selected_for_each_process(self) -> None:
        for name in ("baseline", "candidate"):
            directory = self.directory / name
            directory.mkdir()
            library = (self.directory / f"{name}.so").resolve()
            result = _process(
                [sys.executable, "-c", 'import os;print(os.environ["TOTEDB_LIB"])'],
                directory,
                library,
                5,
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(
                (directory / "stdout.txt").read_text().strip(), str(library)
            )

    def test_failed_status_audit_and_id_association_refuse_comparison(self) -> None:
        changes = (
            {"status": "failed"},
            {"audit": {"rows": 1, "problems": ["bad payload"]}},
            {"completed_jobs": 0},
            {"workers": []},
        )
        for changeset in changes:
            record = self._record("baseline", "measured", 0, 1)
            record["raw"].update(changeset)
            self.assertTrue(
                _raw_problems(
                    record["raw"],
                    Trial(Condition("mixed", 1, 256, 1), "totedb", self.directory),
                )
            )
            records = self._records()
            records[3] = record
            self.assertEqual(_summarize(records, [1], 3)["status"], "incomparable")

    def test_paired_ratios_are_not_ratio_of_medians_and_exclude_preflight(self) -> None:
        summary = _summarize(self._records(), [1], 3)
        self.assertEqual(summary["status"], "comparable")
        ratios = summary["conditions"]["1"]["paired_throughput_candidate_over_baseline"]
        self.assertEqual(ratios["values"], [2, 0.5, 3])
        self.assertEqual(
            (ratios["median"], ratios["min"], ratios["max"], ratios["n"]),
            (2, 0.5, 3, 3),
        )
        self.assertEqual(ratios["bootstrap_95_ci"], [0.5, 3])
        self.assertEqual(
            summary["conditions"]["1"]["elapsed_s"]["baseline"]["median"], 10
        )
        self.assertNotEqual(ratios["median"], 10 / 20)

    def test_duplicate_or_missing_pairs_refuse_comparison(self) -> None:
        records = self._records()
        self.assertEqual(_summarize(records[:-1], [1], 3)["status"], "incomparable")
        records[-1] = records[-2]
        self.assertEqual(_summarize(records, [1], 3)["status"], "incomparable")

    def test_preflight_failure_stops_before_formal_trials(self) -> None:
        library = self.directory / "library.so"
        library.write_bytes(b"not loaded by mock")
        args = self._args(library, self.directory / "compare")
        failed = self._record("baseline", "preflight", 0, 1)
        failed["status"] = "failed"
        with patch(
            "bench.queue_contention.diagnose._execute", return_value=failed
        ) as execute:
            result = _compare(args)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result["status"], "incomparable")
        self.assertTrue((args.output / "records.json").exists())

    def test_trace_unsupported_is_unavailable_not_zero_cost(self) -> None:
        library = self.directory / "library.so"
        library.write_bytes(b"not loaded on unsupported OS")
        args = self._args(library, self.directory / "trace")
        args.command, args.engine, args.library, args.perf, args.workers = (
            "trace",
            "totedb",
            library,
            None,
            1,
        )
        with patch("bench.queue_contention.diagnose.sys.platform", "darwin"):
            result = _trace(args)
        self.assertEqual(result["status"], "unavailable")
        for tool in result["tools"].values():
            self.assertEqual(tool["status"], "unavailable")
            self.assertNotIn("cost", tool)
            self.assertEqual(tool["trial_status"], "not_started")
        with self.assertRaises(FileExistsError):
            _trace(args)

    def test_trace_permission_failure_is_distinct_from_trial_failure(self) -> None:
        library = self.directory / "library.so"
        library.write_bytes(b"fixture")
        args = argparse.Namespace(perf=None)
        for started in (False, True):
            invocation = _invocation(
                self.directory,
                Variant(str(started), "totedb", library),
                "trace",
                0,
                Condition("mixed", 1, 256, 1),
                5,
            )

            def denied(
                command: list[str], directory: Path, selected: Path, timeout: float
            ) -> dict:
                (directory / "stdout.txt").write_text("")
                (directory / "stderr.txt").write_text("permission denied")
                if started:
                    (directory / "trial-started").touch()
                return {"exit_code": 1, "timed_out": False}

            with (
                patch("bench.queue_contention.diagnose.sys.platform", "linux"),
                patch(
                    "bench.queue_contention.diagnose.shutil.which",
                    return_value="/test/perf",
                ),
                patch("bench.queue_contention.diagnose._process", side_effect=denied),
            ):
                result = _trace_tool(invocation, "perf", args)
            self.assertEqual(result["status"], "failed" if started else "unavailable")

    def test_trial_failure_preserves_spec_and_partial_stdout(self) -> None:
        variant = Variant("baseline", "totedb", self.directory / "library.so")
        invocation = _invocation(
            self.directory, variant, "preflight", 0, Condition("mixed", 1, 256, 1), 5
        )

        def failed(
            command: list[str], directory: Path, library: Path, timeout: float
        ) -> dict:
            (directory / "stdout.txt").write_text('{"status":')
            (directory / "stderr.txt").write_text("trial failed")
            invocation.spec.directory.mkdir()
            (invocation.spec.directory / "queue.db").write_bytes(b"preserved")
            return {"exit_code": 1, "timed_out": False}

        with patch("bench.queue_contention.diagnose._process", side_effect=failed):
            record = _execute(invocation)
        self.assertEqual(record["status"], "failed")
        spec = json.loads(
            (invocation.spec.evidence_directory / "spec.json").read_bytes()
        )
        self.assertEqual(spec["engine"], "totedb")
        self.assertEqual(
            (invocation.spec.directory / "queue.db").read_bytes(), b"preserved"
        )

    def test_compare_rotates_variants_and_keeps_libraries_distinct(self) -> None:
        baseline, candidate = (
            self.directory / "baseline.so",
            self.directory / "candidate.so",
        )
        baseline.write_bytes(b"baseline")
        candidate.write_bytes(b"candidate")
        args = self._args(baseline, self.directory / "rotation")
        args.candidate_lib, args.jobs, args.repetitions = candidate, 1, 3

        def success(invocation: Invocation) -> dict:
            return self._record(
                invocation.variant.name, invocation.phase, invocation.repetition, 1
            )

        with patch(
            "bench.queue_contention.diagnose._execute", side_effect=success
        ) as execute:
            result = _compare(args)
        self.assertEqual(result["status"], "comparable")
        invocations = [call.args[0] for call in execute.call_args_list]
        names = [i.variant.name for i in invocations]
        self.assertEqual(
            names,
            [
                "baseline",
                "candidate",
                "control",
                "baseline",
                "candidate",
                "control",
                "candidate",
                "control",
                "baseline",
                "control",
                "baseline",
                "candidate",
            ],
        )
        for invocation in invocations:
            self.assertEqual(
                invocation.variant.library,
                baseline if invocation.variant.name == "baseline" else candidate,
            )
            self.assertEqual(
                invocation.spec.engine,
                "sqlite-wal" if invocation.variant.name == "control" else "totedb",
            )

    def test_small_prebuilt_same_library_smoke(self) -> None:
        extension = "dylib" if sys.platform == "darwin" else "so"
        library = Path(
            os.environ.get("TOTEDB_LIB", ROOT / f"build/lib/libtotedb.{extension}")
        ).resolve()
        if not library.is_file():
            self.skipTest("prebuilt library is required; tests never build")
        output = self.directory / "smoke"
        command = [
            sys.executable,
            str(ROOT / "bench/queue_contention/diagnose.py"),
            "compare",
            "--baseline-lib",
            str(library),
            "--candidate-lib",
            str(library),
            "--output",
            str(output),
            "--jobs",
            "12",
            "--workers",
            "1",
            "--repetitions",
            "1",
        ]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=120)
        (self.directory / "smoke-stdout.txt").write_bytes(result.stdout)
        (self.directory / "smoke-stderr.txt").write_bytes(result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        records = json.loads((output / "records.json").read_bytes())
        self.assertEqual(len(records), 6)
        self.assertEqual(len({r["raw"]["pid"] for r in records}), 6)
        for record in records:
            self.assertEqual(record["raw"]["completed_jobs"], 12)
            self.assertEqual(record["raw"]["audit"]["rows"], 12)
            self.assertEqual(len(record["raw"]["audit"]["items"]), 12)
            self.assertEqual(record["raw"]["file_sizes"]["phase"], "after_close")
            self.assertEqual(
                record["engine"],
                "sqlite-wal" if record["variant"] == "control" else "totedb",
            )
        manifest = json.loads((output / "manifest.json").read_bytes())
        self.assertTrue(manifest["libraries_unchanged"])

    def _args(self, library: Path, output: Path) -> argparse.Namespace:
        return argparse.Namespace(
            command="compare",
            baseline_lib=library,
            candidate_lib=library,
            output=output,
            jobs=12,
            workers=[1],
            repetitions=1,
            timeout=10,
        )

    def _record(self, variant: str, phase: str, repeat: int, elapsed: float) -> dict:
        condition = dataclasses.asdict(Condition("mixed", 1, 256, 1))
        engine = "sqlite-wal" if variant == "control" else "totedb"
        return {
            "variant": variant,
            "phase": phase,
            "repetition": repeat,
            "condition": condition,
            "engine": engine,
            "status": "ok",
            "raw": {
                "condition": condition,
                "engine": engine,
                "status": "ok",
                "problems": [],
                "elapsed_s": elapsed,
                "completed_jobs": 1,
                "audit": {"rows": 1, "items": [[10, 0, "hash"]], "problems": []},
                "workers": [
                    {
                        "acked": [[10, 0, 1, "hash"]],
                        "latency_s": {
                            op: [1]
                            for op in ("put", "claim", "ack", "outbox", "empty_claim")
                        },
                        "error": None,
                        "busy": 0,
                        "retry_sleep_s": 0,
                    }
                ],
            },
        }

    def _records(self) -> list[dict]:
        records = [
            self._record(v, "preflight", 0, 999)
            for v in ("baseline", "candidate", "control")
        ]
        for repeat, (baseline, candidate) in enumerate(((2, 1), (10, 20), (90, 30))):
            records.extend(
                self._record(v, "measured", repeat, elapsed)
                for v, elapsed in (
                    ("baseline", baseline),
                    ("candidate", candidate),
                    ("control", 1),
                )
            )
        return records
