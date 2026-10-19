#!/usr/bin/env python3
"""Compare durable local database primitives at the serial Python API boundary."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import importlib.metadata
import json
import logging
import math
import os
import platform
import random
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINES = ("tote", "sqlite-delete-full", "sqlite-wal-full", "duckdb", "lmdb", "rocksdb")
WORKLOADS = ("write", "atomic-outbox", "atomic-outbox-batch")
PINS = {"duckdb": "1.5.6", "lmdb": "3.0.0", "rocksdict": "0.3.29"}
OPERATIONS = 512
TIMEOUT_S = 180
PRODUCT_PATHS = ("src", "include", "Makefile", "python", "setup.py")
logger = logging.getLogger(__name__)


class SQLDatabase:
    def __init__(self, engine: str, path: Path) -> None:
        self.engine = engine
        self.transaction: Any = None
        self.config: dict[str, Any] = {}
        if engine == "tote":
            library = Path(os.environ["TOTEDB_LIB"])
            if not library.is_file() or _digest(library) != os.environ.get(
                "LOCAL_DB_LIBRARY_SHA256", _digest(library)
            ):
                raise ImportError(
                    "explicit native library is missing or changed; fallback is forbidden"
                )
            import totedb

            self.db = totedb.Database(path)
            self.config = {
                "version": totedb.__version__,
                "durability": "product default synchronous commit",
            }
        elif engine == "duckdb":
            module = _import_pinned("duckdb")
            self.db = module.connect(str(path), config={"threads": "1"})
            self.config = {
                "version": module.__version__,
                "settings": dict(
                    self.db.execute(
                        "SELECT name, value FROM duckdb_settings() ORDER BY name"
                    ).fetchall()
                ),
                "durability": "default WAL commit; no SQLite PRAGMAs",
            }
        else:
            self.db = sqlite3.connect(path, isolation_level=None, cached_statements=128)
            mode = "wal" if engine == "sqlite-wal-full" else "delete"
            self.db.execute(f"PRAGMA journal_mode={mode}").fetchall()
            self.db.execute("PRAGMA synchronous=FULL").fetchall()
            self.db.execute("PRAGMA wal_autocheckpoint=1000").fetchall()
            pragmas = (
                "journal_mode",
                "synchronous",
                "wal_autocheckpoint",
                "fullfsync",
                "checkpoint_fullfsync",
            )
            self.config = {
                name: self.db.execute(f"PRAGMA {name}").fetchone()[0]
                for name in pragmas
            }
            self.config.update(
                version=sqlite3.sqlite_version,
                isolation_level=None,
                cached_statements=128,
            )
            self.config["compile_options"] = [
                row[0] for row in self.db.execute("PRAGMA compile_options").fetchall()
            ]
            if self.config["journal_mode"] != mode or self.config["synchronous"] != 2:
                raise ValueError("sqlite durability configuration was not applied")

    def close(self) -> None:
        self.transaction = None
        self.db.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        target = self.transaction if self.transaction is not None else self.db
        result = target.execute(sql, params)
        return list(result.rows) if self.engine == "tote" else result.fetchall()

    def create(self, tables: tuple[str, ...]) -> None:
        for table in tables:
            self.execute(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, payload BLOB NOT NULL)"
            )

    def commit_records(self, records: list[tuple[str, int, bytes, bytes]]) -> None:
        if self.engine == "tote":
            self.transaction = self.db.begin()
        else:
            self.execute("BEGIN")
        for table, identifier, payload, _ in records:
            self.execute(
                f"INSERT INTO {table} (id, payload) VALUES (?, ?)",
                (identifier, payload),
            )
        if self.engine == "tote":
            self.transaction.commit()
            self.transaction = None
        else:
            self.execute("COMMIT")

    def read_records(self, tables: tuple[str, ...]) -> list[tuple[bytes, bytes]]:
        records = []
        for table in tables:
            for identifier, payload in self.execute(f"SELECT id, payload FROM {table}"):
                records.append((_key(table, identifier), bytes(payload)))
        return records


class LMDBDatabase:
    def __init__(self, engine: str, path: Path) -> None:
        module = _import_pinned("lmdb")
        self.db = module.open(
            str(path),
            map_size=256 * 1024 * 1024,
            sync=True,
            metasync=True,
            writemap=False,
            lock=True,
        )
        self.config = {
            "version": module.__version__,
            "linked_version": module.version(),
            "flags": self.db.flags(),
            "info": self.db.info(),
        }

    def close(self) -> None:
        self.db.close()

    def create(self, tables: tuple[str, ...]) -> None:
        return None

    def commit_records(self, records: list[tuple[str, int, bytes, bytes]]) -> None:
        with self.db.begin(write=True) as transaction:
            for _, _, payload, key in records:
                if not transaction.put(key, payload, overwrite=False):
                    raise ValueError("duplicate lmdb key")

    def read_records(self, tables: tuple[str, ...]) -> list[tuple[bytes, bytes]]:
        with self.db.begin() as transaction:
            with transaction.cursor() as cursor:
                return [(bytes(key), bytes(value)) for key, value in cursor]


class RocksDatabase:
    def __init__(self, engine: str, path: Path) -> None:
        self.module = _import_pinned("rocksdict")
        options = self.module.Options(raw_mode=True)
        options.create_if_missing(True)
        self.db = self.module.Rdict(str(path), options)
        self.write_options = self.module.WriteOptions()
        self.write_options.sync = True
        self.write_options.disable_wal = False
        self.config = {
            "version": importlib.metadata.version("rocksdict"),
            "raw_mode": True,
            "sync": self.write_options.sync,
            "disable_wal": self.write_options.disable_wal,
            "compaction": "default",
            "primitive": "atomic WriteBatch, not isolated RMW",
            "linked_version": "not exposed by binding; native module hash recorded",
        }

    def close(self) -> None:
        self.db.close()
        self.db = None
        gc.collect()

    def create(self, tables: tuple[str, ...]) -> None:
        return None

    def commit_records(self, records: list[tuple[str, int, bytes, bytes]]) -> None:
        batch = self.module.WriteBatch(raw_mode=True)
        for _, _, payload, key in records:
            batch.put(key, payload)
        self.db.write(batch, self.write_options)

    def read_records(self, tables: tuple[str, ...]) -> list[tuple[bytes, bytes]]:
        return [(bytes(key), bytes(value)) for key, value in self.db.items()]


def _import_pinned(name: str) -> Any:
    module = importlib.import_module(name)
    actual = importlib.metadata.version(name)
    if actual != PINS[name]:
        raise ImportError(
            f"package version mismatch: {name} expected {PINS[name]}, got {actual}"
        )
    deps = os.environ.get("LOCAL_DB_DEPS")
    if deps and not Path(module.__file__).resolve().is_relative_to(
        Path(deps).resolve()
    ):
        raise ImportError(f"package loaded outside pinned dependency directory: {name}")
    return module


def _encode(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode()


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_encode(value))


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(root: Path) -> dict[str, dict[str, Any]]:
    return {
        str(path.relative_to(root)): {
            "bytes": path.stat().st_size,
            "allocated_bytes": path.stat().st_blocks * 512,
            "sha256": _digest(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _key(table: str, identifier: int) -> bytes:
    return (b"b" if table == "business" else b"o") + identifier.to_bytes(8, "big")


def _prepare(
    workload: str, payload_bytes: int, count: int
) -> tuple[tuple[str, ...], list[Any]]:
    tables = ("business",) if workload == "write" else ("business", "outbox")
    batch_size = 32 if workload == "atomic-outbox-batch" else 1
    records = []
    for identifier in range(1, count + 1):
        seed = hashlib.sha256(b"local-db-244" + identifier.to_bytes(8, "big")).digest()
        payload = (seed * math.ceil(payload_bytes / len(seed)))[:payload_bytes]
        records.extend(
            (table, identifier, payload, _key(table, identifier)) for table in tables
        )
    width = batch_size * len(tables)
    return tables, [
        records[index : index + width] for index in range(0, len(records), width)
    ]


def _audit(db: Any, tables: tuple[str, ...], batches: list[Any]) -> dict[str, Any]:
    expected = sorted(
        (key, payload) for batch in batches for _, _, payload, key in batch
    )
    actual = sorted(db.read_records(tables))
    if actual != expected:
        raise ValueError(
            f"record audit failed: expected {len(expected)} records, got {len(actual)}"
        )
    return _audit_digest(actual)


def _audit_digest(actual: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    digest = hashlib.sha256()
    for key, payload in actual:
        digest.update(key + len(payload).to_bytes(8, "big") + payload)
    return {
        "status": "PASS",
        "records": len(actual),
        "business": sum(key[:1] == b"b" for key, _ in actual),
        "outbox": sum(key[:1] == b"o" for key, _ in actual),
        "sha256": digest.hexdigest(),
    }


def _open_database(engine: str, root: Path) -> Any:
    factory = {"lmdb": LMDBDatabase, "rocksdb": RocksDatabase}.get(engine, SQLDatabase)
    return factory(engine, root / "database")


def _worker(
    condition: dict[str, Any], trial: Path, count: int = OPERATIONS
) -> dict[str, Any]:
    result = {
        **condition,
        "status": "ERROR",
        "requested_operations": count,
        "completed_operations": 0,
        "completed_commits": 0,
        "commit_latency_ns": [],
        "db_root": "db",
    }
    db_root = trial / "db"
    db_root.mkdir()
    tables, batches = _prepare(condition["workload"], condition["payload_bytes"], count)
    db = None
    try:
        db = _open_database(condition["engine"], db_root)
        result["config"] = db.config
        db.create(tables)
        start = time.perf_counter_ns()
        for batch in batches:
            commit_start = time.perf_counter_ns()
            db.commit_records(batch)
            result["commit_latency_ns"].append(time.perf_counter_ns() - commit_start)
            result["completed_commits"] += 1
            result["completed_operations"] += len(batch) // len(tables)
        result["elapsed_ns"] = time.perf_counter_ns() - start
        result["audit_before_close"] = _audit(db, tables, batches)
        result["files_before_close"] = _files(db_root)
        close_start = time.perf_counter_ns()
        db.close()
        db = None
        result["close_ns"] = time.perf_counter_ns() - close_start
        result["files_after_close"] = _files(db_root)
        result["physical_size_after_close"] = {
            field: sum(info[field] for info in result["files_after_close"].values())
            for field in ("bytes", "allocated_bytes")
        }
        reopen_start = time.perf_counter_ns()
        db = _open_database(condition["engine"], db_root)
        result["reopen_ns"] = time.perf_counter_ns() - reopen_start
        result["audit_reopened"] = _audit(db, tables, batches)
        db.close()
        db = None
        result["status"] = "PASS"
    except (ImportError, importlib.metadata.PackageNotFoundError) as error:
        result.update(status="UNAVAILABLE", error=str(error))
        traceback.print_exc()
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    finally:
        if db is not None:
            db.close()
        result["final_files"] = _files(db_root)
    return result


def _schedule() -> list[dict[str, Any]]:
    schedule = []
    for workload in WORKLOADS:
        for payload in (256, 4096):
            for repeat in range(6):
                order = ENGINES[repeat:] + ENGINES[:repeat]
                for engine in order:
                    schedule.append(
                        {
                            "engine": engine,
                            "workload": workload,
                            "payload_bytes": payload,
                            "repeat": repeat,
                            "warmup": repeat == 0,
                        }
                    )
    return schedule


def _run_trial(
    condition: dict[str, Any], trial: Path, env: dict[str, str], *, smoke: bool = False
) -> dict[str, Any]:
    trial.mkdir(parents=True)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_worker",
        "--output",
        str(trial),
        "--condition",
        json.dumps(condition, sort_keys=True),
    ]
    if smoke:
        command += ["--smoke"]
    _write_json(
        trial / "command.json",
        {
            "argv": command,
            "cwd": str(ROOT),
            "env": {
                key: env.get(key)
                for key in (
                    "PYTHONPATH",
                    "TOTEDB_LIB",
                    "PYTHONNOUSERSITE",
                    "LOCAL_DB_DEPS",
                    "LOCAL_DB_LIBRARY_SHA256",
                )
            },
        },
    )
    with (
        (trial / "stdout.json").open("wb") as stdout,
        (trial / "stderr.log").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            exit_code = process.wait(timeout=TIMEOUT_S)
            status = "ERROR"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            exit_code = process.wait()
            status = "TIMEOUT"
    result: dict[str, Any] = {
        **condition,
        "status": status,
        "completed_operations": None,
        "completed_commits": None,
        "commit_latency_ns": [],
    }
    try:
        raw = json.loads((trial / "stdout.json").read_bytes())
        if not isinstance(raw, dict) or raw.get("status") not in {
            "PASS",
            "ERROR",
            "TIMEOUT",
            "UNAVAILABLE",
        }:
            raise ValueError("invalid worker result shape")
        if status != "TIMEOUT":
            result = raw
    except (ValueError, UnicodeDecodeError):
        result["error"] = "missing or invalid worker JSON; inspect stdout/stderr"
    result["exit_code"] = exit_code
    if exit_code != 0 and result["status"] == "PASS":
        result.update(status="ERROR", error="worker failed after emitting PASS")
    _write_json(trial / "result.json", result)
    return result


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _statistics(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def _paired_ratios(rows: list[dict[str, Any]], engine: str) -> dict[str, Any]:
    matching = {
        (row["engine"], row["repeat"]): row for row in rows if row["status"] == "PASS"
    }
    ratios = []
    for repeat in range(1, 6):
        tote = matching.get(("tote", repeat))
        other = matching.get((engine, repeat))
        if tote is None or other is None:
            return {
                "status": "INCOMPARABLE",
                "reason": "five matched PASS pairs required",
            }
        ratios.append(other["elapsed_ns"] / tote["elapsed_ns"])
    rng = random.Random(244)
    bootstrap = [statistics.median(rng.choices(ratios, k=5)) for _ in range(10000)]
    return {
        "status": "PASS",
        "tote_ops_per_s_over_engine": ratios,
        **_statistics(ratios),
        "median_bootstrap_95pct": [
            _percentile(bootstrap, 0.025),
            _percentile(bootstrap, 0.975),
        ],
        "bootstrap_samples": 10000,
        "seed": 244,
    }


def _analyze_rows(
    rows: list[dict[str, Any]], preflight: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    measured = [row for row in rows if not row["warmup"]]
    controls = {row["engine"]: row["status"] == "PASS" for row in preflight or []}
    groups = []
    ratios = []
    for workload in WORKLOADS:
        for payload in (256, 4096):
            selected = [
                row
                for row in measured
                if row["workload"] == workload and row["payload_bytes"] == payload
            ]
            eligible = set()
            for engine in ENGINES:
                trials = [row for row in selected if row["engine"] == engine]
                passed = [row for row in trials if row["status"] == "PASS"]
                warmups = [
                    row
                    for row in rows
                    if row["workload"] == workload
                    and row["payload_bytes"] == payload
                    and row["engine"] == engine
                    and row["warmup"]
                ]
                ready = (
                    len(passed) == 5
                    and len(warmups) == 1
                    and warmups[0]["status"] == "PASS"
                    and controls.get(engine, False)
                )
                if ready:
                    eligible.add(engine)
                group: dict[str, Any] = {
                    "engine": engine,
                    "workload": workload,
                    "payload_bytes": payload,
                    "status": "PASS" if ready else "INCOMPARABLE",
                    "pass_count": len(passed),
                    "statuses": [row["status"] for row in trials],
                    "per_trial": [],
                }
                if ready:
                    group["ops_per_s"] = _statistics(
                        [OPERATIONS * 1e9 / row["elapsed_ns"] for row in passed]
                    )
                    group["per_trial"] = [
                        {
                            "repeat": row["repeat"],
                            "ops_per_s": OPERATIONS * 1e9 / row["elapsed_ns"],
                            "transaction_latency_ns": {
                                name: _percentile(row["commit_latency_ns"], fraction)
                                for name, fraction in (
                                    ("p50", 0.5),
                                    ("p95", 0.95),
                                    ("p99", 0.99),
                                )
                            },
                        }
                        for row in passed
                    ]
                groups.append(group)
            ratios.extend(
                {
                    "engine": engine,
                    "workload": workload,
                    "payload_bytes": payload,
                    **(
                        _paired_ratios(selected, engine)
                        if {"tote", engine} <= eligible
                        else {
                            "status": "INCOMPARABLE",
                            "reason": "preflight, warmup and five PASS runs required",
                        }
                    ),
                }
                for engine in ENGINES[1:]
            )
    return {
        "schema_version": 1,
        "groups": groups,
        "paired_ratios": ratios,
        "latency_unit": "one transaction including begin, all inserts/puts and commit; batch workload is 32 pairs, not per op",
        "boundary": "serial Python API durable commit; not SQL/KV overall superiority, ACK queue, concurrent RMW or power-loss proof",
        "warmups_excluded": True,
        "outlier_removal": False,
    }


def _validate_pass(row: dict[str, Any], count: int = OPERATIONS) -> None:
    commits = math.ceil(count / (32 if row["workload"] == "atomic-outbox-batch" else 1))
    if row["completed_operations"] != count or row["completed_commits"] != commits:
        raise ValueError("incomplete PASS operation counts")
    latencies = row["commit_latency_ns"]
    if len(latencies) != commits or any(value <= 0 for value in latencies):
        raise ValueError("invalid PASS transaction latencies")
    if row["elapsed_ns"] < sum(latencies):
        raise ValueError("elapsed time excludes transaction calls")
    _, batches = _prepare(row["workload"], row["payload_bytes"], count)
    expected = _audit_digest(
        sorted((key, payload) for batch in batches for _, _, payload, key in batch)
    )
    if row["audit_before_close"] != expected or row["audit_reopened"] != expected:
        raise ValueError("invalid PASS exact-content audit")
    if not row["final_files"]:
        raise ValueError("missing PASS database files")
    config = row["config"]
    if row["engine"].startswith("sqlite"):
        mode = "wal" if row["engine"] == "sqlite-wal-full" else "delete"
        if config["synchronous"] != 2 or config["journal_mode"] != mode:
            raise ValueError("sqlite FULL/journal mode mismatch")
    if row["engine"] == "lmdb" and any(
        config["flags"][key] != value
        for key, value in {
            "sync": True,
            "metasync": True,
            "writemap": False,
            "lock": True,
        }.items()
    ):
        raise ValueError("lmdb durability flags mismatch")
    if row["engine"] == "rocksdb" and (not config["sync"] or config["disable_wal"]):
        raise ValueError("rocksdb synchronous WAL not enabled")
    if row["engine"] == "duckdb" and config["settings"]["threads"] != "1":
        raise ValueError("duckdb thread configuration mismatch")


def _load_trial(trial: Path, condition: dict[str, Any], count: int) -> dict[str, Any]:
    for filename in ("command.json", "stdout.json", "stderr.log", "result.json"):
        if not (trial / filename).is_file():
            raise ValueError(f"missing raw file: {trial / filename}")
    row = json.loads((trial / "result.json").read_bytes())
    if any(row.get(key) != value for key, value in condition.items()):
        raise ValueError("trial condition mismatch")
    if row["status"] == "PASS":
        _validate_pass(row, count)
        raw = json.loads((trial / "stdout.json").read_bytes())
        if row != {**raw, "exit_code": 0}:
            raise ValueError("PASS raw/result mismatch")
        actual = {
            name: (info["bytes"], info["sha256"])
            for name, info in _files(trial / "db").items()
        }
        expected = {
            name: (info["bytes"], info["sha256"])
            for name, info in row["final_files"].items()
        }
        if actual != expected:
            raise ValueError("final database files differ from worker evidence")
    if row["status"] not in {"PASS", "ERROR", "TIMEOUT", "UNAVAILABLE"}:
        raise ValueError("unknown trial status")
    return row


def _load_rows(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest["schedule"] != _schedule():
        raise ValueError("missing or modified trial schedule")
    return [
        _load_trial(root / "trials" / f"{index:04d}", condition, OPERATIONS)
        for index, condition in enumerate(manifest["schedule"])
    ]


def _identity(library: Path) -> dict[str, Any]:
    diff = subprocess.run(
        ["git", "diff", "a86cf241", "--", *PRODUCT_PATHS],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    if diff:
        raise ValueError("product source differs from a86cf241")
    subprocess.run(
        [sys.executable, str(ROOT / "tools/verify_benchmark.py"), str(library)],
        check=True,
        capture_output=True,
    )
    fingerprint = (library.parent.parent / ".build-fingerprint").read_text()
    shared_flags = next(
        line for line in fingerprint.splitlines() if line.startswith("SHARED_CFLAGS=")
    )
    if (
        "BENCHMARK=0\n" not in fingerprint
        or "-O2" not in shared_flags
        or "sanitize" in fingerprint
    ):
        raise ValueError(
            "native library must be prebuilt -O2 release BENCHMARK=0 without sanitizers"
        )
    sources = [
        path
        for folder in ("src", "include", "python/totedb")
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and path.suffix in {".c", ".h", ".py"}
    ]
    sources.extend(
        ROOT / name
        for name in (
            "Makefile",
            "setup.py",
            "bench/local_db_compare.py",
            "tools/verify_benchmark.py",
        )
    )
    return {
        "base": "a86cf241",
        "product_diff": "",
        "native_benchmark": "OFF; verify_benchmark.py succeeded",
        "build_fingerprint": fingerprint,
        "library_sha256": _digest(library),
        "sources": {
            str(path.relative_to(ROOT)): _digest(path) for path in sorted(sources)
        },
    }


def _package_evidence(deps: Path) -> dict[str, Any]:
    paths = sorted(
        path
        for path in deps.rglob("*")
        if path.is_file()
        and (
            path.suffix in {".so", ".dylib", ".pyd"}
            or path.name in {"METADATA", "RECORD", "direct_url.json"}
        )
    )
    return {str(path.relative_to(deps)): _digest(path) for path in paths}


def _seal(root: Path) -> None:
    entries = [
        f"{_digest(path)}  {path.relative_to(root)}\n"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (root / "SHA256SUMS").write_text("".join(entries))


def _verify(root: Path) -> dict[str, Any]:
    expected = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or name in expected:
            raise ValueError("unsafe or duplicate seal path")
        expected[name] = digest
    actual = {
        str(path.relative_to(root)): _digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if actual != expected:
        raise ValueError("missing, extra or tampered sealed file")
    manifest = json.loads((root / "manifest.json").read_bytes())
    if manifest["operations"] != OPERATIONS or len(manifest["schedule"]) != 216:
        raise ValueError("formal operation/trial count mismatch")
    if (
        manifest["identity_before"] != manifest["identity_after"]
        or manifest["packages_before"] != manifest["packages_after"]
    ):
        raise ValueError("source/library/packages changed during collection")
    identity = manifest["identity_before"]
    if _digest(root / manifest["native_library"]) != identity["library_sha256"]:
        raise ValueError("native library evidence mismatch")
    if (
        _digest(root / "evidence/requirements.lock")
        != manifest["installation_lock_sha256"]
    ):
        raise ValueError("installation lock evidence mismatch")
    if len(manifest["preflight"]) != len(ENGINES):
        raise ValueError("incomplete preflight")
    for engine in ENGINES:
        row = _load_trial(root / "preflight" / engine, {"engine": engine}, 4)
        if {key: row[key] for key in ("engine", "status", "exit_code")} not in manifest[
            "preflight"
        ]:
            raise ValueError("preflight manifest/result mismatch")
    rows = _load_rows(root, manifest)
    if (root / "summary.json").read_bytes() != _encode(
        _analyze_rows(rows, manifest["preflight"])
    ):
        raise ValueError("aggregate bytes inconsistent with raw trials")
    return {
        "status": "PASS",
        "trials": len(rows),
        "trial_failures": sum(row["status"] != "PASS" for row in rows),
        "preflight_failures": sum(
            row["status"] != "PASS" for row in manifest["preflight"]
        ),
    }


def _collect(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if output.exists():
        raise ValueError("output already exists")
    library, deps = args.library.resolve(), args.deps.resolve()
    identity = _identity(library)
    lock = args.requirements_lock.read_bytes()
    packages = _package_evidence(deps)
    output.mkdir(parents=True)
    evidence = output / "evidence"
    evidence.mkdir()
    shutil.copyfile(library, evidence / library.name)
    (evidence / "requirements.lock").write_bytes(lock)
    (evidence / "build-fingerprint").write_text(identity["build_fingerprint"])
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join((str(ROOT / "python"), str(deps))),
        TOTEDB_LIB=str(evidence / library.name),
        PYTHONNOUSERSITE="1",
        LOCAL_DB_DEPS=str(deps),
        LOCAL_DB_LIBRARY_SHA256=identity["library_sha256"],
    )
    manifest = {
        "schema_version": 1,
        "run_id": args.run_id,
        "schedule": _schedule(),
        "operations": OPERATIONS,
        "identity_before": identity,
        "actual_product_identity": identity,
        "native_library": str((evidence / library.name).relative_to(output)),
        "packages_before": packages,
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "command": sys.argv,
        "timeout_s": TIMEOUT_S,
        "clients": 1,
        "installation_lock_sha256": hashlib.sha256(lock).hexdigest(),
        "filesystem": {"block_size": os.statvfs(output).f_bsize},
        "durability_scope": "normal commit and exact close/reopen audit, not process crash or power loss",
    }
    _write_json(output / "manifest.json", manifest)
    preflight = []
    for engine in ENGINES:
        condition = {
            "engine": engine,
            "workload": "atomic-outbox-batch",
            "payload_bytes": 256,
            "repeat": 0,
            "warmup": True,
        }
        preflight.append(
            _run_trial(condition, output / "preflight" / engine, env, smoke=True)
        )
    manifest["preflight"] = [
        {
            "engine": row["engine"],
            "status": row["status"],
            "exit_code": row["exit_code"],
        }
        for row in preflight
    ]
    for index, condition in enumerate(manifest["schedule"]):
        _run_trial(condition, output / "trials" / f"{index:04d}", env)
    manifest["identity_after"] = _identity(library)
    manifest["packages_after"] = _package_evidence(deps)
    _write_json(output / "manifest.json", manifest)
    rows = _load_rows(output, manifest)
    _write_json(output / "summary.json", _analyze_rows(rows, manifest["preflight"]))
    _seal(output)
    verification = _verify(output)
    print(json.dumps(verification, sort_keys=True))
    return int(
        verification["trial_failures"] != 0 or verification["preflight_failures"] != 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--run-id", required=True)
    collect.add_argument("--library", type=Path, required=True)
    collect.add_argument("--deps", type=Path, required=True)
    collect.add_argument("--requirements-lock", type=Path, required=True)
    for name in ("analyze", "verify"):
        subparser = commands.add_parser(name)
        subparser.add_argument(
            "--output",
            type=Path,
            required=True,
            help="existing collection root (read-only)",
        )
    worker = commands.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--output", type=Path, required=True)
    worker.add_argument("--condition", required=True)
    worker.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "_worker":
            condition = json.loads(args.condition)
            if (
                condition["engine"] not in ENGINES
                or condition["workload"] not in WORKLOADS
            ):
                raise ValueError("unknown worker condition")
            result = _worker(condition, args.output, 4 if args.smoke else OPERATIONS)
            sys.stdout.buffer.write(_encode(result))
            return int(result["status"] != "PASS")
        if args.command == "collect":
            return _collect(args)
        if args.command == "verify":
            verification = _verify(args.output)
            sys.stdout.buffer.write(_encode(verification))
        else:
            verification = _verify(args.output)
            manifest = json.loads((args.output / "manifest.json").read_bytes())
            sys.stdout.buffer.write(
                _encode(
                    _analyze_rows(
                        _load_rows(args.output, manifest), manifest["preflight"]
                    )
                )
            )
        return int(
            verification["trial_failures"] != 0
            or verification["preflight_failures"] != 0
        )
    except Exception as error:
        logger.error("%s: %s", type(error).__name__, error)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
