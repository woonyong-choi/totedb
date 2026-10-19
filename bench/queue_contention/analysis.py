from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

from bench.queue_contention.adapters import ENGINES, ContentionError


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode()


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def summarize(
    records: list[dict[str, object]], repetitions: int
) -> tuple[bytes, bytes]:
    grouped = defaultdict(list)
    for record in records:
        if record["phase"] == "measured":
            condition = record["condition"]
            key = (
                condition["workload"],
                condition["workers"],
                condition["payload_bytes"],
                condition["jobs"],
            )
            grouped[key].append(record)
    runs = [_run_metrics(record) for record in records]
    comparisons = []
    aggregates = []
    for key, trials in sorted(grouped.items()):
        condition = dict(zip(("workload", "workers", "payload_bytes", "jobs"), key))
        preflights = [
            r
            for r in records
            if r["phase"] == "preflight" and r["condition"] == condition
        ]
        valid = _comparable(trials, preflights, repetitions)
        for engine in ENGINES:
            engine_runs = [
                r
                for r in runs
                if r["phase"] == "measured"
                and r["condition"] == condition
                and r["engine"] == engine
            ]
            aggregates.append(
                {
                    "condition": condition,
                    "engine": engine,
                    "comparable": valid,
                    "throughput_jobs_s": _distribution(
                        [r["throughput_jobs_s"] for r in engine_runs]
                    ),
                    "latency_s": _latency_aggregate(engine_runs),
                }
            )
        for baseline in ENGINES[1:]:
            ratios = _paired_ratios(trials, baseline) if valid else []
            comparisons.append(
                {
                    "condition": condition,
                    "baseline": baseline,
                    "status": "comparable" if valid else "incomparable",
                    "pairs": len(ratios),
                    "metric": "totedb / sqlite throughput",
                    "median_ratio": statistics.median(ratios) if ratios else None,
                    "bootstrap_95_ci": _bootstrap(ratios) if ratios else None,
                }
            )
    summary = {
        "schema": 1,
        "workload_origin": "synthetic queue workload; not an official YCSB workload",
        "bootstrap": {"samples": 10000, "seed": 226, "confidence": 0.95},
        "runs": runs,
        "aggregates": aggregates,
        "comparisons": comparisons,
        "totedb_mixed_worker_scaling": _worker_scaling(records, repetitions),
    }
    return json_bytes(summary), _csv(runs)


def verify_output(root: Path, *, summaries: bool = True) -> dict[str, object]:
    candidates = list((root / "data/raw").glob("*/manifest.json"))
    if len(candidates) != 1:
        raise ContentionError("expected exactly one manifest")
    manifest_path = candidates[0]
    output = manifest_path.parent
    manifest = json.loads(manifest_path.read_bytes())
    if manifest["state"] != "complete":
        raise ContentionError("collection is incomplete")
    hashes = manifest["hashes"]
    actual = {
        str(path.relative_to(root)) for path in output.rglob("*") if path.is_file()
    }
    expected = set(hashes) - {"env.json"} | {str(manifest_path.relative_to(root))}
    if actual != expected:
        raise ContentionError("evidence file set mismatch")
    for name, expected_hash in hashes.items():
        if digest(root / name) != expected_hash:
            raise ContentionError("evidence hash mismatch: " + name)
    checksums = "".join(
        f"{value}  {name}\n"
        for name, value in sorted(
            {
                **hashes,
                str(manifest_path.relative_to(root)): digest(manifest_path),
            }.items()
        )
    )
    if (root / "data/SHA256SUMS").read_text() != checksums:
        raise ContentionError("checksum set mismatch")
    records = [
        json.loads(line) for line in (output / "raw.jsonl").read_text().splitlines()
    ]
    if len(records) != manifest["expected_records"]:
        raise ContentionError("raw record count mismatch")
    _validate_records(manifest, records)
    if not summaries:
        return {"status": "verified", "records": len(records), "hashes": len(hashes)}
    generated = summarize(records, manifest["matrix"]["repetitions"])
    for name, body in zip(("summary.json", "summary.csv"), generated):
        if (root / "results" / name).read_bytes() != body:
            raise ContentionError("summary regeneration mismatch: " + name)
    return {"status": "verified", "records": len(records), "hashes": len(hashes)}


