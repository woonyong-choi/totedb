#!/usr/bin/env python3
"""Real Python queue packages: successful-path timing, never reliability equivalence.

collect fixes the formal 120-trial matrix; worker permits small validation runs.
All DBs, failed trials and stderr remain in the explicitly supplied output tree.
Huey measures SqliteHuey.storage, not task decoration, execution or workers.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
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
DEPS = ROOT / ".local/runs/local-db-comparison/deps-queue"
ENGINES = ("totedb", "persist-queue", "litequeue", "diskcache", "huey")
PINS = {
    "persist-queue": "1.1.0",
    "litequeue": "0.14",
    "diskcache": "5.6.3",
    "huey": "3.4.0",
}
SOURCES = {
    "persist-queue": "https://github.com/peter-wangxu/persist-queue/tree/v1.1.0",
    "litequeue": "https://github.com/litements/litequeue/blob/main/README.md",
    "diskcache": "https://grantjenks.com/docs/diskcache/tutorial.html",
    "huey": "https://huey.readthedocs.io/en/latest/api.html#SqliteStorage",
}
LABEL = "successful path only; no reliability equivalence"
PROTOCOL = {
    "schema": 1,
    "sizes": [128, 1024],
    "payload_bytes": [256, 4096],
    "engines": list(ENGINES),
    "repeats": 5,
    "warmups": 1,
    "clients": 1,
    "processes_per_trial": 1,
    "timeout_s": 180,
    "label": LABEL,
    "serialization": "canonical JSON; dumps in put, loads in read; UTF-8 bytes for Tote/Huey, text for litequeue/diskcache/persist; generation excluded",
    "retention": "ACK rows retained through reopen audit, then public purge outside timing; FIFO removes on read",
    "throughput": "n / (put loop wall time + consume/ACK loop wall time); includes per-call timing overhead, excludes setup/probes/audit/purge",
}


class JSONSerializer:
    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def loads(value: str | bytes) -> Any:
        return json.loads(value)


class QueueAdapter:
    """Use only public queue operations; private connections are PRAGMA-only."""

    def __init__(self, engine: str, directory: Path) -> None:
        self.engine = engine
        self.directory = directory
        self.db: Any = None
        self.queue: Any = None
        self.conn: Any = None
        self.pragmas: dict[str, Any] = {}
        self.tier = "ACK" if engine in ENGINES[:3] else "FIFO"
        directory.mkdir(parents=True, exist_ok=True)
        if engine == "totedb":
            from totedb import Database, Queue

            self.db = Database(directory / "queue.totedb", busy_timeout_s=120)
            self.queue = Queue(self.db, "jobs", idem_retention_ms=0)
            self.pragmas = {"durability": "native COW fsync; not SQLite WAL/FULL"}
        elif engine == "persist-queue":
            import persistqueue

            self.queue = persistqueue.SQLiteAckQueue(
                str(directory / "queue"),
                multithreading=False,
                auto_resume=True,
                serializer=JSONSerializer,
            )
            self.conn = self.queue._putter
            self._configure_sqlite()
        elif engine == "litequeue":
            from litequeue import LiteQueue

            self.queue = LiteQueue(directory / "queue.sqlite")
            self.conn = self.queue.conn
            self._configure_sqlite()
        elif engine == "diskcache":
            from diskcache import Cache, Deque

            cache_dir = str(directory / "queue")
            with Cache(cache_dir, sqlite_synchronous=2, sqlite_journal_mode="wal"):
                self.queue = Deque(directory=cache_dir)
            self.conn = self.queue.cache._con
            self._read_pragmas()
        elif engine == "huey":
            from huey import SqliteHuey

            self.queue = SqliteHuey(
                "jobs",
                filename=str(directory / "queue.sqlite"),
                fsync=True,
                journal_mode="wal",
                strict_fifo=True,
            ).storage
            self.conn = self.queue.conn
            self._read_pragmas()
        else:
            raise ValueError(f"unknown engine: {engine}")
        if engine != "totedb" and (
            self.pragmas["synchronous"] != 2 or self.pragmas["journal_mode"] != "wal"
        ):
            raise ValueError(f"cannot establish actual WAL/FULL: {self.pragmas}")

    def _configure_sqlite(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self._read_pragmas()

    def _read_pragmas(self) -> None:
        self.pragmas = {
            key: self.conn.execute(f"PRAGMA {key}").fetchone()[0]
            for key in (
                "journal_mode",
                "synchronous",
                "cache_size",
                "page_size",
                "mmap_size",
                "wal_autocheckpoint",
            )
        }

    def put(self, item: dict[str, Any]) -> Any:
        if self.engine == "persist-queue":
            return self.queue.put(item)
        encoded = JSONSerializer.dumps(item)
        if self.engine == "totedb":
            return self.queue.put(encoded.encode()).id
        if self.engine == "litequeue":
            return self.queue.put(encoded).message_id
        if self.engine == "diskcache":
            return self.queue.append(encoded)
        return self.queue.enqueue(encoded.encode())

    def read(self) -> tuple[Any, dict[str, Any]] | None:
        if self.engine == "totedb":
            claim = self.queue.get(600_000)
            return (
                None if claim is None else (claim, JSONSerializer.loads(claim.payload))
            )
        if self.engine == "persist-queue":
            from persistqueue.exceptions import Empty

            try:
                claim = self.queue.get(block=False, raw=True)
            except Empty:
                return None
            return claim, claim["data"]
        if self.engine == "litequeue":
            claim = self.queue.pop()
            return None if claim is None else (claim, JSONSerializer.loads(claim.data))
        if self.engine == "diskcache":
            try:
                value = self.queue.popleft()
            except IndexError:
                return None
        else:
            value = self.queue.dequeue()
            if value is None:
                return None
        return None, JSONSerializer.loads(value)

    def ack(self, claim: Any) -> None:
        if self.engine == "totedb":
            self.queue.ack(claim)
        elif self.engine == "persist-queue":
            if self.queue.ack(id=claim["pqid"]) is None:
                raise ValueError("persist-queue ack rejected")
        elif self.engine == "litequeue":
            if not self.queue.done(claim.message_id, claim_id=claim.claim_id):
                raise ValueError("litequeue done rejected")
        else:
            raise ValueError("FIFO has no ACK API")

    def retry(self, claim: Any) -> None:
        if self.engine == "totedb":
            self.queue.nack(claim)
        elif self.engine == "persist-queue":
            if self.queue.nack(id=claim["pqid"]) is None:
                raise ValueError("persist-queue nack rejected")
        elif self.engine == "litequeue":
            if not self.queue.retry(claim.message_id, claim_id=claim.claim_id):
                raise ValueError("litequeue retry rejected")
        else:
            raise ValueError("FIFO retry unsupported")

    def fail(self, claim: Any) -> None:
        if self.engine == "totedb":
            self.queue.fail(claim, "probe")
        elif self.engine == "persist-queue":
            if self.queue.ack_failed(id=claim["pqid"]) is None:
                raise ValueError("persist-queue failure rejected")
        elif self.engine == "litequeue":
            if not self.queue.mark_failed(claim.message_id, claim_id=claim.claim_id):
                raise ValueError("litequeue failure rejected")
        else:
            raise ValueError("FIFO failure unsupported")

    def counts(self) -> dict[str, int]:
        if self.engine == "totedb":
            stats = self.queue.stats()
            return {
                "pending": stats.ready + stats.leased,
                "completed": stats.acked,
                "failed": stats.failed,
            }
        if self.engine == "persist-queue":
            from persistqueue.sqlackqueue import AckStatus

            rows = self.queue.queue()
            return {
                "pending": sum(
                    str(row["status"])
                    in (AckStatus.inited, AckStatus.ready, AckStatus.unack)
                    for row in rows
                ),
                "completed": sum(str(row["status"]) == AckStatus.acked for row in rows),
                "failed": sum(
                    str(row["status"]) == AckStatus.ack_failed for row in rows
                ),
            }
        if self.engine == "litequeue":
            return {
                "pending": self.queue.active_count(),
                "completed": self.queue.done_count(),
                "failed": self.queue.failed_count(),
            }
        pending = (
            len(self.queue) if self.engine == "diskcache" else self.queue.queue_size()
        )
        return {"pending": pending, "completed": 0, "failed": 0}

    def purge(self, n: int) -> None:
        if self.engine == "totedb":
            while self.queue.purge(1024, include_failed=True):
                continue
        elif self.engine == "persist-queue":
            self.queue.clear_acked_data(max_delete=n, keep_latest=0)
        elif self.engine == "litequeue":
            self.queue.prune()

    def close(self) -> None:
        if self.engine == "totedb":
            self.db.close()
        elif self.engine == "diskcache":
            self.queue.cache.close()
        else:
            self.queue.close()


def synthetic(index: int, body_bytes: int) -> dict[str, Any]:
    chunks = []
    digest = hashlib.sha256(f"queue-job-{index}".encode()).hexdigest()
    for _ in range((body_bytes + 63) // 64):
        chunks.append(digest)
        digest = hashlib.sha256(digest.encode()).hexdigest()
    return {"id": index, "body": "".join(chunks)[:body_bytes]}


def latency(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "total_ns": 0, "p50_ns": 0, "p95_ns": 0, "p99_ns": 0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "total_ns": sum(values),
        **{
            f"p{percent}_ns": ordered[
                max(0, math.ceil(len(values) * percent / 100) - 1)
            ]
            for percent in (50, 95, 99)
        },
    }


def capabilities(engine: str) -> dict[str, Any]:
    return {
        "tier": "ACK" if engine in ENGINES[:3] else "FIFO",
        "lease_fence": {
            "totedb": "600000 ms lease and fence; release_leases probe is explicit operator recovery",
            "persist-queue": "id-only ACK; no lease/fence; auto_resume requeues unack on reopen",
            "litequeue": "integer claim_id fence; retry_expired age threshold, no automatic lease expiry on pop",
            "diskcache": "none; popleft removes before application completion",
            "huey": "none at storage layer; dequeue deletes before task execution",
        }[engine],
        "caller_owned_business_transaction": {
            "totedb": "supported: native Queue txn=Database.transaction; not measured here",
            "persist-queue": "unsupported by public queue API: internal auto-commit connections",
            "litequeue": "unsupported unrelated application tables since 0.10; queue.transaction exists",
            "diskcache": "unverified SQL business transaction; Deque.transact supports grouped cache operations",
            "huey": "unverified caller-owned business transaction; storage owns commit boundary",
        }[engine],
        "claim_reopen_recovery_supported": engine in ENGINES[:3],
        "configuration": {
            "totedb": "native Database+Queue; lease_ms=600000; idem_retention_ms=0; no idem key; max_attempts=0",
            "persist-queue": "SQLiteAckQueue; multithreading=False; auto_resume=True; default auto_commit=True; canonical JSON serializer; shared getter/putter connection PRAGMA-configured outside timing",
            "litequeue": "LiteQueue; maxsize=None; writer conn WAL/FULL configured outside timing; query-only reader pool unchanged; no idem key",
            "diskcache": "Deque maxlen=None; Cache(sqlite_synchronous=2, sqlite_journal_mode=wal); text stored without pickle; no eviction/expiry",
            "huey": "SqliteHuey.storage only; fsync=True maps synchronous=2; journal_mode=wal; strict_fifo=True; no worker/task/result execution",
        }[engine],
        "external_exactly_once": False,
        "label": LABEL,
        "sqlite_statement_cache": "0 (library-managed)"
        if engine == "litequeue"
        else "CPython default (not overridden by adapter)"
        if engine != "totedb"
        else "native; Python sqlite cache N/A",
    }


def probe(engine: str, directory: Path) -> dict[str, Any]:
    item = synthetic(0, 256)
    adapter = QueueAdapter(engine, directory)
    result: dict[str, Any] = {"fail": None, "requeue": None, "claim_reopen": None}
    try:
        adapter.put(item)
        adapter.close()
        adapter = QueueAdapter(engine, directory)
        first = adapter.read()
        if first is None or first[1] != item:
            raise ValueError("probe queued payload did not survive reopen")
        result["queued_reopen"] = True
        if adapter.tier == "FIFO":
            result["unsupported_reason"] = (
                "destructive dequeue; no ACK, failure or claimed-message recovery API"
            )
            result["claim_reopen"] = False
        else:
            adapter.retry(first[0])
            second = adapter.read()
            if second is None or second[1] != item:
                raise ValueError("probe retry lost payload")
            result["requeue"] = True
            adapter.close()
            adapter = QueueAdapter(engine, directory)
            if engine == "totedb":
                adapter.queue.release_leases()
            elif engine == "litequeue":
                adapter.queue.retry_expired(threshold_seconds=0)
            recovered = adapter.read()
            if recovered is None or recovered[1] != item:
                raise ValueError("probe claim recovery lost payload")
            result["claim_reopen"] = True
            adapter.fail(recovered[0])
            if adapter.counts()["failed"] != 1 or adapter.counts()["pending"] != 0:
                raise ValueError("probe failed-state audit failed")
            result["fail"] = True
        return result
    finally:
        adapter.close()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_metadata(engine: str) -> dict[str, Any]:
    if engine == "totedb":
        from totedb._native import load_library

        library = Path(load_library()._name).resolve()
        return {
            "native_library": str(library),
            "native_version": load_library().tote_version().decode(),
            "native_sha256": file_hash(library),
            "native_benchmark": _native_identity(library),
            "python_sources_sha256": {
                str(p.relative_to(ROOT)): file_hash(p)
                for p in sorted((ROOT / "python/totedb").glob("*.py"))
            },
        }
    distribution = importlib.metadata.distribution(engine)
    if not Path(distribution.locate_file("")).resolve().is_relative_to(DEPS.resolve()):
        raise ModuleNotFoundError(
            f"pinned package must be installed in isolated target: {DEPS}"
        )
    if distribution.version != PINS[engine]:
        raise ValueError(f"package pin mismatch: {engine} {distribution.version}")
    files = distribution.files or []
    hashes = {
        str(p): file_hash(Path(distribution.locate_file(p)))
        for p in files
        if str(p).endswith((".py", "/RECORD", "/METADATA"))
    }
    return {
        "version": distribution.version,
        "source": SOURCES[engine],
        "pypi": f"https://pypi.org/pypi/{engine}/{distribution.version}/json",
        "sources_checked_on": "2026-10-19",
        "requires_python": distribution.metadata.get("Requires-Python"),
        "installed_files_sha256": hashes,
    }


def _native_identity(library: Path) -> dict[str, Any]:
    subprocess.run(
        [sys.executable, str(ROOT / "tools/verify_benchmark.py"), str(library)],
        check=True,
        capture_output=True,
    )
    fingerprint = (library.parent.parent / ".build-fingerprint").read_text()
    flags = next(
        line for line in fingerprint.splitlines() if line.startswith("SHARED_CFLAGS=")
    )
    if (
        "BENCHMARK=0\n" not in fingerprint
        or "-O2" not in flags
        or "sanitize" in fingerprint
    ):
        raise ValueError(
            "native library requires -O2 release BENCHMARK=0 without sanitizers"
        )
    diff = subprocess.run(
        [
            "git",
            "diff",
            "a86cf241",
            "--",
            "src",
            "include",
            "Makefile",
            "python",
            "setup.py",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    if diff:
        raise ValueError("product source differs from a86cf241")
    sources = [
        path
        for folder in ("src", "include", "python/totedb")
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and path.suffix in (".c", ".h", ".py")
    ]
    sources.extend(
        ROOT / name for name in ("Makefile", "setup.py", "tools/verify_benchmark.py")
    )
    return {
        "enabled": False,
        "build_fingerprint": fingerprint,
        "verify_benchmark": "passed",
        "base": "a86cf241",
        "sources_sha256": {
            str(path.relative_to(ROOT)): file_hash(path) for path in sorted(sources)
        },
    }


def run_worker(
    engine: str, directory: Path, n: int, payload_bytes: int
) -> dict[str, Any]:
    if n <= 0 or payload_bytes <= 0:
        raise ValueError("n and payload bytes must be positive")
    directory.mkdir(parents=True, exist_ok=False)
    metadata = package_metadata(engine)
    probes = probe(engine, directory / "probe")
    items = [synthetic(index, payload_bytes) for index in range(n)]
    encoded = [JSONSerializer.dumps(item).encode() for item in items]
    expected_digest = hashlib.sha256(b"".join(encoded)).hexdigest()
    adapter = QueueAdapter(engine, directory / "data")
    put_ns: list[int] = []
    read_ns: list[int] = []
    ack_ns: list[int] = []
    consumed: list[dict[str, Any]] = []
    cpu_start = time.process_time_ns()
    try:
        phase_start = time.perf_counter_ns()
        for item in items:
            start = time.perf_counter_ns()
            adapter.put(item)
            put_ns.append(time.perf_counter_ns() - start)
        put_wall_ns = time.perf_counter_ns() - phase_start
        phase_start = time.perf_counter_ns()
        for _ in range(n):
            start = time.perf_counter_ns()
            entry = adapter.read()
            read_ns.append(time.perf_counter_ns() - start)
            if entry is None:
                raise ValueError("queue ended before n payloads")
            if adapter.tier == "ACK":
                start = time.perf_counter_ns()
                adapter.ack(entry[0])
                ack_ns.append(time.perf_counter_ns() - start)
            consumed.append(entry[1])
        consume_wall_ns = time.perf_counter_ns() - phase_start
        cpu_ns = time.process_time_ns() - cpu_start
        validate_payloads(consumed, n, payload_bytes)
        if adapter.read() is not None:
            raise ValueError("queue returned more than n payloads")
        before = adapter.counts()
        actual_pragmas = adapter.pragmas
    finally:
        adapter.close()
    adapter = QueueAdapter(engine, directory / "data")
    try:
        after = adapter.counts()
        reopened_pragmas = adapter.pragmas
        expected_completed = n if adapter.tier == "ACK" else 0
        expected_counts = {"pending": 0, "completed": expected_completed, "failed": 0}
        if before != expected_counts or after != expected_counts:
            raise ValueError(f"before/reopen counts differ: {before}, {after}")
        adapter.purge(n)
        cleanup_counts = adapter.counts()
        if cleanup_counts["pending"] or cleanup_counts["completed"]:
            raise ValueError("public cleanup left completed or pending jobs")
    finally:
        adapter.close()
    total_ns = put_wall_ns + consume_wall_ns
    result = {
        "status": "ok",
        "engine": engine,
        "n": n,
        "payload_bytes": payload_bytes,
        "capabilities": capabilities(engine),
        "probes": probes,
        "package": metadata,
        "pragmas": actual_pragmas,
        "pragmas_after_reopen": reopened_pragmas,
        "timing": {
            "put": latency(put_ns),
            "read": latency(read_ns),
            "ack": latency(ack_ns),
        },
        "raw_ns": {"put": put_ns, "read": read_ns, "ack": ack_ns},
        "total_ns": total_ns,
        "phase_wall_ns": {"put": put_wall_ns, "consume_ack": consume_wall_ns},
        "jobs_per_s": n * 1e9 / total_ns,
        "cpu_ns": cpu_ns,
        "before_close": before,
        "after_reopen": after,
        "after_cleanup": cleanup_counts,
        "payload_audit": {
            "expected_sha256": expected_digest,
            "consumed": consumed,
            "encoded_bytes": [len(value) for value in encoded],
        },
        "environment": environment(directory),
    }
    write_json(directory / "result.json", result)
    return result


def validate_payloads(items: list[dict[str, Any]], n: int, payload_bytes: int) -> None:
    if len(items) != n or items != [
        synthetic(index, payload_bytes) for index in range(n)
    ]:
        raise ValueError(
            "payload audit failed: missing, duplicate, reordered or corrupt jobs"
        )


def environment(directory: Path) -> dict[str, Any]:
    filesystem = os.statvfs(directory)
    return {
        "python": sys.version,
        "executable": sys.executable,
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "filesystem": {
            "path": str(directory.resolve()),
            "device": directory.stat().st_dev,
            "block_size": filesystem.f_bsize,
            "fragment_size": filesystem.f_frsize,
        },
        "rss": "not measured; inherited process high-water marks are not trial memory",
        "script_sha256": file_hash(Path(__file__)),
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def plan() -> list[dict[str, Any]]:
    trials = []
    for n in PROTOCOL["sizes"]:
        for payload_bytes in PROTOCOL["payload_bytes"]:
            for repeat in range(6):
                order = ENGINES[repeat % 5 :] + ENGINES[: repeat % 5]
                for engine in order:
                    trials.append(
                        {
                            "id": f"n{n}-b{payload_bytes}-r{repeat}-{engine}",
                            "engine": engine,
                            "n": n,
                            "payload_bytes": payload_bytes,
                            "repeat": repeat,
                            "warmup": repeat == 0,
                        }
                    )
    return trials


def collect(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    trials = plan()
    write_json(output / "protocol.json", PROTOCOL)
    executions = []
    for trial in trials:
        trial_path = output / "data/trials" / trial["id"]
        trial_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "worker",
            "--engine",
            trial["engine"],
            "--output",
            str(trial_path),
            "--n",
            str(trial["n"]),
            "--payload-bytes",
            str(trial["payload_bytes"]),
        ]
        stderr_path = output / f"{trial['id']}.stderr"
        stdout_path = output / f"{trial['id']}.stdout"
        with stderr_path.open("wb") as stderr, stdout_path.open("wb") as stdout:
            process = subprocess.Popen(
                command, stdout=stdout, stderr=stderr, start_new_session=True
            )
            timed_out = False
            try:
                returncode = process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                timed_out = True
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                returncode = process.wait()
        executions.append(
            {
                **trial,
                "command": command,
                "returncode": returncode,
                "timeout": timed_out,
            }
        )
        write_json(output / "executions.json", executions)
    seal(output)
    write_json(output / "summary.json", analyze(output))
    seal(output)


def seal(output: Path) -> None:
    hashes = {
        str(path.relative_to(output)): file_hash(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path != output / "SHA256SUMS.json"
    }
    write_json(output / "SHA256SUMS.json", hashes)


def verify(output: Path, *, check_summary: bool = True) -> dict[str, Any]:
    hashes = json.loads((output / "SHA256SUMS.json").read_text())
    actual = {
        str(path.relative_to(output))
        for path in output.rglob("*")
        if path.is_file() and path != output / "SHA256SUMS.json"
    }
    if actual != set(hashes):
        raise ValueError("artifact set mismatch: missing or unexpected file")
    for name, digest in hashes.items():
        path = output / name
        if (
            path.resolve().is_relative_to(output.resolve()) is False
            or path.is_symlink()
        ):
            raise ValueError("unsafe manifest path")
        if file_hash(path) != digest:
            raise ValueError(f"artifact hash mismatch: {name}")
    if json.loads((output / "protocol.json").read_text()) != PROTOCOL:
        raise ValueError("protocol mismatch")
    executions = json.loads((output / "executions.json").read_text())
    expected = plan()
    if len(executions) != len(expected):
        raise ValueError("incomplete execution matrix")
    failures = []
    for execution, config in zip(executions, expected, strict=True):
        if any(execution.get(key) != value for key, value in config.items()):
            raise ValueError("trial configuration mismatch")
        for suffix in ("stdout", "stderr"):
            if not (output / f"{config['id']}.{suffix}").is_file():
                raise ValueError("missing trial process log")
        if not isinstance(execution.get("command"), list) or not execution["command"]:
            raise ValueError("missing trial command")
        path = output / "data/trials" / config["id"] / "result.json"
        if execution["returncode"] != 0 or execution["timeout"]:
            failures.append(config["id"])
            continue
        result = json.loads(path.read_text())
        validate_result(result, config)
    audit = {
        "valid": True,
        "expected_trials": len(expected),
        "failed_trials": failures,
        "complete_success": not failures,
    }
    if check_summary and (output / "summary.json").exists():
        if json.loads((output / "summary.json").read_text()) != analyze(output):
            raise ValueError("summary regeneration mismatch")
    return audit


def validate_result(result: dict[str, Any], config: dict[str, Any]) -> None:
    if result.get("status") != "ok":
        raise ValueError("successful execution lacks successful result")
    for key in ("engine", "n", "payload_bytes"):
        if result[key] != config[key]:
            raise ValueError("result configuration mismatch")
    n, body_bytes = config["n"], config["payload_bytes"]
    validate_payloads(result["payload_audit"]["consumed"], n, body_bytes)
    encoded = [
        JSONSerializer.dumps(synthetic(i, body_bytes)).encode() for i in range(n)
    ]
    audit = result["payload_audit"]
    if audit["expected_sha256"] != hashlib.sha256(
        b"".join(encoded)
    ).hexdigest() or audit["encoded_bytes"] != list(map(len, encoded)):
        raise ValueError("encoded payload audit mismatch")
    ack = config["engine"] in ENGINES[:3]
    for phase, count in (("put", n), ("read", n), ("ack", n if ack else 0)):
        values = result["raw_ns"][phase]
        if len(values) != count or any(
            not isinstance(value, int) or value <= 0 for value in values
        ):
            raise ValueError("invalid operation latency samples")
        if result["timing"][phase] != latency(values):
            raise ValueError("latency summary mismatch")
    wall = result["phase_wall_ns"]
    if wall["put"] < sum(result["raw_ns"]["put"]) or wall["consume_ack"] < sum(
        result["raw_ns"]["read"]
    ) + sum(result["raw_ns"]["ack"]):
        raise ValueError("phase wall time shorter than operation samples")
    total = sum(wall.values())
    if result["total_ns"] != total or result["jobs_per_s"] != n * 1e9 / total:
        raise ValueError("throughput mismatch")
    counts = {"pending": 0, "completed": n if ack else 0, "failed": 0}
    if result["before_close"] != counts or result["after_reopen"] != counts:
        raise ValueError("pending/completion audit mismatch")
    if result["after_cleanup"] != {"pending": 0, "completed": 0, "failed": 0}:
        raise ValueError("cleanup audit mismatch")
    probes = result["probes"]
    if probes.get("queued_reopen") is not True:
        raise ValueError("queued reopen probe missing")
    if ack and any(
        probes.get(key) is not True for key in ("fail", "requeue", "claim_reopen")
    ):
        raise ValueError("ACK capability probes missing")
    if not ack and (
        probes.get("claim_reopen") is not False
        or probes.get("requeue") is not None
        or probes.get("fail") is not None
    ):
        raise ValueError("FIFO cannot claim ACK capability probes")
    if result["capabilities"] != capabilities(config["engine"]):
        raise ValueError("contract metadata mismatch")
    if result["pragmas_after_reopen"] != result["pragmas"]:
        raise ValueError("reopened pragma metadata mismatch")
    if config["engine"] != "totedb":
        if (
            result["package"]["version"] != PINS[config["engine"]]
            or result["pragmas"]["synchronous"] != 2
            or result["pragmas"]["journal_mode"] != "wal"
        ):
            raise ValueError("package/durability mismatch")
    elif result["package"]["native_benchmark"]["enabled"]:
        raise ValueError("native backend mismatch")


def summarize_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = []
    for n in PROTOCOL["sizes"]:
        for body_bytes in PROTOCOL["payload_bytes"]:
            baseline = {
                row["repeat"]: row
                for row in rows
                if row["n"] == n
                and row["payload_bytes"] == body_bytes
                and row["engine"] == "totedb"
                and not row["warmup"]
            }
            baseline_warmup_ok = any(
                row["n"] == n
                and row["payload_bytes"] == body_bytes
                and row["engine"] == "totedb"
                and row["warmup"]
                and row["status"] == "ok"
                for row in rows
            )
            for engine in ENGINES:
                selected = [
                    row
                    for row in rows
                    if row["n"] == n
                    and row["payload_bytes"] == body_bytes
                    and row["engine"] == engine
                    and not row["warmup"]
                ]
                successful = [row for row in selected if row["status"] == "ok"]
                speeds = [row["jobs_per_s"] for row in successful]
                complete = (
                    len(selected) == 5
                    and len(successful) == 5
                    and {row["repeat"] for row in selected} == set(range(1, 6))
                    and any(
                        row["n"] == n
                        and row["payload_bytes"] == body_bytes
                        and row["engine"] == engine
                        and row["warmup"]
                        and row["status"] == "ok"
                        for row in rows
                    )
                )
                ratios = []
                if (
                    engine in ENGINES[1:3]
                    and complete
                    and len(baseline) == 5
                    and baseline_warmup_ok
                    and all(row["status"] == "ok" for row in baseline.values())
                ):
                    ratios = [
                        baseline[row["repeat"]]["jobs_per_s"] / row["jobs_per_s"]
                        for row in successful
                    ]
                groups.append(
                    {
                        "engine": engine,
                        "tier": capabilities(engine)["tier"],
                        "n": n,
                        "payload_bytes": body_bytes,
                        "successful": len(successful),
                        "expected": 5,
                        "publishable": complete,
                        "label": LABEL,
                        "phase_latency_ns": {
                            phase: {
                                percentile: _range_summary(
                                    [
                                        row["timing"][phase][percentile]
                                        for row in successful
                                    ]
                                )
                                for percentile in ("p50_ns", "p95_ns", "p99_ns")
                            }
                            for phase in ("put", "read", "ack")
                            if phase != "ack" or engine in ENGINES[:3]
                        }
                        if complete
                        else None,
                        "jobs_per_s": {
                            "median": statistics.median(speeds),
                            "min": min(speeds),
                            "max": max(speeds),
                        }
                        if complete
                        else None,
                        "paired_totedb_over_baseline": {
                            "median": statistics.median(ratios),
                            "min": min(ratios),
                            "max": max(ratios),
                            "values": ratios,
                            "bootstrap_95_ci": bootstrap_ci(ratios),
                            "bootstrap_seed": 244,
                            "pairs": 5,
                            "feature_mismatch_warning": LABEL,
                        }
                        if ratios
                        else None,
                    }
                )
    return groups


def _range_summary(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def bootstrap_ci(values: list[float]) -> list[float]:
    if len(values) != 5 or not all(
        math.isfinite(value) and value > 0 for value in values
    ):
        raise ValueError("five finite positive paired values are required")
    rng = random.Random(244)
    samples = sorted(statistics.median(rng.choices(values, k=5)) for _ in range(10000))
    return [samples[249], samples[9749]]


def analyze(output: Path) -> dict[str, Any]:
    audit = verify(output, check_summary=False)
    executions = json.loads((output / "executions.json").read_text())
    rows = []
    for execution in executions:
        path = output / "data/trials" / execution["id"] / "result.json"
        if execution["returncode"] != 0 or execution["timeout"]:
            evidence = json.loads(path.read_text()) if path.is_file() else {}
            status = (
                "timeout"
                if execution["timeout"]
                else "unavailable"
                if evidence.get("status") == "unavailable"
                else "failed"
            )
            rows.append({**execution, **evidence, "status": status})
        else:
            result = json.loads(path.read_text())
            rows.append({**execution, **result})
    groups = summarize_groups(rows)
    return {
        "label": LABEL,
        "audit": audit,
        "groups": groups,
        "publication_comparisons": [
            {
                "target": "totedb",
                "baseline": group["engine"],
                "tier": "ACK",
                "n": group["n"],
                "payload_bytes": group["payload_bytes"],
                "ratio": group["paired_totedb_over_baseline"],
                "label": LABEL,
                "reliability_equivalent": False,
            }
            for group in groups
            if group["paired_totedb_over_baseline"] is not None
        ],
        "raw_trials": rows,
        "overall_ranking": None,
        "limitations": [
            "single client/process; no concurrency or fault-model equivalence",
            "FIFO dequeue is not ACK completion",
            "Tote COW fsync and SQLite WAL/FULL are distinct implementations",
            "no external exactly-once claim",
            "Huey storage only; no task worker costs",
            "observed successful path is not proof against power failure",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "analyze", "verify", "worker"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        if name == "worker":
            command.add_argument("--engine", choices=ENGINES, required=True)
            command.add_argument("--n", type=int, default=4)
            command.add_argument("--payload-bytes", type=int, default=256)
    args = parser.parse_args()
    sys.path[:0] = [str(DEPS), str(ROOT / "python")]
    output_existed = args.output.exists()
    try:
        if args.command == "worker":
            result = run_worker(args.engine, args.output, args.n, args.payload_bytes)
            print(
                json.dumps(
                    {"status": result["status"], "engine": args.engine, "n": args.n}
                )
            )
        elif args.command == "collect":
            collect(args.output)
            audit = json.loads((args.output / "summary.json").read_text())["audit"]
            return int(not audit["complete_success"])
        else:
            result = (
                analyze(args.output)
                if args.command == "analyze"
                else verify(args.output)
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            audit = result["audit"] if args.command == "analyze" else result
            return int(not audit["complete_success"])
        return 0
    except Exception as error:
        traceback.print_exc()
        if (
            args.command == "worker"
            and not output_existed
            and args.output.is_dir()
            and not (args.output / "result.json").exists()
        ):
            write_json(
                args.output / "result.json",
                {
                    "status": "unavailable"
                    if isinstance(
                        error, (ImportError, importlib.metadata.PackageNotFoundError)
                    )
                    else "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
