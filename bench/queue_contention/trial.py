from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import resource
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from bench.queue_contention.adapters import (
    Adapter,
    ContentionError,
    Retry,
    open_adapter,
    payload,
)

WORKLOADS = ("enqueue", "drain", "mixed", "outbox")


@dataclass(frozen=True)
class Condition:
    workload: str
    workers: int
    payload_bytes: int
    jobs: int

    def key(self) -> str:
        return f"{self.workload}-w{self.workers}-b{self.payload_bytes}-n{self.jobs}"

    def validate(self) -> None:
        if (
            self.workload not in WORKLOADS
            or min(self.workers, self.jobs) < 1
            or self.payload_bytes < 8
        ):
            raise ContentionError("invalid condition")


@dataclass(frozen=True)
class Trial:
    condition: Condition
    engine: str
    directory: Path
    timeout_s: float = 180.0
    fault: str = ""
    evidence_directory: Path | None = None


@dataclass
class WorkerResult:
    worker: int
    role: str
    settings: dict[str, object] = field(default_factory=dict)
    put: list[tuple[int, int, str]] = field(default_factory=list)
    acked: list[tuple[int, int, int, str]] = field(default_factory=list)
    latency_s: dict[str, list[float]] = field(
        default_factory=lambda: {
            "put": [],
            "claim": [],
            "ack": [],
            "outbox": [],
            "empty_claim": [],
        }
    )
    busy: int = 0
    retry_sleep_s: float = 0.0
    empty: int = 0
    error: str | None = None
    ready_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    requests_finished_at: float = 0.0
    requests_finished_perf: float = 0.0
    close_s: float = 0.0


class Progress:
    def __init__(self, producers: int) -> None:
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.gate = threading.Event()
        self.acked = 0
        self.producers = producers

    def produced(self) -> None:
        with self.lock:
            self.producers -= 1

    def acknowledged(self) -> None:
        with self.lock:
            self.acked += 1

    def complete(self, jobs: int) -> bool:
        with self.lock:
            return self.producers == 0 and self.acked >= jobs


def run_trial(spec: Trial) -> dict[str, object]:
    setup_started = time.perf_counter()
    spec.condition.validate()
    spec.directory.mkdir(parents=True, exist_ok=False)
    payloads = tuple(
        payload(index, spec.condition.payload_bytes)
        for index in range(spec.condition.jobs)
    )
    path = spec.directory / "queue.db"
    setup = open_adapter(path, spec.engine, install=True)
    settings = setup.settings()
    if spec.condition.workload == "drain":
        for index in range(spec.condition.jobs):
            setup.put(payloads[index], Retry())
    setup.close()
    roles = _roles(spec.condition)
    progress = Progress(
        spec.condition.workers if spec.condition.workload == "mixed" else 0
    )
    results = [WorkerResult(index, role) for index, role in enumerate(roles)]
    threads = _start_workers(spec, progress, results, payloads)
    setup_s = time.perf_counter() - setup_started
    before = _resources()
    started_at = time.time()
    start = time.perf_counter()
    progress.gate.set()
    for thread in threads:
        thread.join(max(0, spec.timeout_s - (time.perf_counter() - start)))
    joined = time.perf_counter()
    elapsed = max(result.requests_finished_perf for result in results) - start
    after = _resources()
    problems = [result.error for result in results if result.error]
    if any(thread.is_alive() for thread in threads):
        progress.stop.set()
        raise ContentionError("trial worker timeout; controller must terminate process")
    audit = audit_database(spec)
    problems.extend(audit["problems"])
    problems.extend(_check_completions(spec.condition, results, audit))
    return {
        "condition": dataclasses.asdict(spec.condition),
        "engine": spec.engine,
        "status": "failed" if problems else "ok",
        "problems": problems,
        "started_at": started_at,
        "finished_at": time.time(),
        "elapsed_s": elapsed,
        "setup_s": setup_s,
        "cleanup_s": joined - start - elapsed,
        "file_sizes": {
            "phase": "after_close",
            "bytes": {
                "db": path.stat().st_size,
                "wal": Path(str(path) + "-wal").stat().st_size
                if Path(str(path) + "-wal").exists()
                else 0,
                "shm": Path(str(path) + "-shm").stat().st_size
                if Path(str(path) + "-shm").exists()
                else 0,
            },
        },
        "completed_jobs": _completed(spec.condition, results),
        "throughput_jobs_s": _completed(spec.condition, results) / elapsed,
        "resources_before": before,
        "resources_after": after,
        "settings": settings,
        "pid": os.getpid(),
        "measurement_scope": {
            "payload_generation": "before start gate",
            "setup": "trial validation, payload generation, database installation, drain preload and worker readiness",
            "file_sizes": "after all worker and reopen audit connections close; absent files have zero bytes",
            "elapsed": "gate to last worker request completion",
            "includes": "public API, external BUSY retry, payload validation and result bookkeeping",
            "excludes": "connection setup, payload generation, close and reopen audit",
        },
        "workers": [dataclasses.asdict(result) for result in results],
        "audit": audit,
    }