def _validate_records(
    manifest: dict[str, object], records: list[dict[str, object]]
) -> None:
    matrix = manifest["matrix"]
    phases = [("preflight", 0)] + (
        [("profile", 0)]
        if manifest["kind"] == "profile"
        else [("measured", repeat) for repeat in range(matrix["repetitions"])]
    )
    expected = {
        (phase, repeat, engine, work, workers, size, matrix["jobs"])
        for (phase, repeat), engine, work, workers, size in itertools.product(
            phases,
            matrix["engines"],
            matrix["workloads"],
            matrix["workers"],
            matrix["payloads"],
        )
    }
    observed = []
    for record in records:
        condition = record["condition"]
        key = (
            record["phase"],
            record["repetition"],
            record["engine"],
            condition["workload"],
            condition["workers"],
            condition["payload_bytes"],
            condition["jobs"],
        )
        observed.append(key)
        identifier = hashlib.sha256(json_bytes(condition)).hexdigest()
        if (
            record["run_id"] != manifest["run_id"]
            or record["input_identifier"] != identifier
        ):
            raise ContentionError("raw run/input identity mismatch")
    if len(observed) != len(expected) or set(observed) != expected:
        raise ContentionError("raw trial matrix has missing or duplicated conditions")


def _run_metrics(record: dict[str, object]) -> dict[str, object]:
    workers = record.get("workers", [])
    latency = {}
    for operation in ("put", "claim", "ack", "outbox", "empty_claim"):
        samples = [
            sample for worker in workers for sample in worker["latency_s"][operation]
        ]
        latency[operation] = {
            "count": len(samples),
            **{
                name: _percentile(samples, quantile)
                for name, quantile in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
            },
        }
    fairness = {}
    for role in ("producer", "consumer"):
        counts = [
            len(w["put"] if role == "producer" else w["acked"])
            for w in workers
            if w["role"] == role
        ]
        fairness[role] = {
            "counts": counts,
            "min": min(counts, default=0),
            "max": max(counts, default=0),
            "jain_index": (
                sum(counts) ** 2 / (len(counts) * sum(n * n for n in counts))
            )
            if counts and any(counts)
            else None,
        }
    fields = (
        "trial_id",
        "phase",
        "repetition",
        "condition",
        "engine",
        "status",
        "problems",
        "elapsed_s",
        "setup_s",
        "file_sizes",
        "completed_jobs",
        "throughput_jobs_s",
        "started_at",
        "finished_at",
        "resources_before",
        "resources_after",
    )
    return {
        **{name: record.get(name) for name in fields},
        "latency_s": latency,
        "worker_fairness": fairness,
        "busy": sum(w["busy"] for w in workers),
        "empty": sum(w["empty"] for w in workers),
        "retry_sleep_s": sum(w["retry_sleep_s"] for w in workers),
        "errors": [w["error"] for w in workers if w["error"]],
    }


def _comparable(
    trials: list[dict[str, object]],
    preflights: list[dict[str, object]],
    repetitions: int,
) -> bool:
    if len(trials) != len(ENGINES) * repetitions or len(preflights) != len(ENGINES):
        return False
    if any(record["status"] != "ok" for record in trials + preflights):
        return False
    keys = {(r["engine"], r["repetition"]) for r in trials}
    return keys == {
        (engine, repeat) for engine in ENGINES for repeat in range(repetitions)
    }


def _paired_ratios(trials: list[dict[str, object]], baseline: str) -> list[float]:
    by_key = {(r["engine"], r["repetition"]): r["throughput_jobs_s"] for r in trials}
    repeats = sorted({r["repetition"] for r in trials})
    return [
        by_key[("totedb", repeat)] / by_key[(baseline, repeat)] for repeat in repeats
    ]


