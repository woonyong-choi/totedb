from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from totedb import StaleError

from bench.queue_contention.adapters import (
    ENGINES,
    LEASE_MS,
    Retry,
    open_adapter,
    payload,
)
from bench.queue_contention.analysis import (
    ContentionError,
    _validate_records,
    verify_output,
)
from bench.queue_contention.run import _matrix, _read_trial, _schedule
from bench.queue_contention.trial import WorkerResult

ROOT = Path(__file__).resolve().parents[1]


class QueueContentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = ROOT / "build/queue-contention/tests" / uuid.uuid4().hex
        self.directory.mkdir(parents=True)

    def test_adapters_preserve_binary_lease_fence_outbox_and_reopen(self) -> None:
        for engine in ENGINES:
            with self.subTest(engine=engine):
                self._adapter_contract(engine)

    def test_reduced_matrix_completes_all_jobs_and_preserves_evidence(self) -> None:
        output = self.directory / "matrix"
        result = self._command(output, [])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["expected_records"], 48)
        self.assertEqual(manifest["failed_records"], 0)
        records = self._records(output)
        self.assertEqual(sum(r["phase"] == "preflight" for r in records), 24)
        self.assertEqual(sum(r["phase"] == "measured" for r in records), 24)
        for record in records:
            self.assertEqual(record["audit"]["rows"], 12)
            self.assertEqual(record["completed_jobs"], 12)
            self.assertTrue(
                all(w["ready_at"] <= record["started_at"] for w in record["workers"])
            )
            self.assertEqual(
                len(record["workers"]),
                record["condition"]["workers"]
                * (2 if record["condition"]["workload"] == "mixed" else 1),
            )
        self.assertEqual(verify_output(output)["status"], "verified")
        duplicated = list(records)
        duplicated[1] = duplicated[0]
        with self.assertRaisesRegex(ContentionError, "missing or duplicated"):
            _validate_records(manifest, duplicated)

    def test_failures_are_retained_and_make_comparisons_incomparable(self) -> None:
        for fault in ("worker-error", "bad-payload", "missing-job", "timeout"):
            with self.subTest(fault=fault):
                output = self.directory / fault
                result = self._command(
                    output,
                    [
                        "--workloads",
                        "enqueue",
                        "--workers",
                        "1",
                        "--engines",
                        "sqlite-wal",
                        "--fault",
                        fault,
                        "--timeout",
                        "0.15" if fault == "timeout" else "10",
                    ],
                )
                self.assertEqual(result.returncode, 1, result.stderr.decode())
                manifest = json.loads(result.stdout)
                self.assertEqual(manifest["expected_records"], 2)
                self.assertEqual(manifest["failed_records"], 2)
                records = self._records(output)
                self.assertTrue(
                    all(r["status"] == "failed" and r["problems"] for r in records)
                )
                self.assertEqual(verify_output(output)["status"], "verified")
                summary = json.loads((output / "results/summary.json").read_bytes())
                self.assertTrue(
                    all(c["status"] == "incomparable" for c in summary["comparisons"])
                )
                self.assertEqual(
                    len(list(self._raw_directory(output).glob("trials/*/stderr.txt"))),
                    2,
                )

    def test_duplicate_output_and_raw_or_summary_tampering_are_rejected(self) -> None:
        output = self.directory / "evidence"
        options = [
            "--workloads",
            "enqueue",
            "--workers",
            "1",
            "--engines",
            "sqlite-wal",
        ]
        self.assertEqual(self._command(output, options).returncode, 0)
        original = (self._raw_directory(output) / "raw.jsonl").read_bytes()
        self.assertEqual(self._command(output, options).returncode, 1)
        self.assertEqual(
            (self._raw_directory(output) / "raw.jsonl").read_bytes(), original
        )

        (self._raw_directory(output) / "raw.jsonl").write_bytes(original + b"\n")
        with self.assertRaisesRegex(ContentionError, "hash mismatch"):
            verify_output(output)
        (self._raw_directory(output) / "raw.jsonl").write_bytes(original)
        summary = output / "results/summary.json"
        summary.write_bytes(summary.read_bytes() + b"\n")
        with self.assertRaisesRegex(ContentionError, "regeneration mismatch"):
            verify_output(output)

    def test_killed_partial_worker_output_is_retained_without_stopping_collection(
        self,
    ) -> None:
        valid = dataclasses.asdict(WorkerResult(1, "producer"))
        (self.directory / "worker-1.json").write_text(json.dumps(valid))
        path = self.directory / "worker-0.json"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import pathlib, sys, time; "
                "pathlib.Path(sys.argv[1]).write_bytes(b'{\"worker\":'); "
                "print('written', flush=True); time.sleep(60)",
                str(path),
            ],
            stdout=subprocess.PIPE,
        )
        try:
            self.assertEqual(child.stdout.readline(), b"written\n")
        finally:
            child.kill()
            child.wait(timeout=5)
            child.stdout.close()
        original = path.read_bytes()
        for partial in (original, b'{"worker": "\xff'):
            with self.subTest(partial=partial):
                path.write_bytes(partial)
                failed = _read_trial(self.directory, child.returncode, True)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["workers"], [valid])
                self.assertTrue(
                    any("worker-0.json" in problem for problem in failed["problems"])
                )
                self.assertIn("controller timeout", failed["problems"])
                self.assertEqual(path.read_bytes(), partial)
        (self.directory / "stdout.txt").write_text(json.dumps({"status": "ok"}))
        self.assertEqual(_read_trial(self.directory, 0, False)["status"], "ok")

    def test_default_schedule_contains_72_preflights_and_360_rotated_trials(
        self,
    ) -> None:
        import argparse

        args = argparse.Namespace(
            command="collect",
            workers=None,
            payloads=None,
            engines=",".join(ENGINES),
            workloads="enqueue,drain,mixed,outbox",
            jobs=None,
            repetitions=None,
        )
        schedule = _schedule(_matrix(args))
        self.assertEqual(sum(phase == "preflight" for phase, _, _, _ in schedule), 72)
        self.assertEqual(sum(phase == "measured" for phase, _, _, _ in schedule), 360)
        first = schedule[0][2]
        orders = [
            [
                engine
                for phase, repeat, condition, engine in schedule
                if phase == "measured" and repeat == repetition and condition == first
            ]
            for repetition in range(3)
        ]
        self.assertEqual(
            orders,
            [
                list(ENGINES),
                [ENGINES[1], ENGINES[2], ENGINES[0]],
                [ENGINES[2], ENGINES[0], ENGINES[1]],
            ],
        )

    def _adapter_contract(self, engine: str) -> None:
        path = self.directory / (engine + ".db")
        adapter = open_adapter(path, engine, install=True)
        retry = Retry()
        binary = payload(0, 64)
        try:
            adapter.outbox(99, b"rolled back\x00", retry, rollback=True)
            self.assertEqual(adapter.rows(), [])
            self.assertEqual(adapter.business(), [])
            first = adapter.put(binary, retry)
            second = adapter.put(payload(1, 64), retry)
            claim = adapter.claim(retry)
            self.assertEqual(
                (claim.id, claim.payload, claim.fence, claim.attempts),
                (first, binary, 1, 1),
            )
            self.assertEqual(sum(row[1] == 0 for row in adapter.rows()), 1)
            adapter.clock += LEASE_MS + 1
            reclaimed = adapter.claim(retry)
            self.assertEqual(
                (reclaimed.id, reclaimed.fence, reclaimed.attempts), (first, 2, 2)
            )
            with self.assertRaises(StaleError):
                adapter.ack(claim, retry)
            adapter.ack(reclaimed, retry)
            adapter.ack(reclaimed, retry)
            remaining = adapter.claim(retry)
            self.assertEqual(remaining.id, second)
            adapter.ack(remaining, retry)
            self.assertIsNone(adapter.claim(retry))
            adapter.outbox(2, payload(2, 64), retry)
            expected = adapter.rows()
            self.assertEqual(adapter.business(), [(2, payload(2, 64))])
            if engine != "totedb":
                settings = adapter.settings()
                self.assertEqual(
                    (
                        settings["page_size"],
                        settings["synchronous"],
                        settings["cache_size"],
                        settings["busy_timeout"],
                        settings["mmap_size"],
                    ),
                    (8192, 2, 256, 0, 0),
                )
        finally:
            adapter.close()
        reopened = open_adapter(path, engine)
        try:
            self.assertEqual(reopened.rows(), expected)
            self.assertEqual(reopened.business(), [(2, payload(2, 64))])
        finally:
            reopened.close()

    def _command(
        self, output: Path, options: list[str]
    ) -> subprocess.CompletedProcess[bytes]:
        command = [
            sys.executable,
            str(ROOT / "bench/queue_contention/run.py"),
            "test",
            "--output",
            str(output),
            "--run-id",
            self.directory.name + "-" + output.name,
            *options,
        ]
        return subprocess.run(
            command,
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            timeout=180,
            check=False,
        )

    def _raw_directory(self, output: Path) -> Path:
        return next((output / "data/raw").iterdir())

    def _records(self, output: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in (self._raw_directory(output) / "raw.jsonl")
            .read_text()
            .splitlines()
        ]


if __name__ == "__main__":
    unittest.main()
