"""Validate diagnostic trial evidence and compare identical paired conditions."""

from __future__ import annotations

import dataclasses
import math
import statistics
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bench.queue_contention.trial import Trial

PREFLIGHT_JOBS = 12
OPERATIONS = ("put", "claim", "ack", "outbox", "empty_claim")
VARIANT_ENGINES = {"baseline": "totedb", "candidate": "totedb", "control": "sqlite-wal"}


def raw_problems(raw: dict[str, Any] | None, spec: Trial) -> list[str]:
    if not isinstance(raw, dict):
        return ["missing trial record"]
    problems = []
    if raw.get("status") != "ok" or raw.get("problems") != []:
        problems.append("trial is not ok")
    audit = raw.get("audit")
    if (
        not isinstance(audit, dict)
        or audit.get("problems") != []
        or type(audit.get("rows")) is not int
        or audit["rows"] != spec.condition.jobs
    ):
        problems.append("reopen audit failed or missing")
    if (
        type(raw.get("completed_jobs")) is not int
        or raw["completed_jobs"] != spec.condition.jobs
    ):
        problems.append("incomplete jobs")
    if (
        raw.get("condition") != dataclasses.asdict(spec.condition)
        or raw.get("engine") != spec.engine
    ):
        problems.append("trial identity mismatch")
    elapsed = raw.get("elapsed_s")
    if not _nonnegative_number(elapsed) or elapsed <= 0:
        problems.append("invalid elapsed time")
    workers = raw.get("workers")
    if (
        not isinstance(workers, list)
        or not workers
        or not all(isinstance(w, dict) for w in workers)
    ):
        return [*problems, "invalid worker evidence"]
    if any(_invalid_worker(w) for w in workers):
        problems.append("invalid worker metrics or worker errors")
    if isinstance(audit, dict):
        problems.extend(_identity_problems(raw, spec))
    return problems


def summarize(
    records: list[dict[str, Any]], workers: list[int], repetitions: int
) -> dict[str, Any]:
    conditions = {
        str(w): _condition_summary(
            [r for r in records if r["condition"]["workers"] == w], repetitions
        )
        for w in workers
    }
    return {
        "schema": 1,
        "status": "comparable"
        if all(c["status"] == "comparable" for c in conditions.values())
        else "incomparable",
        "bootstrap": {"seed": 226, "samples": 10000, "confidence": 0.95},
        "conditions": conditions,
        "performance_verdict": "not decided automatically; inspect CI, repeat ranges and native traces",
    }


def _nonnegative_number(value: Any) -> bool:
    if type(value) not in (int, float) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _invalid_worker(worker: dict[str, Any]) -> bool:
    if "error" not in worker or worker["error"] is not None:
        return True
    if type(worker.get("busy")) is not int or not _nonnegative_number(worker["busy"]):
        return True
    if not _nonnegative_number(worker.get("retry_sleep_s")):
        return True
    latency = worker.get("latency_s")
    if not isinstance(latency, dict) or not all(op in latency for op in OPERATIONS):
        return True
    return any(
        not isinstance(samples, list)
        or not all(_nonnegative_number(v) for v in samples)
        for samples in latency.values()
    )


def _identity_problems(raw: dict[str, Any], spec: Trial) -> list[str]:
    try:
        workers = raw["workers"]
        items = raw["audit"]["items"]
        completed = [row for worker in workers for row in worker["acked"]]
        identities = {row[0]: (row[1], row[2]) for row in items}
        if len(items) != spec.condition.jobs or len(identities) != spec.condition.jobs:
            return ["audit ids missing or duplicated"]
        if sorted(row[1] for row in items) != list(range(spec.condition.jobs)):
            return ["audit payload indices missing or duplicated"]
        if len({row[0] for row in completed}) != spec.condition.jobs:
            return ["completed ids missing or duplicated"]
        if sorted(row[1] for row in completed) != list(range(spec.condition.jobs)):
            return ["completed payload indices missing or duplicated"]
        if any(identities.get(row[0]) != (row[1], row[-1]) for row in completed):
            return ["completed id/payload association mismatch"]
    except (KeyError, TypeError, IndexError):
        return ["invalid identity evidence"]
    return []


def _distribution(values: list[float]) -> dict[str, Any]:
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "n": len(values),
    }


def _trial_metrics(record: dict[str, Any]) -> dict[str, Any]:
    from bench.queue_contention.analysis import _percentile

    raw = record["raw"]
    latency = {}
    for operation in OPERATIONS:
        samples = [v for w in raw["workers"] for v in w["latency_s"][operation]]
        latency[operation] = {
            key: _percentile(samples, q)
            for key, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
        }
    return {
        "variant": record["variant"],
        "repetition": record["repetition"],
        "elapsed_s": raw["elapsed_s"],
        "latency_s": latency,
        "completed_jobs": raw["completed_jobs"],
        "errors": [w["error"] for w in raw["workers"] if w["error"]],
        "busy": sum(w["busy"] for w in raw["workers"]),
        "retry_sleep_s": sum(w["retry_sleep_s"] for w in raw["workers"]),
    }


def _condition_summary(
    records: list[dict[str, Any]], repetitions: int
) -> dict[str, Any]:
    from bench.queue_contention.analysis import _bootstrap

    names = tuple(VARIANT_ENGINES)
    measured = [r for r in records if r["phase"] == "measured"]
    preflight = [r for r in records if r["phase"] == "preflight"]
    expected = {(v, i) for v in names for i in range(repetitions)}
    valid_keys = (
        len(measured) == len(expected)
        and {(r["variant"], r["repetition"]) for r in measured} == expected
    )
    valid_preflight = len(preflight) == 3 and {r["variant"] for r in preflight} == set(
        names
    )
    if (
        not valid_keys
        or not valid_preflight
        or any(r["status"] != "ok" for r in records)
    ):
        return {
            "status": "incomparable",
            "reason": "missing, duplicate or failed trial; no failed observations discarded",
        }
    common = measured[0]["condition"]
    expected_preflight = {**common, "jobs": min(PREFLIGHT_JOBS, common["jobs"])}
    if any(
        r["condition"] != (common if r["phase"] == "measured" else expected_preflight)
        or r["engine"] != VARIANT_ENGINES.get(r["variant"])
        for r in records
    ):
        return {
            "status": "incomparable",
            "reason": "condition or variant engine mismatch",
        }
    if any(_has_invalid_raw(r) for r in records):
        return {"status": "incomparable", "reason": "raw record or audit failed"}
    by_key = {(r["variant"], r["repetition"]): r["raw"]["elapsed_s"] for r in measured}
    ratios = [
        by_key[("baseline", i)] / by_key[("candidate", i)] for i in range(repetitions)
    ]
    return {
        "status": "comparable",
        "paired_throughput_candidate_over_baseline": {
            **_distribution(ratios),
            "values": ratios,
            "bootstrap_95_ci": _bootstrap(ratios),
            "definition": "median of paired baseline elapsed / candidate elapsed, not ratio of medians",
        },
        "elapsed_s": {
            v: _distribution([by_key[(v, i)] for i in range(repetitions)])
            for v in names
        },
        "trials": [_trial_metrics(r) for r in measured],
    }


def _has_invalid_raw(record: dict[str, Any]) -> bool:
    from bench.queue_contention.trial import Condition, Trial

    try:
        spec = Trial(Condition(**record["condition"]), record["engine"], Path("."))
        return bool(raw_problems(record["raw"], spec))
    except (KeyError, TypeError, ValueError):
        return True