def audit_database(spec: Trial) -> dict[str, object]:
    reopened = open_adapter(spec.directory / "queue.db", spec.engine)
    try:
        rows = reopened.rows()
        business = reopened.business()
    finally:
        reopened.close()
    condition = spec.condition
    consumed = condition.workload in ("drain", "mixed")
    expected_state = 2 if consumed else 0
    expected_fence = 1 if consumed else 0
    indices = []
    problems = []
    digest = hashlib.sha256()
    for item_id, state, fence, attempts, body in rows:
        index = int.from_bytes(body[:8], "big")
        indices.append(index)
        digest.update(item_id.to_bytes(8, "big") + body)
        if body != payload(index, condition.payload_bytes):
            problems.append(f"payload mismatch at row {item_id}")
        if (state, fence, attempts) != (expected_state, expected_fence, expected_fence):
            problems.append(
                f"state/fence/attempts mismatch at row {item_id}: {(state, fence, attempts)}"
            )
    if sorted(indices) != list(range(condition.jobs)):
        problems.append("reopen payload indices missing or duplicated")
    expected_business = [
        (index, payload(index, condition.payload_bytes))
        for index in range(condition.jobs)
    ]
    if business != (expected_business if condition.workload == "outbox" else []):
        problems.append("business transaction atomicity mismatch")
    return {
        "rows": len(rows),
        "business_rows": len(business),
        "row_hash": digest.hexdigest(),
        "items": [
            (item_id, int.from_bytes(body[:8], "big"), hashlib.sha256(body).hexdigest())
            for item_id, _, _, _, body in rows
        ],
        "business_hash": hashlib.sha256(
            b"".join(body for _, body in business)
        ).hexdigest(),
        "problems": problems,
    }


def _roles(condition: Condition) -> list[str]:
    if condition.workload == "mixed":
        return ["producer"] * condition.workers + ["consumer"] * condition.workers
    return [
        "consumer" if condition.workload == "drain" else "producer"
    ] * condition.workers


def _start_workers(
    spec: Trial,
    progress: Progress,
    results: list[WorkerResult],
    payloads: tuple[bytes, ...],
) -> list[threading.Thread]:
    threads = []
    for result in results:
        ready = threading.Event()
        thread = threading.Thread(
            target=_worker_entry,
            args=(spec, progress, result, ready, payloads),
            daemon=True,
        )
        thread.start()
        threads.append(thread)
        if not ready.wait(spec.timeout_s):
            progress.stop.set()
            raise ContentionError("worker setup timeout")
        if result.error:
            progress.stop.set()
            progress.gate.set()
            for running in threads:
                running.join(spec.timeout_s)
            raise ContentionError(result.error)
    return threads


def _worker_entry(
    spec: Trial,
    progress: Progress,
    result: WorkerResult,
    ready: threading.Event,
    payloads: tuple[bytes, ...],
) -> None:
    adapter = None
    retry = Retry()
    try:
        adapter = open_adapter(spec.directory / "queue.db", spec.engine)
        result.settings = adapter.settings()
        result.ready_at = time.time()
        _save_worker(spec, result)
        ready.set()
        progress.gate.wait()
        result.started_at = time.time()
        if spec.fault == "worker-error" and result.worker == 0:
            raise ContentionError("injected worker exception")
        if spec.fault == "timeout":
            time.sleep(spec.timeout_s * 10)
        if result.role == "producer":
            _produce(spec, adapter, retry, result, progress, payloads)
        else:
            _consume(spec, adapter, retry, result, progress, payloads)
    except Exception:
        result.error = traceback.format_exc()
        progress.stop.set()
    finally:
        result.requests_finished_at = time.time()
        result.requests_finished_perf = time.perf_counter()
        result.busy = retry.busy
        result.retry_sleep_s = retry.sleep_s
        close_started = time.perf_counter()
        if adapter is not None:
            try:
                adapter.close()
            except Exception:
                result.error = (result.error or "") + traceback.format_exc()
                progress.stop.set()
        result.finished_at = time.time()
        result.close_s = time.perf_counter() - close_started
        try:
            _save_worker(spec, result)
        except OSError:
            result.error = (result.error or "") + traceback.format_exc()
            progress.stop.set()
        ready.set()


