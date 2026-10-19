from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/experiments/release-cost/suite.py"
SPEC = importlib.util.spec_from_file_location("release_cost_suite", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load release-cost suite")
suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suite)


@pytest.fixture
def evidence() -> Path:
    path = ROOT / ".local/runs/release-cost-tests" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture
def native(evidence: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    spec = {
        "id": "fixture",
        "n": 8,
        "mode": "on",
        "engine": "tote",
        "phase": "measured",
        "repetition": 1,
    }
    records = []
    for index in range(24):
        phases = []
        for phase in range(12):
            phases.append(
                {
                    "wall_ns": 99 if phase == 9 else 0,
                    "cpu_ns": 11 if phase == 9 else 0,
                    "exclusive_wall_ns": 7 if phase == 9 else 0,
                    "exclusive_cpu_ns": 3 if phase == 9 else 0,
                    "attempts": 2 if phase == 9 else 0,
                    "bytes": 17 if phase == 9 else 0,
                    "errors": 0,
                    "short_io": 0,
                    "eintr": 0,
                }
            )
        records.append(
            {
                "operation": ("tote_begin", "tote_exec", "tote_commit")[index % 3],
                "sequence": index + 5,
                "pid": 42,
                "tid": 43,
                "incomplete": 0,
                "overflow": 0,
                "dropped": 0,
                "phases": phases,
            }
        )
    raw = {
        "error": 0,
        "completed": 8,
        "begin_ns": 11,
        "prepare_ns": 13,
        "exec_ns": 17,
        "commit_ns": 19,
        "output_client_ns": 23,
        "records": records,
    }
    directory = evidence / "native"
    directory.mkdir()
    (directory / "stdout.txt").write_text(json.dumps(raw))
    (directory / "stderr.txt").write_text("")
    (directory / "spec.json").write_text(json.dumps(spec))
    (directory / "command.json").write_text(
        json.dumps({"exit_code": 0, "timed_out": False, "pid": 42})
    )
    return directory, spec, raw


def test_native_fixture_recomputes_elapsed_exclusive_phases_and_accepted_io(
    native: tuple,
) -> None:
    directory, spec, _ = native

    row = suite._read_native(directory, spec)
    phases = suite._phase_summary([row])

    assert row["elapsed_ns"] == 83
    assert row["ops_s"] == pytest.approx(8_000_000_000 / 83)
    assert phases["write"]["exclusive_wall_ns"]["median"] == 168
    assert phases["write"]["exclusive_cpu_ns"]["median"] == 72
    assert phases["write"]["bytes"]["median"] == 408
    assert phases["write"]["bytes_per_op"]["median"] == 51
    assert phases["write"]["attempts_per_op"]["median"] == 6
    assert "wall_ns" not in phases["write"]


def test_failed_on_preflight_invalidates_perturbation(
    evidence: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def read(directory: Path, spec: dict[str, Any]) -> dict[str, Any]:
        if (
            spec["n"] == 128
            and spec["mode"] == "on"
            and spec["engine"] == "tote"
            and spec["phase"] == "preflight"
        ):
            raise ValueError("failed ON preflight")
        return {
            **spec,
            "ops_s": 1.0,
            "elapsed_ns": 1,
            "timers": dict.fromkeys(suite.TIMERS, 1),
            "phases": {},
        }

    monkeypatch.setattr(suite, "_read_native", read)
    result = suite._native_summary(evidence, "run")
    assert result["instrumentation_perturbation"][0]["status"] == "incomparable"
    assert result["instrumentation_perturbation"][1]["status"] == "comparable"


@pytest.mark.parametrize("fault", ["modify", "delete", "extra"])
def test_external_database_changes_are_rejected(
    evidence: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    output = evidence / "output"
    database = evidence / "build/ycsb/runs/run/database.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"original database")
    suite._write(
        output / "data/manifest.json",
        {"database_roots": ["build/ycsb/runs/run"]},
    )
    monkeypatch.setenv("RELEASE_COST_DATABASE_ROOT", str(evidence))
    suite._seal(output)
    suite._verify_hashes(output)
    if fault == "modify":
        database.write_bytes(b"corrupt database")
    elif fault == "delete":
        database.unlink()
    else:
        (database.parent / "extra.db").write_bytes(b"unexpected database")
    with pytest.raises(ValueError, match="external database"):
        suite._verify_hashes(output)


def test_collect_rejects_verification_database_root_override(
    evidence: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RELEASE_COST_DATABASE_ROOT", str(evidence / "wrong"))
    with pytest.raises(ValueError, match="verification-only"):
        suite._collect(evidence, "run")


@pytest.mark.parametrize(
    "fault",
    [
        "exit",
        "timeout",
        "completed",
        "error",
        "nan",
        "negative",
        "missing",
        "dropped",
        "incomplete",
        "overflow",
        "pid",
        "tid",
        "sequence",
        "operation",
        "phase-count",
        "condition",
        "launched-pid",
    ],
)
def test_native_corrupt_evidence_is_rejected(native: tuple, fault: str) -> None:
    directory, spec, raw = native
    if fault in ("exit", "timeout"):
        (directory / "command.json").write_text(
            json.dumps(
                {
                    "exit_code": 1 if fault == "exit" else 0,
                    "timed_out": fault == "timeout",
                }
            )
        )
    elif fault == "completed":
        raw["completed"] = 7
    elif fault == "error":
        raw["error"] = 1
    elif fault == "nan":
        raw["exec_ns"] = float("nan")
    elif fault == "negative":
        raw["begin_ns"] = -1
    elif fault == "missing":
        (directory / "stdout.txt").unlink()
    elif fault in ("dropped", "incomplete", "overflow"):
        raw["records"][4][fault] = 1
    elif fault in ("pid", "tid"):
        raw["records"][4][fault] += 1
    elif fault == "sequence":
        raw["records"][4]["sequence"] = 5
    elif fault == "operation":
        raw["records"][4]["operation"] = "tote_commit"
    elif fault == "phase-count":
        raw["records"][4]["phases"].pop()
    elif fault == "condition":
        (directory / "spec.json").write_text(json.dumps({**spec, "n": 9}))
    elif fault == "launched-pid":
        (directory / "command.json").write_text(
            json.dumps({"exit_code": 0, "timed_out": False, "pid": 999})
        )
    if fault != "missing":
        (directory / "stdout.txt").write_text(json.dumps(raw))

    with pytest.raises((ValueError, OSError)):
        suite._read_native(directory, spec)


def test_native_invalid_trace_is_listed_and_excluded(
    native: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, spec, raw = native
    root = directory.parent
    destination = root / "logs/run-native-fixture"
    destination.parent.mkdir()
    directory.rename(destination)
    raw["records"][0]["dropped"] = 1
    (destination / "stdout.txt").write_text(json.dumps(raw))
    monkeypatch.setattr(suite, "_native_schedule", lambda: [spec])

    summary = suite._native_summary(root, "run")

    assert summary["failed_runs"] == 1
    assert summary["failures"][0]["id"] == "fixture"
    assert summary["runs"] == []
    assert summary["aggregates"] == []
    assert all(row["status"] == "incomparable" for row in summary["comparisons"])


@pytest.mark.parametrize(
    "fault", ["modified", "missing", "additional", "duplicate", "traversal"]
)
def test_evidence_hash_or_file_set_change_is_rejected(
    evidence: Path, fault: str
) -> None:
    (evidence / "data").mkdir()
    raw = evidence / "data/raw.json"
    raw.write_text('{"value": 1}\n')
    suite._seal(evidence)
    suite._verify_hashes(evidence)
    if fault == "modified":
        raw.write_text('{"value": 2}\n')
    elif fault == "missing":
        raw.unlink()
    elif fault == "additional":
        (evidence / "env.json").write_text("{}")
    else:
        path = evidence / "data/SHA256SUMS"
        extra = path.read_text() if fault == "duplicate" else "0  ../outside\n"
        path.write_text(path.read_text() + extra)

    with pytest.raises(ValueError):
        suite._verify_hashes(evidence)


def test_native_schedule_has_fixed_counts_rotation_and_mode_alternation() -> None:
    schedule = suite._native_schedule()

    assert len(schedule) == 72
    assert sum(row["phase"] == "measured" for row in schedule) == 60
    assert sum(row["phase"] == "preflight" for row in schedule) == 12
    assert len({row["id"] for row in schedule}) == 72
    for n in (128, 1024):
        first_engines = []
        for repeat in range(6):
            selected = [
                row for row in schedule if row["n"] == n and row["repetition"] == repeat
            ]
            assert selected[0]["mode"] == ("off" if repeat % 2 == 0 else "on")
            assert {row["mode"] for row in selected} == {"on", "off"}
            first_engines.append(selected[0]["engine"])
        assert first_engines[:3] == ["tote", "sqlite-each", "sqlite-reuse"]


@pytest.mark.parametrize("fault", ["none", "missing", "duplicate", "n", "repeat"])
def test_native_ratios_only_pair_same_n_and_repetition(fault: str) -> None:
    left = [{"n": 128, "repetition": repeat, "ops_s": 200} for repeat in range(1, 6)]
    right = [{"n": 128, "repetition": repeat, "ops_s": 100} for repeat in range(1, 6)]
    if fault == "missing":
        right.pop()
    elif fault == "duplicate":
        right[4] = copy.deepcopy(right[3])
    elif fault in ("n", "repeat"):
        right[4]["n" if fault == "n" else "repetition"] += 1

    result = suite._paired(left, right)

    if fault == "none":
        assert result["values"] == [2, 2, 2, 2, 2]
        assert result["median"] == 2
        assert result["bootstrap_95_ci"] == [2, 2]
    else:
        assert result["status"] == "incomparable"
        assert result["values"] == []


def test_suite_partial_summary_is_deterministic_and_tampering_is_rejected(
    evidence: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hashes = {"src/example.c": "a" * 64}
    source = {
        "hashes": hashes,
        "fingerprint": hashlib.sha256(suite._json_bytes(hashes)).hexdigest(),
        "product_diff": {"exit_code": 0, "stdout": ""},
    }
    suite._write(
        evidence / "data/manifest.json",
        {
            "run_id": "fixture",
            "state": "failed",
            "source": source,
            "source_after": source,
            "expected": {},
        },
    )
    suite._seal(evidence)
    monkeypatch.setattr(suite, "_native_schedule", lambda: [])

    def subordinate(*args: Any) -> dict[str, Any]:
        if args[1] == "queue":
            return {"runs": [], "aggregates": [], "comparisons": []}
        return {
            "measured_runs": 0,
            "preflight_runs": 0,
            "failed_runs": 0,
            "conditions": [],
        }

    monkeypatch.setattr(suite, "_derived", subordinate)
    summary = suite._process(evidence, verify=False)
    path = evidence / "results/summary.json"
    before = path.read_bytes()
    repeated = suite._process(evidence, verify=True)

    assert summary == repeated
    assert summary["status"] == "failed"
    assert summary["incomplete_or_failed"] == [
        "native",
        "ycsb",
        "queue",
        "collection-state",
    ]
    assert path.read_bytes() == before
    path.write_text("{}\n")
    with pytest.raises(ValueError, match="regeneration"):
        suite._process(evidence, verify=True)


def test_cli_requires_output_and_refuses_existing_collection(evidence: Path) -> None:
    missing = subprocess.run(
        [sys.executable, str(SCRIPT), "collect"], capture_output=True
    )
    existing = subprocess.run(
        [sys.executable, str(SCRIPT), "collect", "--output", str(evidence)],
        capture_output=True,
    )

    assert missing.returncode != 0
    assert existing.returncode != 0
    assert list(evidence.iterdir()) == []


def test_run_records_nonzero_exit_and_refuses_log_overwrite(evidence: Path) -> None:
    directory = evidence / "logs/fixture-native"

    result = suite._run(
        [sys.executable, "-c", "import sys; print('raw'); sys.exit(3)"], directory, 5
    )

    assert result["exit_code"] == 3
    assert (directory / "stdout.txt").read_text() == "raw\n"
    assert json.loads((directory / "command.json").read_bytes())["exit_code"] == 3
    with pytest.raises(FileExistsError):
        suite._run([sys.executable, "-c", "print('overwrite')"], directory, 5)


def test_native_bootstrap_nonconstant_pairs_have_reproducible_interval() -> None:
    left = [
        {"n": 1024, "repetition": repeat, "ops_s": repeat * 100}
        for repeat in range(1, 6)
    ]
    right = [{"n": 1024, "repetition": repeat, "ops_s": 100} for repeat in range(1, 6)]

    result = suite._paired(left, right)

    assert result["values"] == [1, 2, 3, 4, 5]
    assert result["median"] == 3
    assert result["bootstrap_95_ci"] == [1, 5]
    assert suite._paired(left, right) == result


def test_collection_finishes_all_builds_then_continues_axes_after_failure(
    evidence: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []
    source = {"product_diff": {"exit_code": 0, "stdout": ""}}
    monkeypatch.setattr(suite, "_source_identity", lambda: source)
    monkeypatch.setattr(suite, "_environment", lambda: {})

    def build(*args: Any) -> dict[str, Any]:
        events.append("builds-and-controls")
        return {"off": {"exit_code": 0, "timed_out": False}}

    def native(*args: Any) -> None:
        events.append("native")
        raise ValueError("fixture native failure")

    def run(command: list[str], *args: Any) -> dict[str, Any]:
        events.append("ycsb" if "ycsb" in command[1] else "queue")
        return {"exit_code": 0, "timed_out": False}

    monkeypatch.setattr(suite, "_build", build)
    monkeypatch.setattr(suite, "_collect_native", native)
    monkeypatch.setattr(suite, "_run", run)

    suite._collect(evidence, "fixture")
    manifest = json.loads((evidence / "data/manifest.json").read_bytes())

    assert events == ["builds-and-controls", "native", "ycsb", "queue"]
    assert manifest["state"] == "failed"
    assert manifest["axes"]["native"]["state"] == "failed"
    assert manifest["axes"]["queue"]["state"] == "complete"
    suite._verify_hashes(evidence)


def test_suite_complete_summary_preserves_subordinate_comparisons_and_total_counts(
    evidence: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "hashes": {},
        "fingerprint": hashlib.sha256(b"{}\n").hexdigest(),
        "product_diff": {"exit_code": 0, "stdout": ""},
    }
    suite._write(
        evidence / "data/manifest.json",
        {
            "run_id": "fixture",
            "state": "complete",
            "source": source,
            "source_after": source,
            "expected": {
                "native": {"measured": 60, "preflight": 12},
                "ycsb": {"measured": 120, "preflight": 24},
                "queue": {"measured": 240, "preflight": 48},
            },
        },
    )
    suite._seal(evidence)
    monkeypatch.setattr(
        suite,
        "_native_summary",
        lambda *args: {"measured_runs": 60, "preflight_runs": 12, "failed_runs": 0},
    )
    queue = {
        "runs": [{"phase": "measured", "status": "ok"}] * 240
        + [{"phase": "preflight", "status": "ok"}] * 48,
        "comparisons": [{"baseline": "sqlite-wal", "median_ratio": 1.25}],
        "aggregates": [{"engine": "totedb"}],
    }
    ycsb = {
        "measured_runs": 120,
        "preflight_runs": 24,
        "failed_runs": 0,
        "conditions": [{"workload": "F", "paired_throughput_ratio": {"median": 0.75}}],
    }
    monkeypatch.setattr(
        suite,
        "_derived",
        lambda output, axis, *args: queue if axis == "queue" else ycsb,
    )

    summary = suite._process(evidence, verify=False)

    assert summary["status"] == "complete"
    assert summary["totals"] == {
        "measured_runs": 420,
        "preflight_runs": 84,
        "failed_runs": 0,
    }
    assert summary["axes"]["queue"]["original_summary"] == queue
    assert summary["axes"]["ycsb"]["original_summary"] == ycsb
    assert suite._process(evidence, verify=True) == summary
