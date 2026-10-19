"""Protocol arithmetic, evidence integrity and actual package smoke checks."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "python_queue_compare", ROOT / "bench/python_queue_compare.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load benchmark module")
QUEUE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUEUE)


def test_paired_bootstrap_uses_five_values() -> None:
    assert QUEUE.bootstrap_ci([2.0] * 5) == [2.0, 2.0]
    with pytest.raises(ValueError):
        QUEUE.bootstrap_ci([2.0] * 4)


def test_verify_cli_reports_trial_failures(
    evidence: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        QUEUE,
        "verify",
        lambda output: {
            "valid": True,
            "complete_success": False,
            "failed_trials": ["failed"],
        },
    )
    monkeypatch.setattr(sys, "argv", ["queue", "verify", "--output", str(evidence)])
    assert QUEUE.main() == 1


def test_verify_rejects_resealed_incorrect_summary(evidence: Path) -> None:
    summary = QUEUE.analyze(evidence)
    summary["overall_ranking"] = ["fabricated"]
    QUEUE.write_json(evidence / "summary.json", summary)
    QUEUE.seal(evidence)
    with pytest.raises(ValueError, match="summary regeneration mismatch"):
        QUEUE.verify(evidence)


@pytest.fixture
def compact_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(QUEUE.PROTOCOL, "sizes", [4])
    monkeypatch.setitem(QUEUE.PROTOCOL, "payload_bytes", [8])


@pytest.fixture
def evidence(tmp_path: Path, compact_protocol: None) -> Path:
    QUEUE.write_json(tmp_path / "protocol.json", QUEUE.PROTOCOL)
    executions = []
    for config in QUEUE.plan():
        trial = tmp_path / "data/trials" / config["id"]
        trial.mkdir(parents=True)
        QUEUE.write_json(trial / "result.json", _result(config))
        (trial / "queue.sqlite").write_bytes(b"fixture database")
        (tmp_path / f"{config['id']}.stderr").write_text("")
        (tmp_path / f"{config['id']}.stdout").write_text("fixture stdout")
        executions.append(
            {**config, "command": ["fixture"], "returncode": 0, "timeout": False}
        )
    QUEUE.write_json(tmp_path / "executions.json", executions)
    QUEUE.seal(tmp_path)
    return tmp_path


def _result(config: dict[str, Any]) -> dict[str, Any]:
    n, body_bytes = config["n"], config["payload_bytes"]
    ack = config["engine"] in QUEUE.ENGINES[:3]
    scale = 1 if config["engine"] == "totedb" else 2
    raw = {
        "put": [10 * scale] * n,
        "read": [20 * scale] * n,
        "ack": [30 * scale] * n if ack else [],
    }
    items = [QUEUE.synthetic(index, body_bytes) for index in range(n)]
    encoded = [QUEUE.JSONSerializer.dumps(item).encode() for item in items]
    total = sum(map(sum, raw.values()))
    counts = {"pending": 0, "completed": n if ack else 0, "failed": 0}
    package = (
        {"native_benchmark": {"enabled": False}}
        if config["engine"] == "totedb"
        else {"version": QUEUE.PINS[config["engine"]]}
    )
    return {
        "status": "ok",
        "engine": config["engine"],
        "n": n,
        "payload_bytes": body_bytes,
        "raw_ns": raw,
        "timing": {phase: QUEUE.latency(values) for phase, values in raw.items()},
        "total_ns": total,
        "phase_wall_ns": {
            "put": sum(raw["put"]),
            "consume_ack": sum(raw["read"]) + sum(raw["ack"]),
        },
        "jobs_per_s": n * 1e9 / total,
        "payload_audit": {
            "consumed": items,
            "expected_sha256": hashlib.sha256(b"".join(encoded)).hexdigest(),
            "encoded_bytes": list(map(len, encoded)),
        },
        "before_close": counts,
        "after_reopen": counts,
        "after_cleanup": {"pending": 0, "completed": 0, "failed": 0},
        "probes": {
            "queued_reopen": True,
            "requeue": True if ack else None,
            "fail": True if ack else None,
            "claim_reopen": ack,
        },
        "capabilities": QUEUE.capabilities(config["engine"]),
        "package": package,
        "pragmas": {"synchronous": 2, "journal_mode": "wal"},
        "pragmas_after_reopen": {"synchronous": 2, "journal_mode": "wal"},
    }


def test_matrix_has_120_trials_and_rotates_engine_order() -> None:
    trials = QUEUE.plan()
    assert len(trials) == 120
    assert len({trial["id"] for trial in trials}) == 120
    assert sum(trial["warmup"] for trial in trials) == 20
    assert [trial["engine"] for trial in trials[5:10]] == list(
        QUEUE.ENGINES[1:] + QUEUE.ENGINES[:1]
    )


def test_latency_uses_nearest_rank_and_total_not_median() -> None:
    assert QUEUE.latency([40, 10, 30, 20]) == {
        "count": 4,
        "total_ns": 100,
        "p50_ns": 20,
        "p95_ns": 40,
        "p99_ns": 40,
    }
    assert QUEUE.latency([])["count"] == 0


def test_analysis_pairs_all_five_repeats_with_tote_ack_only(evidence: Path) -> None:
    summary = QUEUE.analyze(evidence)
    assert summary["audit"]["complete_success"]
    assert summary["overall_ranking"] is None
    persist = next(
        group for group in summary["groups"] if group["engine"] == "persist-queue"
    )
    assert persist["paired_totedb_over_baseline"]["median"] == 2
    assert persist["paired_totedb_over_baseline"]["pairs"] == 5
    assert persist["jobs_per_s"]["median"] == 1e9 / 120
    for group in summary["groups"]:
        if group["tier"] == "FIFO":
            assert group["paired_totedb_over_baseline"] is None


@pytest.mark.parametrize("status", ["failed", "unavailable", "timeout"])
def test_failure_or_unavailable_is_retained_and_prevents_partial_publish(
    evidence: Path, status: str
) -> None:
    executions_path = evidence / "executions.json"
    executions = json.loads(executions_path.read_text())
    failed = next(
        row
        for row in executions
        if row["engine"] == "persist-queue" and row["repeat"] == 2
    )
    failed["returncode"] = 1
    failed["timeout"] = status == "timeout"
    QUEUE.write_json(executions_path, executions)
    QUEUE.write_json(
        evidence / "data/trials" / failed["id"] / "result.json",
        {"status": status, "error": "fixture error preserved"},
    )
    QUEUE.seal(evidence)
    summary = QUEUE.analyze(evidence)
    group = next(row for row in summary["groups"] if row["engine"] == "persist-queue")
    assert not group["publishable"]
    assert group["jobs_per_s"] is None
    assert group["paired_totedb_over_baseline"] is None
    assert summary["audit"]["failed_trials"] == [failed["id"]]
    assert any(row["status"] == status for row in summary["raw_trials"])


def test_missing_pair_does_not_produce_ratio(compact_protocol: None) -> None:
    rows = [
        {**config, **_result(config)}
        for config in QUEUE.plan()
        if config["engine"] != "totedb" or config["repeat"] != 3
    ]
    group = next(
        row for row in QUEUE.summarize_groups(rows) if row["engine"] == "persist-queue"
    )
    assert group["paired_totedb_over_baseline"] is None


@pytest.mark.parametrize("repeat", [0, 2])
def test_nonzero_exit_overrides_success_result_even_for_warmup(
    evidence: Path, repeat: int
) -> None:
    path = evidence / "executions.json"
    executions = json.loads(path.read_text())
    failed = next(
        row
        for row in executions
        if row["engine"] == "persist-queue" and row["repeat"] == repeat
    )
    failed["returncode"] = 1
    QUEUE.write_json(path, executions)
    QUEUE.seal(evidence)
    summary = QUEUE.analyze(evidence)
    group = next(row for row in summary["groups"] if row["engine"] == "persist-queue")
    assert not group["publishable"]
    assert any(
        row["id"] == failed["id"] and row["status"] == "failed"
        for row in summary["raw_trials"]
    )


def test_verify_rejects_changed_root_summary(evidence: Path) -> None:
    QUEUE.write_json(evidence / "summary.json", QUEUE.analyze(evidence))
    QUEUE.seal(evidence)
    (evidence / "summary.json").write_text("tampered")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        QUEUE.verify(evidence)


def test_verify_rejects_resealed_missing_process_log(evidence: Path) -> None:
    next(evidence.glob("*.stderr")).unlink()
    QUEUE.seal(evidence)
    with pytest.raises(ValueError, match="missing trial process log"):
        QUEUE.verify(evidence)


@pytest.mark.parametrize("damage", ["missing", "changed", "extra"])
def test_verify_rejects_missing_changed_or_extra_artifact(
    evidence: Path, damage: str
) -> None:
    path = next(evidence.rglob("queue.sqlite"))
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_bytes(b"modified")
    else:
        (evidence / "unexpected").write_text("extra")
    with pytest.raises(ValueError, match="artifact"):
        QUEUE.verify(evidence)


def test_verify_rejects_resealed_corrupt_payload(evidence: Path) -> None:
    path = next(evidence.rglob("result.json"))
    result = json.loads(path.read_text())
    result["payload_audit"]["consumed"][0]["body"] = "corrupt"
    QUEUE.write_json(path, result)
    QUEUE.seal(evidence)
    with pytest.raises(ValueError, match="payload audit"):
        QUEUE.verify(evidence)


def test_verify_rejects_resealed_root_protocol_change(evidence: Path) -> None:
    protocol = copy.deepcopy(QUEUE.PROTOCOL)
    protocol["clients"] = 9
    QUEUE.write_json(evidence / "protocol.json", protocol)
    QUEUE.seal(evidence)
    with pytest.raises(ValueError, match="protocol mismatch"):
        QUEUE.verify(evidence)


def test_verify_rejects_incomplete_matrix(evidence: Path) -> None:
    executions = json.loads((evidence / "executions.json").read_text())
    QUEUE.write_json(evidence / "executions.json", executions[:-1])
    QUEUE.seal(evidence)
    with pytest.raises(ValueError, match="incomplete"):
        QUEUE.verify(evidence)


@pytest.mark.parametrize("engine", QUEUE.ENGINES)
@pytest.mark.parametrize("body_bytes", [256, 4096])
def test_actual_package_worker_n4_reopens_and_audits(
    engine: str, body_bytes: int
) -> None:
    if not QUEUE.DEPS.is_dir():
        pytest.skip("isolated pinned packages not installed")
    library = ROOT / "build/totedb-native-release/lib/libtotedb.dylib"
    if engine == "totedb" and not library.exists() and not os.environ.get("TOTEDB_LIB"):
        pytest.skip("native release build unavailable")
    directory = (
        ROOT / ".local/runs/local-db-comparison/queue-test-runs" / str(uuid.uuid4())
    )
    directory.mkdir(parents=True)
    env = {**os.environ, "TOTEDB_LIB": os.environ.get("TOTEDB_LIB", str(library))}
    command = [
        sys.executable,
        str(ROOT / "bench/python_queue_compare.py"),
        "worker",
        "--engine",
        engine,
        "--n",
        "4",
        "--payload-bytes",
        str(body_bytes),
        "--output",
        str(directory / "trial"),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=180, env=env
    )
    (directory / "stderr.log").write_text(completed.stderr)
    (directory / "stdout.log").write_text(completed.stdout)
    QUEUE.write_json(
        directory / "execution.json",
        {"command": command, "returncode": completed.returncode},
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads((directory / "trial/result.json").read_text())
    QUEUE.validate_result(
        result, {"engine": engine, "n": 4, "payload_bytes": body_bytes}
    )
    assert result["probes"]["queued_reopen"] is True
    if engine in QUEUE.ENGINES[:3]:
        assert result["probes"]["requeue"] is True
        assert result["probes"]["fail"] is True
        assert result["probes"]["claim_reopen"] is True
    else:
        assert result["probes"]["claim_reopen"] is False
        assert result["probes"]["requeue"] is None
    QUEUE.seal(directory)


def test_worker_refuses_existing_output_without_modifying_it(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bench/python_queue_compare.py"),
            "worker",
            "--engine",
            "litequeue",
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 1
    assert not list(tmp_path.iterdir())