def _save_worker(spec: Trial, result: WorkerResult) -> None:
    if spec.evidence_directory is not None:
        path = spec.evidence_directory / f"worker-{result.worker}.json"
        path.write_text(json.dumps(dataclasses.asdict(result), sort_keys=True) + "\n")


def _produce(
    spec: Trial,
    adapter: Adapter,
    retry: Retry,
    result: WorkerResult,
    progress: Progress,
    payloads: tuple[bytes, ...],
) -> None:
    condition = spec.condition
    operation = "outbox" if condition.workload == "outbox" else "put"
    for index in range(result.worker, condition.jobs, condition.workers):
        if progress.stop.is_set():
            return
        body = payloads[index]
        if spec.fault == "bad-payload" and index == 0:
            body = body[:8] + bytes(len(body) - 8)
        if spec.fault == "missing-job" and index == 0:
            continue
        before = time.perf_counter()
        item_id = (
            adapter.outbox(index, body, retry)
            if operation == "outbox"
            else adapter.put(body, retry)
        )
        result.latency_s[operation].append(time.perf_counter() - before)
        result.put.append((item_id, index, hashlib.sha256(body).hexdigest()))
    if condition.workload == "mixed":
        progress.produced()


def _consume(
    spec: Trial,
    adapter: Adapter,
    retry: Retry,
    result: WorkerResult,
    progress: Progress,
    payloads: tuple[bytes, ...],
) -> None:
    deadline = time.monotonic() + spec.timeout_s
    while not progress.stop.is_set() and not progress.complete(spec.condition.jobs):
        if time.monotonic() >= deadline:
            raise ContentionError("consumer deadline before all jobs acknowledged")
        before = time.perf_counter()
        item = adapter.claim(retry)
        elapsed = time.perf_counter() - before
        if item is None:
            result.empty += 1
            result.latency_s["empty_claim"].append(elapsed)
            time.sleep(0.0005)
            continue
        result.latency_s["claim"].append(elapsed)
        index = int.from_bytes(item.payload[:8], "big")
        if index >= spec.condition.jobs or item.payload != payloads[index]:
            raise ContentionError(f"claimed payload mismatch at {item.id}")
        before = time.perf_counter()
        adapter.ack(item, retry)
        result.latency_s["ack"].append(time.perf_counter() - before)
        result.acked.append(
            (item.id, index, item.fence, hashlib.sha256(item.payload).hexdigest())
        )
        progress.acknowledged()


def _completed(condition: Condition, results: list[WorkerResult]) -> int:
    return sum(
        len(result.acked if condition.workload in ("drain", "mixed") else result.put)
        for result in results
    )


def _check_completions(
    condition: Condition, results: list[WorkerResult], audit: dict[str, object]
) -> list[str]:
    records = [
        row
        for result in results
        for row in (
            result.acked if condition.workload in ("drain", "mixed") else result.put
        )
    ]
    if sorted(row[1] for row in records) != list(range(condition.jobs)):
        return ["completed payload indices missing or duplicated"]
    if len({row[0] for row in records}) != condition.jobs:
        return ["completed queue ids missing or duplicated"]
    identities = {
        item_id: (index, checksum) for item_id, index, checksum in audit["items"]
    }
    if any(identities.get(row[0]) != (row[1], row[-1]) for row in records):
        return ["completed queue id/payload association mismatch"]
    return []


def _resources() -> dict[str, object]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "user_cpu_s": usage.ru_utime,
        "system_cpu_s": usage.ru_stime,
        "peak_rss_bytes": usage.ru_maxrss
        * (1 if os.uname().sysname == "Darwin" else 1024),
        "load_average": list(os.getloadavg()),
    }