def _worker_scaling(
    records: list[dict[str, object]], repetitions: int
) -> list[dict[str, object]]:
    rows = [
        r
        for r in records
        if r["engine"] == "totedb" and r["condition"]["workload"] == "mixed"
    ]
    inputs = sorted(
        {(r["condition"]["payload_bytes"], r["condition"]["jobs"]) for r in rows}
    )
    results = []
    for size, jobs in inputs:
        selected = [
            r
            for r in rows
            if r["condition"]["payload_bytes"] == size
            and r["condition"]["jobs"] == jobs
            and r["condition"]["workers"] in (1, 8)
        ]
        measured = {
            (r["condition"]["workers"], r["repetition"]): r
            for r in selected
            if r["phase"] == "measured"
        }
        preflights = [r for r in selected if r["phase"] == "preflight"]
        keys = {
            (workers, repeat) for workers in (1, 8) for repeat in range(repetitions)
        }
        valid = (
            set(measured) == keys
            and len(preflights) == 2
            and all(r["status"] == "ok" for r in selected)
        )
        ratios = (
            [
                measured[(8, repeat)]["throughput_jobs_s"]
                / measured[(1, repeat)]["throughput_jobs_s"]
                for repeat in range(repetitions)
            ]
            if valid
            else []
        )
        results.append(
            {
                "payload_bytes": size,
                "jobs": jobs,
                "metric": "totedb mixed workers 8 / 1 throughput",
                "status": "comparable" if valid else "incomparable",
                "pairs": len(ratios),
                "median_ratio": statistics.median(ratios) if ratios else None,
                "bootstrap_95_ci": _bootstrap(ratios) if ratios else None,
            }
        )
    return results


def _bootstrap(ratios: list[float]) -> list[float]:
    generator = random.Random(226)
    medians = sorted(
        statistics.median(generator.choices(ratios, k=len(ratios)))
        for _ in range(10000)
    )
    return [_percentile(medians, 0.025), _percentile(medians, 0.975)]


def _percentile(samples: list[float], quantile: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    fraction = position - lower
    return (
        ordered[lower] * (1 - fraction)
        + ordered[min(lower + 1, len(ordered) - 1)] * fraction
    )


def _distribution(values: list[float | None]) -> dict[str, float | None]:
    present = [value for value in values if value is not None]
    return {
        "median": statistics.median(present) if present else None,
        "min": min(present, default=None),
        "max": max(present, default=None),
    }


def _latency_aggregate(runs: list[dict[str, object]]) -> dict[str, object]:
    return {
        operation: {
            quantile: _distribution([r["latency_s"][operation][quantile] for r in runs])
            for quantile in ("p50", "p95", "p99")
        }
        for operation in ("put", "claim", "ack", "outbox", "empty_claim")
    }


def _csv(runs: list[dict[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    names = (
        "trial_id",
        "phase",
        "repetition",
        "engine",
        "workload",
        "workers",
        "payload_bytes",
        "jobs",
        "status",
        "elapsed_s",
        "setup_s",
        "file_sizes",
        "completed_jobs",
        "throughput_jobs_s",
        "busy",
        "empty",
        "retry_sleep_s",
        "errors",
        "latency_s",
        "worker_fairness",
        "resources_before",
        "resources_after",
    )
    writer = csv.DictWriter(buffer, fieldnames=names, lineterminator="\n")
    writer.writeheader()
    for run in runs:
        merged = {**run, **run["condition"]}
        writer.writerow(
            {
                name: json.dumps(merged[name])
                if name
                in (
                    "file_sizes",
                    "errors",
                    "latency_s",
                    "worker_fairness",
                    "resources_before",
                    "resources_after",
                )
                else merged[name]
                for name in names
            }
        )
    return buffer.getvalue().encode()
