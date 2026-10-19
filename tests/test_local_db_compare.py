"""Contract tests for the #244 local database primitive collector."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bench/local_db_compare.py"
SPEC = importlib.util.spec_from_file_location("local_db_compare", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


def test_public_additional_analysis_has_explicit_descriptive_boundaries() -> None:
    spec = importlib.util.spec_from_file_location(
        "local_db_publish",
        SCRIPT.parents[1] / "docs/experiments/local-db-comparison/publish.py",
    )
    assert spec is not None and spec.loader is not None
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    database = {
        "groups": [
            {
                "engine": "tote",
                "workload": "atomic-outbox",
                "payload_bytes": 256,
                "ops_per_s": {"median": 100},
            },
            {
                "engine": "tote",
                "workload": "atomic-outbox-batch",
                "payload_bytes": 256,
                "ops_per_s": {"median": 400},
            },
        ]
    }
    identity = {"engine": "totedb", "n": 2, "payload_bytes": 256}
    queues = {
        "groups": [identity],
        "trials": [
            {
                **identity,
                "warmup": False,
                "status": "ok",
                "timing": {
                    "put": {"count": 2, "total_ns": total},
                    "read": {"count": 0, "total_ns": 0},
                    "ack": {"count": 0, "total_ns": 0},
                },
            }
            for total in (200, 400)
        ],
    }
    result = publisher.additional_analysis(database, queues, 64)
    assert result["batch_scaling"][0]["ratio_of_medians"] == 4
    assert result["batch_protocol"]["commit_calls"] == 2
    costs = result["queue_api_costs"][0]["mean_call_wall_ns_across_trials"]
    assert costs == {"put": {"median": 150, "min": 100, "max": 200}}
    assert "not dynamic" in result["static_success_path"]["evidence_kind"]


def test_public_export_cannot_write_into_original_artifact(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "local_db_publish_guard",
        SCRIPT.parents[1] / "docs/experiments/local-db-comparison/publish.py",
    )
    assert spec is not None and spec.loader is not None
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    with pytest.raises(ValueError, match="outside the immutable artifact"):
        publisher.publish(tmp_path, tmp_path / "modified")


@pytest.fixture
def measured_rows() -> list[dict[str, Any]]:
    rows = []
    for repeat in range(1, 6):
        for engine, elapsed_ns in (("tote", 1_000_000_000), ("lmdb", 2_000_000_000)):
            rows.append(
                {
                    "engine": engine,
                    "workload": "write",
                    "payload_bytes": 256,
                    "warmup": False,
                    "repeat": repeat,
                    "status": "PASS",
                    "elapsed_ns": elapsed_ns,
                    "commit_latency_ns": [100, 200, 300],
                }
            )
    warmups = [{**row, "repeat": 0, "warmup": True} for row in rows[:2]]
    return warmups + rows


@pytest.fixture
def sealed_failures(tmp_path: Path) -> Path:
    root = tmp_path / "collection"
    root.mkdir()
    evidence = root / "evidence"
    evidence.mkdir()
    (evidence / "native.so").write_bytes(b"fixture library, never loaded")
    (evidence / "requirements.lock").write_bytes(b"fixture lock")
    identity = {"library_sha256": compare._digest(evidence / "native.so")}
    manifest = {
        "schedule": compare._schedule(),
        "operations": 512,
        "identity_before": identity,
        "identity_after": identity,
        "packages_before": {},
        "packages_after": {},
        "native_library": "evidence/native.so",
        "installation_lock_sha256": compare._digest(evidence / "requirements.lock"),
        "preflight": [
            {"engine": engine, "status": "UNAVAILABLE", "exit_code": 1}
            for engine in compare.ENGINES
        ],
    }
    rows = []
    for index, condition in enumerate(manifest["schedule"]):
        trial = root / "trials" / f"{index:04d}"
        trial.mkdir(parents=True)
        row = {**condition, "status": "UNAVAILABLE", "exit_code": 1}
        for name, value in (("command.json", {}), ("result.json", row)):
            compare._write_json(trial / name, value)
        (trial / "stdout.json").write_text("")
        (trial / "stderr.log").write_text("import unavailable\n")
        rows.append(row)
    for engine in compare.ENGINES:
        trial = root / "preflight" / engine
        trial.mkdir(parents=True)
        row = {"engine": engine, "status": "UNAVAILABLE", "exit_code": 1}
        compare._write_json(trial / "command.json", {})
        compare._write_json(trial / "result.json", row)
        (trial / "stdout.json").write_text("")
        (trial / "stderr.log").write_text("import unavailable\n")
    compare._write_json(root / "manifest.json", manifest)
    compare._write_json(root / "summary.json", compare._analyze_rows(rows))
    compare._seal(root)
    return root


def test_schedule_fixed_conditions_counts_and_rotating_engines() -> None:
    schedule = compare._schedule()

    assert len(schedule) == 216
    assert sum(not row["warmup"] for row in schedule) == 180
    assert [row["engine"] for row in schedule[6:12]] == list(
        compare.ENGINES[1:] + compare.ENGINES[:1]
    )
    assert len({tuple(sorted(row.items())) for row in schedule}) == 216


def test_analyze_hand_calculated_throughput_latency_and_pairing(
    measured_rows: list[dict[str, Any]],
) -> None:
    result = compare._analyze_rows(
        measured_rows,
        [{"engine": engine, "status": "PASS"} for engine in compare.ENGINES],
    )
    tote = next(
        group
        for group in result["groups"]
        if group["engine"] == "tote" and group["pass_count"]
    )
    ratio = next(
        row
        for row in result["paired_ratios"]
        if row["engine"] == "lmdb" and row["status"] == "PASS"
    )

    assert tote["ops_per_s"] == {"median": 512.0, "min": 512.0, "max": 512.0}
    assert tote["per_trial"][0]["transaction_latency_ns"] == {
        "p50": 200,
        "p95": 290,
        "p99": 298,
    }
    assert ratio["median"] == 2
    assert ratio["median_bootstrap_95pct"] == [2, 2]
    assert "32 pairs, not per op" in result["latency_unit"]
    assert result["outlier_removal"] is False


def test_missing_or_failed_pair_is_incomparable(
    measured_rows: list[dict[str, Any]],
) -> None:
    measured_rows[-1]["status"] = "ERROR"

    assert compare._paired_ratios(measured_rows, "lmdb")["status"] == "INCOMPARABLE"
    assert (
        compare._paired_ratios(measured_rows[:-1], "lmdb")["status"] == "INCOMPARABLE"
    )
    assert "ops_per_s" not in compare._analyze_rows([])["groups"][0]


def test_prepare_same_payload_key_shape_and_batch_counts() -> None:
    tables, batches = compare._prepare("atomic-outbox-batch", 4096, 512)

    assert tables == ("business", "outbox")
    assert len(batches) == 16
    assert all(len(batch) == 64 for batch in batches)
    first, second = batches[0][:2]
    assert first[2] == second[2] and len(first[2]) == 4096
    assert first[3] == b"b\x00\x00\x00\x00\x00\x00\x00\x01"
    assert second[3] == b"o\x00\x00\x00\x00\x00\x00\x00\x01"


def test_verify_all_unavailable_preserves_incomparability(
    sealed_failures: Path,
) -> None:
    result = compare._verify(sealed_failures)
    summary = json.loads((sealed_failures / "summary.json").read_bytes())

    assert result == {
        "status": "PASS",
        "trials": 216,
        "trial_failures": 216,
        "preflight_failures": 6,
    }
    assert all(
        group["status"] == "INCOMPARABLE" and "ops_per_s" not in group
        for group in summary["groups"]
    )
    assert (
        "ACK queue" in summary["boundary"] and "concurrent RMW" in summary["boundary"]
    )


@pytest.mark.parametrize("fault", ["warmup-error", "warmup-missing", "preflight-error"])
def test_failed_prerequisites_exclude_comparison(
    measured_rows: list[dict[str, Any]], fault: str
) -> None:
    controls = [{"engine": engine, "status": "PASS"} for engine in compare.ENGINES]
    if fault == "warmup-error":
        measured_rows[1]["status"] = "ERROR"
    elif fault == "warmup-missing":
        measured_rows.pop(1)
    else:
        next(row for row in controls if row["engine"] == "lmdb")["status"] = "ERROR"
    result = compare._analyze_rows(measured_rows, controls)
    group = next(
        row
        for row in result["groups"]
        if row["engine"] == "lmdb"
        and row["workload"] == "write"
        and row["payload_bytes"] == 256
    )
    ratio = next(
        row
        for row in result["paired_ratios"]
        if row["engine"] == "lmdb"
        and row["workload"] == "write"
        and row["payload_bytes"] == 256
    )
    assert group["status"] == ratio["status"] == "INCOMPARABLE"
    assert "ops_per_s" not in group


def test_verify_cli_fails_on_failed_trials(
    sealed_failures: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["local-db", "verify", "--output", str(sealed_failures)]
    )
    assert compare.main() == 1


@pytest.mark.parametrize("mutation", ["tamper", "missing", "drop", "summary", "extra"])
def test_verify_rejects_tamper_missing_drop_and_inconsistent_aggregate(
    sealed_failures: Path, mutation: str
) -> None:
    root = sealed_failures
    if mutation == "tamper":
        (root / "trials/0000/stderr.log").write_text("changed")
    elif mutation == "missing":
        (root / "trials/0000/stdout.json").unlink()
        compare._seal(root)
    elif mutation == "drop":
        manifest = json.loads((root / "manifest.json").read_bytes())
        manifest["schedule"].pop()
        compare._write_json(root / "manifest.json", manifest)
        compare._seal(root)
    elif mutation == "summary":
        (root / "summary.json").write_text("{}\n")
        compare._seal(root)
    else:
        (root / "extra.log").write_text("unexpected")

    with pytest.raises(ValueError):
        compare._verify(root)


@pytest.mark.parametrize("engine", compare.ENGINES)
@pytest.mark.parametrize("workload", compare.WORKLOADS)
def test_actual_small_worker_exact_audit_and_durability(
    tmp_path: Path, engine: str, workload: str
) -> None:
    root = SCRIPT.parents[1]
    deps = Path(
        os.environ.get(
            "LOCAL_DB_COMPARE_TEST_DEPS",
            root / ".local/runs/local-db-comparison/deps-db",
        )
    )
    library = Path(
        os.environ.get(
            "TOTEDB_LIB",
            root
            / "build/totedb-native-release/lib"
            / ("libtotedb.dylib" if sys.platform == "darwin" else "libtotedb.so"),
        )
    )
    if engine in {"duckdb", "lmdb", "rocksdb"} and not deps.exists():
        pytest.skip("native dependency smoke requires explicitly installed pinned deps")
    if engine == "tote" and not library.exists():
        pytest.skip("native smoke requires prebuilt release library")
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join((str(root / "python"), str(deps))),
        TOTEDB_LIB=str(library),
        PYTHONNOUSERSITE="1",
        LOCAL_DB_DEPS=str(deps),
    )
    condition = {
        "engine": engine,
        "workload": workload,
        "payload_bytes": 256,
        "warmup": True,
        "repeat": 0,
    }

    result = compare._run_trial(condition, tmp_path / engine, env, smoke=True)

    assert result["status"] == "PASS", (
        result,
        (tmp_path / engine / "stderr.log").read_text(),
    )
    compare._validate_pass(result, 4)
    assert result["completed_operations"] == 4
    assert result["completed_commits"] == (
        1 if workload == "atomic-outbox-batch" else 4
    )
    assert result["audit_reopened"]["business"] == 4
    assert result["audit_reopened"]["outbox"] == (0 if workload == "write" else 4)
    assert result["final_files"] == compare._files(tmp_path / engine / "db")
    if engine.startswith("sqlite"):
        assert result["config"]["journal_mode"] == (
            "wal" if "wal" in engine else "delete"
        )
        assert result["config"]["synchronous"] == 2
    elif engine == "duckdb":
        assert result["config"]["settings"]["threads"] == "1"
    elif engine == "lmdb":
        assert result["config"]["info"]["map_size"] == 256 * 1024 * 1024
    bad = copy.deepcopy(result)
    bad["audit_reopened"]["sha256"] = "fabricated"
    with pytest.raises(ValueError, match="exact-content"):
        compare._validate_pass(bad, 4)


def test_existing_collect_output_rejected_before_native_access(tmp_path: Path) -> None:
    args = compare.argparse.Namespace(output=tmp_path, run_id="test")

    with pytest.raises(ValueError, match="already exists"):
        compare._collect(args)


def test_missing_engine_import_records_unavailable_without_speed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(engine: str, root: Path) -> Any:
        raise ImportError("deliberately missing native wheel")

    monkeypatch.setattr(compare, "_open_database", unavailable)
    condition = {
        "engine": "lmdb",
        "workload": "write",
        "payload_bytes": 256,
        "repeat": 0,
        "warmup": True,
    }

    result = compare._worker(condition, tmp_path, 4)

    assert result["status"] == "UNAVAILABLE"
    assert result["completed_operations"] == 0
    assert "elapsed_ns" not in result and "ops_per_s" not in result
    assert (tmp_path / "db").is_dir()


def test_timeout_kills_process_group_and_preserves_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    killed = []

    class TimedOutProcess:
        pid = 244

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.calls = 0
            kwargs["stderr"].write(b"partial engine diagnostics\n")

        def wait(self, timeout: int | None = None) -> int:
            self.calls += 1
            if self.calls == 1:
                raise compare.subprocess.TimeoutExpired("worker", timeout)
            return -9

    monkeypatch.setattr(compare.subprocess, "Popen", TimedOutProcess)
    monkeypatch.setattr(
        compare.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )
    condition = {
        "engine": "lmdb",
        "workload": "write",
        "payload_bytes": 256,
        "repeat": 1,
        "warmup": False,
    }

    result = compare._run_trial(condition, tmp_path / "timeout", dict(os.environ))

    assert result["status"] == "TIMEOUT" and result["completed_operations"] is None
    assert killed == [(244, compare.signal.SIGKILL)]
    assert (
        tmp_path / "timeout/stderr.log"
    ).read_bytes() == b"partial engine diagnostics\n"
    assert (tmp_path / "timeout/stdout.json").is_file()
