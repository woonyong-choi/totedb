"""Collect immutable release-cost evidence; process/analyze/verify require --output."""

from __future__ import annotations

__all__ = ["main"]

import argparse
import datetime
import hashlib
import json
import logging
import math
import os
import platform
import random
import re
import shlex
import signal
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BASE = "d79ad8c8"
ENGINES = ("tote", "sqlite-each", "sqlite-reuse")
PHASES = "request parse exec metadata publish row_index handle_lock file_lock read write sync lock_syscall".split()
COST_FIELDS = "wall_ns cpu_ns exclusive_wall_ns exclusive_cpu_ns attempts bytes errors short_io eintr".split()
TIMERS = ("begin_ns", "prepare_ns", "exec_ns", "commit_ns", "output_client_ns")
EXPECTED = {"native": (60, 12), "ycsb": (120, 24), "queue": (240, 48)}
logger = logging.getLogger(__name__)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _load(path: Path) -> Any:
    value = json.loads(path.read_bytes())
    _json_bytes(value)
    return value


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _observe(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
        )
        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": command, "unavailable": str(error)}


def _source_identity() -> dict[str, Any]:
    paths = [ROOT / "Makefile", Path(__file__).resolve()]
    for name in ("src", "include", "bench", "tools"):
        paths.extend(
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
    hashes = {p.relative_to(ROOT).as_posix(): _digest(p) for p in sorted(set(paths))}
    return {
        "base": BASE,
        "head": _observe(["git", "rev-parse", "HEAD"]),
        "tree": _observe(["git", "rev-parse", "HEAD^{tree}"]),
        "product_diff": _observe(
            ["git", "diff", BASE, "--", "src", "include", "Makefile"]
        ),
        "hashes": hashes,
        "fingerprint": hashlib.sha256(_json_bytes(hashes)).hexdigest(),
    }


def _environment() -> dict[str, Any]:
    commands = {
        "uname": ["uname", "-a"],
        "cpu": ["lscpu"],
        "compiler": [*shlex.split(os.environ.get("CC", "cc")), "--version"],
        "sqlite": ["sqlite3", "--version"],
        "java": ["java", "-version"],
        "status": ["git", "status", "--porcelain"],
        "mac_hardware": "sysctl hw.model hw.memsize machdep.cpu.brand_string".split(),
    }
    proc = {}
    for name in ("cpuinfo", "meminfo", "loadavg"):
        path = Path("/proc") / name
        proc[name] = path.read_text() if path.exists() else None
    return {
        "utc": _utc(),
        "platform": platform.platform(),
        "system": platform.system(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "load_average": os.getloadavg(),
        "proc": proc,
        "variables": {
            key: os.environ.get(key)
            for key in "CC CFLAGS SHARED_CFLAGS LDFLAGS RUNNER_NAME RUNNER_OS".split()
        },
        "tools": {key: _observe(command) for key, command in commands.items()},
    }


def _run(command: list[str], directory: Path, timeout_s: float) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = {
        "command": command,
        "cwd": str(ROOT),
        "timeout_s": timeout_s,
        "started_utc": _utc(),
        "timed_out": False,
        "exit_code": None,
    }
    suffix = "dylib" if sys.platform == "darwin" else "so"
    forced = {
        "BENCHMARK": "0",
        "SANITIZE": "",
        "PYTHON": sys.executable,
        "BUILD_DIR": "build",
        "TOTEDB_LIB": str(ROOT / "build/lib" / f"libtotedb.{suffix}"),
    }
    env = {**os.environ, **forced}
    for (
        key
    ) in "LD_LIBRARY_PATH LD_PRELOAD DYLD_LIBRARY_PATH DYLD_INSERT_LIBRARIES".split():
        env.pop(key, None)
    result["forced_environment"] = forced
    with (
        (directory / "stdout.txt").open("xb") as stdout,
        (directory / "stderr.txt").open("xb") as stderr,
    ):
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            result["pid"] = process.pid
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                result["timed_out"] = True
            result["exit_code"] = process.returncode
        except OSError as error:
            result["launch_error"] = str(error)
    result.update(wall_s=time.monotonic() - started, finished_utc=_utc())
    _write(directory / "command.json", result)
    return result


def _native_schedule() -> list[dict[str, Any]]:
    schedule = []
    for n in (128, 1024):
        for repeat in range(6):
            modes = ("off", "on") if repeat % 2 == 0 else ("on", "off")
            offset = repeat % len(ENGINES)
            for mode in modes:
                for engine in ENGINES[offset:] + ENGINES[:offset]:
                    schedule.append(
                        {
                            "n": n,
                            "mode": mode,
                            "engine": engine,
                            "phase": "preflight" if repeat == 0 else "measured",
                            "repetition": repeat,
                            "id": f"n{n}-{mode}-{engine}-r{repeat}",
                        }
                    )
    return schedule


def _subcommand(axis: str, command: str, output: Path, run_id: str) -> list[str]:
    module = "ycsb" if axis == "ycsb" else "queue_contention"
    arguments = [
        sys.executable,
        str(ROOT / "bench" / module / "run.py"),
        command,
        "--output",
        str(output / axis),
        "--run-id",
        run_id,
    ]
    if command != "collect":
        return arguments
    arguments += ["--repetitions", "5", "--timeout", "180"]
    if axis == "ycsb":
        return arguments + shlex.split(
            "--sizes 1000,10000 --workloads A,B,C,D,E,F --threads 1 --operations 1000"
        )
    return arguments + shlex.split(
        "--workloads enqueue,drain,mixed,outbox --workers 1,8 --payloads 256,4096 --jobs 256"
    )


def _build(output: Path, run_id: str) -> dict[str, Any]:
    commands = {
        "off": shlex.split("make SANITIZE= BENCHMARK=0 BUILD_DIR=build counter-driver"),
        "on": shlex.split(
            "make SANITIZE= BENCHMARK=1 BUILD_DIR=build-benchmark counter-driver"
        ),
        "ycsb": [sys.executable, str(ROOT / "bench/ycsb/run.py"), "build"],
    }
    results = {
        key: _run(command, output / "logs" / f"{run_id}-build-{key}", 600)
        for key, command in commands.items()
    }
    suffix = "dylib" if sys.platform == "darwin" else "so"
    for mode, folder in (("off", "build"), ("on", "build-benchmark")):
        library = ROOT / folder / "lib" / f"libtotedb.{suffix}"
        command = [
            sys.executable,
            str(ROOT / "tools/verify_benchmark.py"),
            str(library),
        ]
        if mode == "on":
            command.append("--enabled")
        results[f"control-{mode}"] = _run(
            command, output / "logs" / f"{run_id}-control-{mode}", 30
        )
    artifacts = {}
    for folder in ("build", "build-benchmark"):
        for path in (
            ROOT / folder / "counter_driver",
            ROOT / folder / "lib" / f"libtotedb.{suffix}",
        ):
            artifacts[path.relative_to(ROOT).as_posix()] = (
                _digest(path) if path.is_file() else None
            )
        fingerprint = ROOT / folder / ".build-fingerprint"
        artifacts[folder + "/configuration"] = (
            fingerprint.read_text() if fingerprint.is_file() else None
        )
    flags = []
    for folder in ("build", "build-benchmark"):
        configuration = artifacts[folder + "/configuration"] or ""
        shared = next(
            (
                line.split("=", 1)[1]
                for line in configuration.splitlines()
                if line.startswith("SHARED_CFLAGS=")
            ),
            "",
        )
        flags.append(
            [
                flag
                for flag in shlex.split(shared)
                if not flag.startswith(("-UTOTE_BENCHMARK", "-DTOTE_BENCHMARK"))
            ]
        )
    results["compiler-flags"] = {
        "exit_code": 0
        if flags[0] == flags[1]
        and all(required in flags[0] for required in ("-O2", "-g"))
        else 1,
        "timed_out": False,
        "shared_flags_without_benchmark_define": flags,
    }
    _write(output / "data/build.json", {"commands": results, "artifacts": artifacts})
    return results


def _collect_native(output: Path, run_id: str) -> None:
    for spec in _native_schedule():
        directory = output / "logs" / f"{run_id}-native-{spec['id']}"
        database = output / "data/native-db" / spec["id"] / "database"
        database.parent.mkdir(parents=True, exist_ok=False)
        folder = "build" if spec["mode"] == "off" else "build-benchmark"
        command = [
            str(ROOT / folder / "counter_driver"),
            spec["engine"],
            str(database),
            str(spec["n"]),
        ]
        _run(command, directory, 180)
        _write(directory / "spec.json", spec)


def _collect(output: Path, run_id: str) -> None:
    if "RELEASE_COST_DATABASE_ROOT" in os.environ:
        raise ValueError("external database root override is verification-only")
    identity = _source_identity()
    diff = identity["product_diff"]
    if diff.get("exit_code") != 0 or diff["stdout"]:
        raise ValueError("product sources differ from the specified base")
    manifest = {
        "schema": 1,
        "run_id": run_id,
        "state": "collecting",
        "source": identity,
        "expected": {
            key: {"measured": counts[0], "preflight": counts[1]}
            for key, counts in EXPECTED.items()
        },
        "database_roots": [
            f"build/queue-contention/runs/{run_id}",
            f"build/ycsb/runs/{run_id}",
        ],
        "axes": {},
        "started_utc": _utc(),
    }
    _write(output / "data/manifest.json", manifest)
    _write(output / "env-before.json", _environment())
    builds = _build(output, run_id)
    build_ok = all(
        row["exit_code"] == 0 and not row["timed_out"] for row in builds.values()
    )
    for axis in EXPECTED:
        if not build_ok:
            manifest["axes"][axis] = {
                "state": "failed",
                "reason": "build or control failed",
            }
            continue
        try:
            if axis == "native":
                _collect_native(output, run_id)
                failed = bool(_native_summary(output, run_id)["failures"])
            else:
                result = _run(
                    _subcommand(axis, "collect", output, run_id),
                    output / "logs" / f"{run_id}-collect-{axis}",
                    2700,
                )
                failed = result["exit_code"] != 0 or result["timed_out"]
            manifest["axes"][axis] = {"state": "failed" if failed else "complete"}
        except (OSError, ValueError, KeyError, TypeError) as error:
            manifest["axes"][axis] = {"state": "failed", "reason": str(error)}
        _write(output / "data/manifest.json", manifest)
    _write(output / "env-after.json", _environment())
    manifest["source_after"] = _source_identity()
    manifest["finished_utc"] = _utc()
    manifest["state"] = (
        "complete"
        if all(a["state"] == "complete" for a in manifest["axes"].values())
        else "failed"
    )
    if manifest["source_after"] != identity:
        manifest["state"] = "failed"
        manifest["source_changed"] = True
    _write(output / "data/manifest.json", manifest)
    _seal(output)


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid nonnegative integer: {name}")
    return value


def _read_native(directory: Path, spec: dict[str, Any]) -> dict[str, Any]:
    command = _load(directory / "command.json")
    if command["exit_code"] != 0 or command["timed_out"]:
        raise ValueError("native process failed or timed out")
    if _load(directory / "spec.json") != spec:
        raise ValueError("native condition identity mismatch")
    raw = _load(directory / "stdout.txt")
    if raw["error"] != 0 or raw["completed"] != spec["n"]:
        raise ValueError("native exact result check failed")
    elapsed = sum(_integer(raw[key], key) for key in TIMERS)
    if elapsed <= 0:
        raise ValueError("native elapsed time must be positive")
    totals = _validate_records(raw["records"], spec)
    if any(record["pid"] != command["pid"] for record in raw["records"]):
        raise ValueError("native trace pid differs from launched process")
    return {
        **spec,
        "elapsed_ns": elapsed,
        "ops_s": spec["n"] * 1e9 / elapsed,
        "timers": {key: raw[key] for key in TIMERS},
        "phases": totals,
    }


def _validate_records(records: Any, spec: dict[str, Any]) -> dict[str, Any]:
    expected = spec["n"] * 3 if spec["mode"] == "on" and spec["engine"] == "tote" else 0
    if not isinstance(records, list) or len(records) != expected:
        raise ValueError("native trace record count mismatch")
    totals = {phase: {key: 0 for key in COST_FIELDS} for phase in PHASES}
    identities = set()
    sequences = []
    operations = ("tote_begin", "tote_exec", "tote_commit")
    for index, record in enumerate(records):
        for key in ("incomplete", "overflow", "dropped"):
            if _integer(record[key], key) != 0:
                raise ValueError("incomplete, overflow or dropped native trace")
        if record["operation"] != operations[index % 3]:
            raise ValueError("native transaction operation triple mismatch")
        identities.add((_integer(record["pid"], "pid"), _integer(record["tid"], "tid")))
        sequences.append(_integer(record["sequence"], "sequence"))
        if len(record["phases"]) != len(PHASES):
            raise ValueError("native phase count mismatch")
        for phase, costs in zip(PHASES, record["phases"]):
            for key in COST_FIELDS:
                totals[phase][key] += _integer(costs[key], key)
    if records and (
        len(identities) != 1 or any(pid <= 0 or tid <= 0 for pid, tid in identities)
    ):
        raise ValueError("native pid/tid identity mismatch")
    if sequences and sequences != list(
        range(sequences[0], sequences[0] + len(sequences))
    ):
        raise ValueError("native sequence mismatch")
    return totals if records else {}


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "min": None, "max": None}
    if not all(math.isfinite(value) for value in values):
        raise ValueError("nonfinite metric")
    return {
        "count": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _paired(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    left_keys = [(row["n"], row["repetition"]) for row in left]
    right_keys = [(row["n"], row["repetition"]) for row in right]
    if (
        len(left) != 5
        or len(right) != 5
        or left_keys != right_keys
        or len(set(left_keys)) != 5
    ):
        return {
            "status": "incomparable",
            "pairs": 0,
            "values": [],
            "median": None,
            "bootstrap_95_ci": None,
        }
    ratios = [a["ops_s"] / b["ops_s"] for a, b in zip(left, right)]
    rng = random.Random(242)
    samples = sorted(
        statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(10000)
    )
    return {
        "status": "comparable",
        "pairs": 5,
        "values": ratios,
        "median": statistics.median(ratios),
        "bootstrap_95_ci": [samples[249], samples[9749]],
    }


def _phase_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for phase in PHASES:
        traced = [row for row in rows if row["phases"]]
        if not traced:
            continue
        result[phase] = {
            key: _distribution([row["phases"][phase][key] for row in traced])
            for key in COST_FIELDS[2:]
        }
        for key in ("attempts", "bytes"):
            result[phase][key + "_per_op"] = _distribution(
                [row["phases"][phase][key] / row["n"] for row in traced]
            )
    return result


def _native_summary(output: Path, run_id: str) -> dict[str, Any]:
    rows, failures = [], []
    for spec in _native_schedule():
        try:
            rows.append(
                _read_native(output / "logs" / f"{run_id}-native-{spec['id']}", spec)
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            failures.append({"id": spec["id"], "reason": str(error)})
    groups = defaultdict(list)
    for row in rows:
        if row["phase"] == "measured":
            groups[(row["n"], row["mode"], row["engine"])].append(row)
    aggregates = [
        {
            "n": n,
            "mode": mode,
            "engine": engine,
            "ops_s": _distribution([r["ops_s"] for r in selected]),
            "elapsed_ns": _distribution([r["elapsed_ns"] for r in selected]),
            "timers": {
                key: _distribution([r["timers"][key] for r in selected])
                for key in TIMERS
            },
            "phases": _phase_summary(selected),
        }
        for (n, mode, engine), selected in sorted(groups.items())
    ]
    comparisons = []
    for n in (128, 1024):
        for baseline in ENGINES[1:]:
            pair = _paired(groups[(n, "off", "tote")], groups[(n, "off", baseline)])
            preflight_ok = all(
                any(
                    r["n"] == n
                    and r["mode"] == "off"
                    and r["engine"] == engine
                    and r["phase"] == "preflight"
                    for r in rows
                )
                for engine in ("tote", baseline)
            )
            if not preflight_ok:
                pair = _paired([], [])
            comparisons.append({"n": n, "baseline": baseline, "mode": "off", **pair})
    perturbation = [
        {
            "n": n,
            "metric": "on/off tote throughput; diagnostic only",
            **(
                _paired(groups[(n, "on", "tote")], groups[(n, "off", "tote")])
                if all(
                    any(
                        row["n"] == n
                        and row["mode"] == mode
                        and row["engine"] == "tote"
                        and row["phase"] == "preflight"
                        for row in rows
                    )
                    for mode in ("on", "off")
                )
                else _paired([], [])
            ),
        }
        for n in (128, 1024)
    ]
    return {
        "measured_runs": sum(r["phase"] == "measured" for r in rows),
        "preflight_runs": sum(r["phase"] == "preflight" for r in rows),
        "failed_runs": len(failures),
        "failures": failures,
        "runs": rows,
        "aggregates": aggregates,
        "comparisons": comparisons,
        "instrumentation_perturbation": perturbation,
        "bootstrap": {"seed": 242, "samples": 10000, "confidence": 0.95},
        "limitations": [
            "on mode is diagnostic, not throughput comparison evidence",
            "five short samples; residual order/cache confounding",
            "sqlite-reuse initial prepare is outside measured timers",
            "exclusive phase totals only; wall-cpu difference is unknown, not additional cost",
            "io bytes are accepted bytes; attempts include failed/retried calls",
        ],
    }


def _evidence_files(output: Path) -> dict[str, str]:
    files = {}
    for path in sorted(output.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(output)
        if relative.parts[0] == "results" or relative == Path("data/SHA256SUMS"):
            continue
        if relative.parts[0] == "logs" and (
            len(relative.parts) == 2 or relative.parts[1].startswith("_derived-")
        ):
            continue
        files[relative.as_posix()] = _digest(path)
    return files


def _seal(output: Path) -> None:
    _write(output / "data/external-files.json", _external_files(output))
    path = output / "data/SHA256SUMS"
    rows = [f"{digest}  {name}\n" for name, digest in _evidence_files(output).items()]
    path.write_text("".join(rows))


def _verify_hashes(output: Path) -> None:
    listed = {}
    for line in (output / "data/SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if name in listed or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("invalid or duplicate checksum path")
        listed[name] = digest
    if listed != _evidence_files(output):
        raise ValueError("evidence hash or file set mismatch")
    if _load(output / "data/external-files.json") != _external_files(output):
        raise ValueError("external database hash or file set mismatch")


def _external_files(output: Path) -> dict[str, Any]:
    manifest = output / "data/manifest.json"
    roots = _load(manifest).get("database_roots", []) if manifest.exists() else []
    source = Path(os.environ.get("RELEASE_COST_DATABASE_ROOT", str(ROOT)))
    result = {}
    for name in roots:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid external database root")
        directory = source / relative
        result[name] = {
            "exists": directory.is_dir(),
            "files": {
                path.relative_to(directory).as_posix(): _digest(path)
                for path in sorted(directory.rglob("*"))
                if path.is_file()
            },
        }
    return result


def _derived(output: Path, axis: str, command: str, run_id: str) -> dict[str, Any]:
    index = 0
    while (output / "logs" / f"_derived-{run_id}-{command}-{axis}-{index}").exists():
        index += 1
    result = _run(
        _subcommand(axis, command, output, run_id),
        output / "logs" / f"_derived-{run_id}-{command}-{axis}-{index}",
        600,
    )
    if result["exit_code"] != 0 or result["timed_out"]:
        raise ValueError(f"{axis} {command} failed; see logs")
    return _load(output / axis / "results/summary.json")


def _aggregate(output: Path, *, verify: bool) -> dict[str, Any]:
    _verify_hashes(output)
    manifest = _load(output / "data/manifest.json")
    run_id = manifest["run_id"]
    native = _native_summary(output, run_id)
    axes = {"native": native}
    failures = []
    for axis in ("ycsb", "queue"):
        try:
            summary = _derived(output, axis, "verify" if verify else "analyze", run_id)
            if axis == "queue":
                runs = summary["runs"]
                counts = {
                    "measured_runs": sum(r["phase"] == "measured" for r in runs),
                    "preflight_runs": sum(r["phase"] == "preflight" for r in runs),
                    "failed_runs": sum(r["status"] != "ok" for r in runs),
                }
            else:
                counts = {
                    key: summary[key]
                    for key in ("measured_runs", "preflight_runs", "failed_runs")
                }
            axes[axis] = {**counts, "original_summary": summary}
        except (OSError, ValueError, KeyError, TypeError) as error:
            axes[axis] = {
                "measured_runs": 0,
                "preflight_runs": 0,
                "failed_runs": None,
                "incomplete": str(error),
            }
    for axis, counts in EXPECTED.items():
        observed = axes[axis]
        if (
            observed["measured_runs"],
            observed["preflight_runs"],
        ) != counts or observed["failed_runs"] != 0:
            failures.append(axis)
    if manifest["state"] != "complete":
        failures.append("collection-state")
    if manifest.get("source_after") != manifest["source"]:
        failures.append("source-identity")
    source = manifest["source"]
    if (
        source["fingerprint"]
        != hashlib.sha256(_json_bytes(source["hashes"])).hexdigest()
        or source["product_diff"].get("exit_code") != 0
        or source["product_diff"].get("stdout") != ""
    ):
        failures.append("source-fingerprint-or-product-diff")
    _verify_hashes(output)
    return {
        "schema": 1,
        "expected": manifest["expected"],
        "axes": axes,
        "status": "complete" if not failures else "failed",
        "incomplete_or_failed": failures,
        "totals": {
            key: sum(row[key] or 0 for row in axes.values())
            for key in ("measured_runs", "preflight_runs", "failed_runs")
        },
        "unknown_failure_count_axes": [
            axis for axis, row in axes.items() if row["failed_runs"] is None
        ],
        "source_fingerprint": manifest["source"]["fingerprint"],
        "interpretation": "Linux-only evidence when env-before.system is Linux; mixed w1 is one producer plus one consumer; DB writer remains one",
    }


def _process(output: Path, *, verify: bool) -> dict[str, Any]:
    destination = output / "results/summary.json"
    previous = destination.read_bytes() if verify and destination.exists() else None
    summary = _aggregate(output, verify=verify)
    body = _json_bytes(summary)
    if verify and previous is None:
        raise ValueError("missing suite summary; run process first")
    if previous is not None and previous != body:
        raise ValueError("suite summary regeneration mismatch")
    if not verify:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("collect", "process", "analyze", "verify", "all")
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", default="release-cost")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.run_id):
        parser.error("invalid run-id")
    output = args.output.resolve()
    if output == Path(__file__).resolve().parent:
        parser.error("output must not be the experiment source directory")
    if args.command in ("collect", "all"):
        output.mkdir(parents=True, exist_ok=False)
    (output / "logs").mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(
        output / "logs" / f"{args.run_id}-{args.command}.log", mode="x"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[handler, logging.StreamHandler()],
    )
    if args.command in ("collect", "all"):
        _collect(output, args.run_id)
    if args.command == "collect":
        return int(_load(output / "data/manifest.json")["state"] != "complete")
    summary = _process(output, verify=args.command == "verify")
    if args.command == "all" and summary["status"] == "complete":
        summary = _process(output, verify=True)
    logger.info(
        "suite %s; measured=%s preflight=%s failed=%s",
        summary["status"],
        summary["totals"]["measured_runs"],
        summary["totals"]["preflight_runs"],
        summary["totals"]["failed_runs"],
    )
    return int(summary["status"] != "complete")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        logger.error("%s", error)
        raise SystemExit(1) from error
