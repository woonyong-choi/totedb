from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import random
import re
import shlex
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = ("src", "include", "bench/ycsb", "Makefile")
PHASES = ("before", "after")


def _analysis() -> Any:
    spec = importlib.util.spec_from_file_location(
        "text_range_ycsb_analysis", ROOT / "bench/ycsb/analysis.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYSIS = _analysis()


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _encoded(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _snapshot(root: Path) -> dict[str, Any]:
    if _git(root, "status", "--porcelain", "--", *SOURCE_PATHS):
        raise ValueError(f"dirty relevant source: {root}")
    sources = _git(root, "ls-files", "--", *SOURCE_PATHS).splitlines()
    config_path = root / "build/ycsb/build.json"
    config = _json(config_path)
    artifacts = [
        Path(config["native"]),
        *map(Path, config["classpath"].split(os.pathsep)),
    ]
    artifacts += list((root / "build/lib").glob("*"))
    files = [config_path]
    for artifact in artifacts:
        if not artifact.exists():
            raise ValueError(f"missing prepared build artifact: {artifact}")
        if not artifact.resolve().is_relative_to((root / "build").resolve()):
            raise ValueError(f"build artifact outside source worktree: {artifact}")
        files.extend(artifact.rglob("*") if artifact.is_dir() else [artifact])
    newest_source = max((root / name).stat().st_mtime_ns for name in sources)
    if config_path.stat().st_mtime_ns < newest_source:
        raise ValueError(
            "run existing run.py build in each source worktree before collection"
        )
    return {
        "root": str(root).replace(str(Path.home()), "~"),
        "source_sha": _git(root, "rev-parse", "HEAD"),
        "source_sha256": {name: _sha(root / name) for name in sources},
        "build_sha256": {
            path.relative_to(root).as_posix(): _sha(path)
            for path in sorted(set(files))
            if path.is_file()
        },
    }


def _checksums(raw: Path) -> bytes:
    return "".join(
        f"{_sha(path)}  {path.relative_to(raw).as_posix()}\n"
        for path in sorted(raw.rglob("*"))
        if path.is_file()
    ).encode()


def _check_raw(raw: Path, checksum_path: Path) -> None:
    if checksum_path.read_bytes() != _checksums(raw):
        raise ValueError("raw checksum or file set mismatch")


def _schedule() -> list[dict[str, Any]]:
    return [
        {
            "repetition": repetition,
            "source_phase": phase,
            "child": f"r{repetition}-{phase}",
        }
        for repetition in range(1, 6)
        for phase in (PHASES if repetition % 2 else tuple(reversed(PHASES)))
    ]


def _collect(raw: Path, checksum: Path, run_id: str) -> None:
    roots = {
        phase: Path(os.environ[name]).expanduser().resolve()
        for phase, name in zip(PHASES, ("BASELINE_ROOT", "CHANGED_ROOT"))
    }
    if roots["before"] == roots["after"]:
        raise ValueError("baseline and changed worktrees must differ")
    snapshots = {phase: _snapshot(root) for phase, root in roots.items()}
    baseline_product = _git(roots["before"], "rev-parse", "04f9f2d^{commit}")
    if _git(
        roots["before"],
        "diff",
        baseline_product,
        "HEAD",
        "--",
        "src",
        "include",
        "Makefile",
    ):
        raise ValueError("baseline product sources must match commit 04f9f2d")
    for name, digest in snapshots["before"]["source_sha256"].items():
        if (
            name.startswith("bench/ycsb/")
            and snapshots["after"]["source_sha256"].get(name) != digest
        ):
            raise ValueError("existing benchmark sources must match between worktrees")
    if snapshots["before"]["source_sha"] == snapshots["after"]["source_sha"]:
        raise ValueError("changed source must differ from baseline")
    raw.mkdir(parents=True, exist_ok=False)
    manifest = {
        "run_id": run_id,
        "baseline_product_sha": baseline_product,
        "sources": snapshots,
        "schedule": _schedule(),
        "expected_measured": 20,
        "expected_preflight": 20,
        "expected_baseline_loads": 20,
        "bootstrap_seed": 228,
        "bootstrap_resamples": 10000,
        "collections": [],
    }
    try:
        for item in manifest["schedule"]:
            phase = item["source_phase"]
            root = roots[phase]
            if _snapshot(root) != snapshots[phase]:
                raise ValueError("source or prepared build changed during collection")
            output = raw / item["child"]
            command = [
                sys.executable,
                str(root / "bench/ycsb/run.py"),
                "collect",
                "--output",
                str(output),
                "--run-id",
                f"{run_id}-{item['child']}",
                "--sizes",
                "10000",
                "--workloads",
                "E",
                "--threads",
                "1",
                "--operations",
                "1000",
                "--repetitions",
                "1",
            ]
            print("$ " + shlex.join(command), flush=True)
            result = subprocess.run(command, cwd=root, check=False)
            manifest["collections"].append({**item, "exit_code": result.returncode})
            if result.returncode:
                raise RuntimeError(
                    f"collection failed, partial raw preserved: {item['child']}"
                )
        for phase, root in roots.items():
            if _snapshot(root) != snapshots[phase]:
                raise ValueError("source or prepared build changed during collection")
    finally:
        (raw / "manifest.json").write_bytes(_encoded(manifest))
        checksum.write_bytes(_checksums(raw))


def _records(raw: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = _json(raw / "manifest.json")
    if manifest["schedule"] != _schedule() or len(manifest["collections"]) != 10:
        raise ValueError("incomplete paired collection matrix")
    records = []
    manifest["child_environments"] = {}
    for item, collected in zip(_schedule(), manifest["collections"]):
        if collected != {**item, "exit_code": 0}:
            raise ValueError("failed collection or invalid collection order")
        child = raw / item["child"]
        summary = ANALYSIS.verify(child)
        if summary["measured_runs"] != 2 or summary["failed_runs"]:
            raise ValueError("child measurement or audit failure")
        child_raw = child / "data/raw"
        child_manifest = _json(next(child_raw.glob("*/manifest.json")))
        expected = {
            "sizes": [10000],
            "workloads": ["E"],
            "threads": [1],
            "operations": 1000,
            "repetitions": 1,
            "profile": False,
        }
        if any(child_manifest[key] != value for key, value in expected.items()):
            raise ValueError("child collection condition mismatch")
        manifest["child_environments"][item["child"]] = child_manifest["environment"]
        phase = item["source_phase"]
        if (
            child_manifest["environment"]["git_sha"]
            != manifest["sources"][phase]["source_sha"]
        ):
            raise ValueError("child source sha mismatch")
        trials, _ = ANALYSIS.read_records(child_raw)
        if len(trials) != 4 or any(record["problems"] for record in trials):
            raise ValueError("child preflight or measured run failure")
        baselines = list(child_raw.glob("*/baselines/*/record.json"))
        if len(baselines) != 2:
            raise ValueError("missing baseline load records")
        for baseline in baselines:
            parent = baseline.parent
            process = _json(parent / "process.json")
            metrics = ANALYSIS.parse_metrics(
                (parent / "stdout.txt").read_text(), (parent / "stderr.txt").read_text()
            )
            audit = _json(parent / "audit-loaded.json")
            if (
                process["exit_code"]
                or process["timeout"]
                or metrics["non_ok"]
                or metrics["binding_errors"]
            ):
                raise ValueError("baseline load failed")
            if (
                metrics["logical_operations"] != 10000
                or audit["rows"] != 10000
                or audit["payload_bytes"] != 10000000
            ):
                raise ValueError("baseline load or audit count mismatch")
        for record in trials:
            records.append(
                {
                    **record,
                    "source_phase": phase,
                    "source_repetition": item["repetition"],
                }
            )
    return records, manifest


def _bootstrap(ratios: list[float]) -> dict[str, Any]:
    if len(ratios) != 5:
        raise ValueError("five complete paired repetitions required")
    generator = random.Random(228)
    samples = sorted(
        statistics.median(generator.choices(ratios, k=5)) for _ in range(10000)
    )
    bounds = []
    for fraction in (0.025, 0.975):
        position = fraction * (len(samples) - 1)
        lower = int(position)
        bounds.append(
            samples[lower] + (samples[lower + 1] - samples[lower]) * (position - lower)
        )
    return {
        "n": 5,
        "ratios": ratios,
        "median": statistics.median(ratios),
        "ci95": bounds,
        "seed": 228,
        "resamples": 10000,
    }


def _summarize(
    records: list[dict[str, Any]], manifest: dict[str, Any], checksum: Path
) -> dict[str, Any]:
    measured = [record for record in records if record["phase"] == "measured"]
    summary = {
        "collection": manifest,
        "raw_file_list_sha256": _sha(checksum),
        "measured_runs": len(measured),
        "preflight_runs": len(records) - len(measured),
        "engines": {},
        "H1": "채택",
    }
    for engine in ("totedb", "sqlite"):
        phases = {}
        for phase in PHASES:
            selected = [
                record
                for record in measured
                if record["condition"]["engine"] == engine
                and record["source_phase"] == phase
            ]
            phases[phase] = ANALYSIS._engine_summary(selected)
            phases[phase]["binding_errors"] = sum(
                record["metrics"]["binding_errors"] for record in selected
            )
            for operation in ("READ", "SCAN", "INSERT"):
                phases[phase]["operations"].setdefault(
                    operation,
                    {
                        name: ANALYSIS.describe([])
                        for name in (
                            "Operations",
                            "50thPercentileLatency(us)",
                            "95thPercentileLatency(us)",
                            "99thPercentileLatency(us)",
                        )
                    },
                )
            phases[phase]["official_runtime_ms"] = ANALYSIS.describe(
                [
                    record["metrics"]["ycsb"]["OVERALL"]["RunTime(ms)"]
                    for record in selected
                ]
            )
        pairs = {
            (record["source_repetition"], record["source_phase"]): record["metrics"][
                "ycsb"
            ]["OVERALL"]["Throughput(ops/sec)"]
            for record in measured
            if record["condition"]["engine"] == engine
        }
        ratios = [
            pairs[(repetition, "after")] / pairs[(repetition, "before")]
            for repetition in range(1, 6)
        ]
        summary["engines"][engine] = {**phases, "after_before": _bootstrap(ratios)}
    lower, upper = summary["engines"]["totedb"]["after_before"]["ci95"]
    summary["H2"] = "채택" if lower > 2 else ("기각" if upper <= 2 else "보류")
    return summary


def _csv(value: Any) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["metric", "value"])

    def visit(node: Any, key: str) -> None:
        if isinstance(node, dict):
            for name, entry in sorted(node.items()):
                visit(entry, f"{key}.{name}" if key else name)
        elif isinstance(node, list):
            for index, entry in enumerate(node):
                visit(entry, f"{key}[{index}]")
        else:
            writer.writerow([key, node])

    visit(value, "")
    return stream.getvalue().encode()


def _outputs(raw: Path, checksum: Path) -> dict[Path, bytes]:
    _check_raw(raw, checksum)
    records, manifest = _records(raw)
    summary = _summarize(records, manifest, checksum)
    return {
        Path("data/processed/records.jsonl"): b"".join(
            (json.dumps(record, sort_keys=True) + "\n").encode() for record in records
        ),
        Path("results/summary.json"): _encoded(summary),
        Path("results/summary.csv"): _csv(summary),
    }


def _run(command: str, run_id: str, data_id: str) -> None:
    raw = EXPERIMENT / "data/raw" / data_id
    checksum = EXPERIMENT / "data" / f"SHA256SUMS-{data_id}"
    if command in ("collect", "all"):
        if checksum.exists():
            raise ValueError("checksum output already exists")
        _collect(raw, checksum, run_id)
    if command in ("process", "analyze", "verify", "all"):
        outputs = _outputs(raw, checksum)
        for relative, content in outputs.items():
            path = EXPERIMENT / relative
            if command == "verify":
                if path.read_bytes() != content:
                    raise ValueError(f"generated output differs: {relative}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("collect", "process", "analyze", "verify", "all")
    )
    args = parser.parse_args()
    run_id = os.environ["RUN_ID"]
    data_id = os.environ.get("DATA_RUN_ID", run_id.removesuffix("-check"))
    if not all(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value)
        for value in (run_id, data_id)
    ):
        parser.error("invalid run id")
    if args.command in ("collect", "all") and data_id != run_id:
        parser.error("collection requires DATA_RUN_ID equal to RUN_ID")
    _run(args.command, run_id, data_id)


if __name__ == "__main__":
    try:
        _main()
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        subprocess.SubprocessError,
    ) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
