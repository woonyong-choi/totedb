from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def additional_analysis(database: dict, queues: dict, operations: int) -> dict:
    grouped = {
        (row["engine"], row["workload"], row["payload_bytes"]): row
        for row in database["groups"]
    }
    batching = []
    for engine, workload, payload in grouped:
        if workload != "atomic-outbox-batch":
            continue
        single = grouped[engine, "atomic-outbox", payload]
        batch = grouped[engine, workload, payload]
        batching.append(
            {
                "engine": engine,
                "payload_bytes": payload,
                "single_ops_s_median": single["ops_per_s"]["median"],
                "batch_ops_s_median": batch["ops_per_s"]["median"],
                "ratio_of_medians": batch["ops_per_s"]["median"]
                / single["ops_per_s"]["median"],
            }
        )
    api_costs = []
    for group in queues["groups"]:
        trials = [
            row
            for row in queues["trials"]
            if not row["warmup"]
            and row["status"] == "ok"
            and all(row[key] == group[key] for key in ("engine", "n", "payload_bytes"))
        ]
        costs = {}
        for phase in ("put", "read", "ack"):
            means = [
                row["timing"][phase]["total_ns"] / row["timing"][phase]["count"]
                for row in trials
                if row["timing"][phase]["count"] > 0
            ]
            if means:
                costs[phase] = {
                    "median": statistics.median(means),
                    "min": min(means),
                    "max": max(means),
                }
        api_costs.append(
            {
                **{key: group[key] for key in ("engine", "n", "payload_bytes")},
                "mean_call_wall_ns_across_trials": costs,
            }
        )
    return {
        "classification": "post-collection descriptive analysis, not a preregistered causal test",
        "batch_scaling": batching,
        "batch_scaling_caveat": "ratio of workload medians; separate time blocks, not isolated fsync or paired causal effect",
        "queue_api_costs": api_costs,
        "static_success_path": {
            "scope": "no idempotency key, default next-item claim, matching fence, first ACK, no retries",
            "sql_statements": {"put": 1, "get": 3, "ack": 2},
            "own_transactions_per_job": 3,
            "evidence_kind": "source-path inspection, not dynamic SQL or syscall instrumentation",
        },
        "batch_protocol": {
            "logical_operations": operations,
            "pairs_per_commit": 32,
            "commit_calls": operations // 32,
            "insert_calls_per_commit": 64,
        },
    }


def publish(artifact: Path, output: Path) -> None:
    if output.resolve().is_relative_to(artifact.resolve()):
        raise ValueError("public output must remain outside the immutable artifact")
    db_path = artifact / "local-db-comparison/summary.json"
    queue_path = artifact / "python-queue-comparison/summary.json"
    db, queue = load(db_path), load(queue_path)
    manifest = load(artifact / "local-db-comparison/manifest.json")
    backend_configs = {}
    for index, condition in enumerate(manifest["schedule"]):
        row = load(
            artifact / "local-db-comparison/trials" / f"{index:04d}" / "result.json"
        )
        if row["status"] == "PASS":
            backend_configs.setdefault(condition["engine"], row["config"])
    compact = {key: value for key, value in queue.items() if key != "raw_trials"}
    compact["trials"] = [
        {
            key: row[key]
            for key in (
                "id",
                "engine",
                "n",
                "payload_bytes",
                "repeat",
                "warmup",
                "status",
                "returncode",
                "timeout",
                "jobs_per_s",
                "total_ns",
                "phase_wall_ns",
                "timing",
            )
            if key in row
        }
        for row in queue["raw_trials"]
    ]
    compact["backend_contracts"] = {
        engine: {
            "capabilities": row["capabilities"],
            "pragmas": row["pragmas"],
            "pragmas_after_reopen": row["pragmas_after_reopen"],
            "probes": row["probes"],
            "version": row["package"].get(
                "version", row["package"].get("native_version")
            ),
        }
        for engine in ("totedb", "persist-queue", "litequeue", "diskcache", "huey")
        for row in [
            next(
                row
                for row in queue["raw_trials"]
                if row["engine"] == engine and row["status"] == "ok"
            )
        ]
    }
    write(
        output / "results/summary.json",
        {
            "schema": 1,
            "database": db,
            "database_configurations": backend_configs,
            "queues": compact,
            "additional_analysis": additional_analysis(
                db, compact, manifest["operations"]
            ),
            "raw_summary_sha256": {
                "database": digest(db_path),
                "queues": digest(queue_path),
            },
            "counts": {
                "db_measured": sum(not row["warmup"] for row in manifest["schedule"]),
                "db_warmup": sum(row["warmup"] for row in manifest["schedule"]),
                "db_api_preflight": len(manifest["preflight"]),
                "queue_measured": sum(not row["warmup"] for row in queue["raw_trials"]),
                "queue_warmup": sum(row["warmup"] for row in queue["raw_trials"]),
            },
            "compression": "full payloads and package file hashes remain in the immutable artifact; metrics are unchanged",
        },
    )
    write(
        output / "env.json",
        {
            "python": manifest["python"],
            "platform": manifest["platform"],
            "cpu": load(artifact / "local-comparison-cpu.json"),
            "before": load(artifact / "local-comparison-before.json"),
            "after": load(artifact / "local-comparison-after.json"),
            "uname": (artifact / "local-comparison-uname.txt").read_text(),
            "meminfo": (artifact / "local-comparison-memory.txt").read_text(),
            "build": manifest["identity_before"]["build_fingerprint"],
            "library_sha256": manifest["identity_before"]["library_sha256"],
            "package_freeze": (artifact / "local-comparison-freeze.txt").read_text(),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    publish(args.artifact, args.output)


if __name__ == "__main__":
    main()
