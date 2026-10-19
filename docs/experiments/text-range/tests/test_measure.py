from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

SPEC = importlib.util.spec_from_file_location(
    "range_measure", Path(__file__).resolve().parents[1] / "scripts/measure.py"
)
MEASURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MEASURE)


def test_failed_collection_preserves_failure_manifest_without_retry(
    monkeypatch: Any,
) -> None:
    writes = {}
    calls = []
    monkeypatch.setenv("BASELINE_ROOT", "/baseline")
    monkeypatch.setenv("CHANGED_ROOT", "/changed")
    monkeypatch.setattr(
        MEASURE,
        "_snapshot",
        lambda root: {"source_sha": str(root), "source_sha256": {}, "build_sha256": {}},
    )
    monkeypatch.setattr(
        MEASURE, "_git", lambda *args: "" if "diff" in args else "product"
    )
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        Path, "write_bytes", lambda path, data: writes.update({path: data})
    )
    monkeypatch.setattr(MEASURE, "_checksums", lambda raw: b"preserved-files\n")

    def fail(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(MEASURE.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="partial raw preserved"):
        MEASURE._collect(Path("/raw"), Path("/hashes"), "synthetic")
    manifest = json.loads(writes[Path("/raw/manifest.json")])
    assert manifest["collections"] == [
        {
            "repetition": 1,
            "source_phase": "before",
            "child": "r1-before",
            "exit_code": 7,
        }
    ]
    assert len(calls) == 1
    assert writes[Path("/hashes")] == b"preserved-files\n"


def test_missing_paired_collection_rejects_analysis(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        MEASURE,
        "_json",
        lambda path: {"schedule": MEASURE._schedule(), "collections": [{}] * 9},
    )
    with pytest.raises(ValueError, match="incomplete paired collection matrix"):
        MEASURE._records(Path("/synthetic"))


@pytest.mark.parametrize("change", ["content", "file_set"])
def test_raw_tamper_rejects_verification(monkeypatch: Any, change: str) -> None:
    class MemoryFile:
        def __init__(self, name: str, content: bytes) -> None:
            self.name = name
            self.content = content

        def read_bytes(self) -> bytes:
            return self.content

        def relative_to(self, raw: Any) -> Path:
            return Path(self.name)

        def is_file(self) -> bool:
            return True

        def __lt__(self, other: Any) -> bool:
            return self.name < other.name

    class MemoryRaw:
        def rglob(self, pattern: str) -> list[MemoryFile]:
            return files

    files = [MemoryFile("record.json", b"original")]
    raw = MemoryRaw()
    checksum = MemoryFile("SHA256SUMS", MEASURE._checksums(raw))
    MEASURE._check_raw(raw, checksum)
    if change == "content":
        files[0].content = b"modified"
    else:
        files.append(MemoryFile("unexpected.json", b"additional"))
    with pytest.raises(ValueError, match="checksum or file set mismatch"):
        MEASURE._check_raw(raw, checksum)


def test_generated_summary_mismatch_rejects_verify(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        MEASURE,
        "_outputs",
        lambda *args: {Path("results/summary.json"): b'{"expected": 1}\n'},
    )
    monkeypatch.setattr(Path, "read_bytes", lambda path: b'{"expected": 2}\n')
    with pytest.raises(ValueError, match="generated output differs"):
        MEASURE._run("verify", "synthetic-check", "synthetic")


def test_constant_paired_improvement_has_exact_interval() -> None:
    result = MEASURE._bootstrap([3.0] * 5)
    assert result["median"] == 3.0
    assert result["ci95"] == [3.0, 3.0]
    assert result["seed"] == 228


def test_synthetic_summary_preserves_audits_operations_and_paired_effect(
    monkeypatch: Any,
) -> None:
    records = []
    for engine in ("totedb", "sqlite"):
        for phase in ("before", "after"):
            for repetition in range(1, 6):
                throughput = 100.0 if phase == "before" or engine == "sqlite" else 300.0
                records.append(
                    {
                        "phase": "measured",
                        "source_phase": phase,
                        "source_repetition": repetition,
                        "condition": {"engine": engine},
                        "problems": [],
                        "process": {"elapsed_seconds": 11.0, "timeout": False},
                        "metrics": {
                            "logical_operations": 1000,
                            "non_ok": 0,
                            "binding_errors": 0,
                            "binding": {
                                "attempts": 1000,
                                "result_rows": 9000,
                                "output_bytes": 9000000,
                            },
                            "ycsb": {
                                "OVERALL": {
                                    "Throughput(ops/sec)": throughput,
                                    "RunTime(ms)": 10000.0,
                                },
                                "SCAN": {
                                    "Operations": 950,
                                    "50thPercentileLatency(us)": 2,
                                    "95thPercentileLatency(us)": 4,
                                    "99thPercentileLatency(us)": 8,
                                },
                                "INSERT": {
                                    "Operations": 50,
                                    "50thPercentileLatency(us)": 3,
                                    "95thPercentileLatency(us)": 6,
                                    "99thPercentileLatency(us)": 9,
                                },
                            },
                        },
                        "audit_after": {
                            "rows": 10050,
                            "payload_bytes": 10050000,
                            "changed_fields": 0,
                            "added_rows": 50,
                            "missing_rows": 0,
                        },
                    }
                )
    monkeypatch.setattr(MEASURE, "_sha", lambda path: "synthetic-hash")
    summary = MEASURE._summarize(records, {"synthetic": True}, Path("/hashes"))
    assert summary["H2"] == "채택"
    assert summary["engines"]["totedb"]["after_before"]["ci95"] == [3, 3]
    assert summary["engines"]["sqlite"]["after_before"]["ci95"] == [1, 1]
    before = summary["engines"]["totedb"]["before"]
    assert before["audit"]["rows"]["median"] == 10050
    assert before["operations"]["SCAN"]["Operations"]["median"] == 950
    assert before["operations"]["READ"]["Operations"]["n"] == 0
    assert before["returned_bytes"] == 45000000
