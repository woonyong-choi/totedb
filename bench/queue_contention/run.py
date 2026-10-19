"""합성 큐 경합 test/collect/verify/analyze/profile 명령. 공식 YCSB 워크로드가 아니다."""

from __future__ import annotations

import argparse
import cProfile
import dataclasses
import datetime
import hashlib
import itertools
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.queue_contention.adapters import ENGINES, ContentionError
from bench.queue_contention.analysis import digest, json_bytes, summarize, verify_output
from bench.queue_contention.trial import WORKLOADS, Condition, Trial, run_trial

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    args = _arguments()
    try:
        if args.command == "_trial":
            _trial_process(args)
            return 0
        if args.command in ("collect", "test", "profile"):
            manifest = collect(args)
            print(json.dumps(manifest, sort_keys=True))
            return 0 if manifest["failed_records"] == 0 else 1
        if args.command == "verify":
            print(json.dumps(verify_output(args.output), sort_keys=True))
            return 0
        verify_output(args.output, summaries=False)
        manifest_path = _manifest_path(args.output)
        manifest = json.loads(manifest_path.read_bytes())
        _write_summaries(
            args.output, manifest_path.parent, manifest["matrix"]["repetitions"]
        )
        print(json.dumps({"status": "analyzed", "run_id": manifest["run_id"]}))
        return 0
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


def collect(args: argparse.Namespace) -> dict[str, object]:
    root = (
        args.output / "profile" if args.command == "profile" else args.output
    ).resolve()
    matrix = _matrix(args)
    schedule = _schedule(matrix, profile=args.command == "profile")
    if list((root / "data/raw").glob("*/manifest.json")):
        raise ContentionError("output already contains a run manifest")
    output = root / "data/raw" / args.run_id
    database_root = ROOT / "build/queue-contention/runs" / args.run_id
    if args.command == "profile":
        database_root = database_root / "profile"
    if database_root.exists() or output.exists():
        raise ContentionError("run id already exists")
    output.mkdir(parents=True, exist_ok=False)
    database_root.mkdir(parents=True, exist_ok=False)
    environment = _environment()
    started_at = time.time()
    manifest = {
        "schema": 1,
        "run_id": args.run_id,
        "command": sys.argv,
        "matrix": matrix,
        "kind": args.command,
        "expected_records": len(schedule),
        "failed_records": 0,
        "state": "collecting",
        "started_at": started_at,
        "hashes": {},
        "formal_measurement": args.command == "collect",
        "output": str(root),
        "database_root": str(database_root),
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_bytes(json_bytes(manifest))
    records = []
    with (output / "raw.jsonl").open("x") as raw:
        for phase, repeat, condition, engine in schedule:
            identity = f"{phase}-r{repeat}-{condition.key()}-{engine}"
            spec = Trial(
                condition,
                engine,
                database_root / identity / "database",
                args.timeout,
                args.fault,
                output / "trials" / identity,
            )
            record = _execute(output, identity, spec, profile=phase == "profile")
            record.update(
                {
                    "trial_id": identity,
                    "phase": phase,
                    "repetition": repeat,
                    "run_id": args.run_id,
                    "ts_utc": _utc(),
                    "input_identifier": hashlib.sha256(
                        json_bytes(dataclasses.asdict(condition))
                    ).hexdigest(),
                }
            )
            records.append(record)
            raw.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
            raw.flush()
            os.fsync(raw.fileno())
    _write_summaries(root, output, matrix["repetitions"])
    (root / "env.json").write_bytes(
        json_bytes({"before": environment, "after": _environment()})
    )
    hashes = {
        str(path.relative_to(root)): digest(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path != manifest_path
    }
    hashes["env.json"] = digest(root / "env.json")
    manifest.update(
        {
            "state": "complete",
            "finished_at": time.time(),
            "hashes": hashes,
            "failed_records": sum(r["status"] != "ok" for r in records),
        }
    )
    manifest_path.write_bytes(json_bytes(manifest))
    checksum_rows = {
        **hashes,
        str(manifest_path.relative_to(root)): digest(manifest_path),
    }
    (root / "data/SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n" for name, value in sorted(checksum_rows.items()))
    )
    verify_output(root)
    return manifest


def _manifest_path(root: Path) -> Path:
    candidates = list((root / "data/raw").glob("*/manifest.json"))
    if len(candidates) != 1:
        raise ContentionError("expected exactly one manifest")
    return candidates[0]


def _write_summaries(root: Path, output: Path, repetitions: int) -> None:
    records = [
        json.loads(line) for line in (output / "raw.jsonl").read_text().splitlines()
    ]
    destination = root / "results"
    destination.mkdir(parents=True, exist_ok=True)
    summary, table = summarize(records, repetitions)
    (destination / "summary.json").write_bytes(summary)
    (destination / "summary.csv").write_bytes(table)


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("test", "collect", "verify", "analyze", "profile", "_trial")
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "build/queue-contention/runs"
    )
    parser.add_argument("--run-id", default="local")
    parser.add_argument("--workloads", default=",".join(WORKLOADS))
    parser.add_argument("--workers", default=None)
    parser.add_argument("--payloads", default=None)
    parser.add_argument("--engines", default=",".join(ENGINES))
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--cpu-profile", type=Path)
    parser.add_argument(
        "--fault",
        choices=("", "worker-error", "bad-payload", "missing-job", "timeout"),
        default="",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if (
        not args.run_id
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
            for c in args.run_id
        )
        or args.run_id in (".", "..")
    ):
        parser.error(
            "run-id must contain only letters, digits, dot, dash and underscore"
        )
    return args


def _matrix(args: argparse.Namespace) -> dict[str, object]:
    test = args.command == "test"
    profile = args.command == "profile"
    defaults = {
        "workers": "4" if profile else ("1,4" if test else "1,4,8"),
        "payloads": "64" if test else ("256" if profile else "256,4096"),
    }
    matrix = {
        "workloads": ["mixed"] if profile else args.workloads.split(","),
        "workers": [
            int(value) for value in (args.workers or defaults["workers"]).split(",")
        ],
        "payloads": [
            int(value) for value in (args.payloads or defaults["payloads"]).split(",")
        ],
        "engines": args.engines.split(","),
        "jobs": args.jobs if args.jobs is not None else (12 if test else 256),
        "repetitions": args.repetitions
        if args.repetitions is not None
        else (1 if test or profile else 5),
    }
    if matrix["repetitions"] < 1 or matrix["jobs"] < 1:
        raise ContentionError("jobs and repetitions must be positive")
    for name in ("workers", "payloads", "workloads", "engines"):
        if not matrix[name] or len(set(matrix[name])) != len(matrix[name]):
            raise ContentionError("matrix values must be unique and nonempty")
    if not set(matrix["engines"]).issubset(ENGINES):
        raise ContentionError("unknown engine in matrix")
    for workload, workers, size in itertools.product(
        matrix["workloads"], matrix["workers"], matrix["payloads"]
    ):
        Condition(workload, workers, size, matrix["jobs"]).validate()
    return matrix


def _schedule(
    matrix: dict[str, object], *, profile: bool = False
) -> list[tuple[str, int, Condition, str]]:
    schedule = []
    for workload, workers, size in itertools.product(
        matrix["workloads"], matrix["workers"], matrix["payloads"]
    ):
        condition = Condition(workload, workers, size, matrix["jobs"])
        engines = matrix["engines"]
        if profile:
            schedule.extend(("preflight", 0, condition, engine) for engine in engines)
            schedule.extend(("profile", 0, condition, engine) for engine in engines)
            continue
        schedule.extend(("preflight", 0, condition, engine) for engine in engines)
        for repeat in range(matrix["repetitions"]):
            offset = repeat % len(engines)
            schedule.extend(
                ("measured", repeat, condition, engine)
                for engine in engines[offset:] + engines[:offset]
            )
    return schedule


def _execute(
    output: Path, identity: str, spec: Trial, *, profile: bool = False
) -> dict[str, object]:
    directory = output / "trials" / identity
    directory.mkdir(parents=True)
    specification = dataclasses.asdict(spec)
    specification["directory"] = str(spec.directory.resolve())
    specification["evidence_directory"] = str(spec.evidence_directory.resolve())
    spec_path = directory / "spec.json"
    spec_path.write_bytes(json_bytes(specification))
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_trial",
        "--spec",
        str(spec_path.resolve()),
    ]
    if profile:
        command.extend(("--cpu-profile", str((directory / "python.prof").resolve())))
    if profile:
        (directory / "python-profile.json").write_bytes(
            json_bytes(
                {
                    "scope": "controller-thread",
                    "excludes": "worker Python calls and native engine stacks",
                    "native_evidence": "native-sample.txt",
                }
            )
        )
    started = time.time()
    timed_out = False
    with (
        (directory / "stdout.txt").open("wb") as stdout,
        (directory / "stderr.txt").open("wb") as stderr,
    ):
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, cwd=ROOT)
        sampling = _sample(process.pid, directory) if profile else None
        try:
            process.wait(timeout=spec.timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            timed_out = True
        if sampling is not None:
            _finish_sample(sampling, directory)
    result = _read_trial(directory, process.returncode, timed_out)
    result.update(
        {
            "command": command,
            "exit_code": process.returncode,
            "controller_started_at": started,
            "controller_finished_at": time.time(),
            "condition": dataclasses.asdict(spec.condition),
            "engine": spec.engine,
        }
    )
    return result


def _read_trial(directory: Path, exit_code: int, timed_out: bool) -> dict[str, object]:
    if not timed_out and exit_code == 0:
        try:
            result = json.loads((directory / "stdout.txt").read_bytes())
            if result.get("status") in ("ok", "failed"):
                return result
        except (json.JSONDecodeError, UnicodeDecodeError):
            reason = "invalid trial JSON"
        else:
            reason = "invalid trial result"
    else:
        reason = (
            "controller timeout" if timed_out else f"trial process exited {exit_code}"
        )
    return {
        "status": "failed",
        "problems": [reason],
        "workers": [
            json.loads(path.read_bytes())
            for path in sorted(directory.glob("worker-*.json"))
        ],
        "elapsed_s": None,
        "throughput_jobs_s": None,
        "completed_jobs": 0,
        "audit": None,
    }


def _trial_process(args: argparse.Namespace) -> None:
    values = json.loads(args.spec.read_bytes())
    values["condition"] = Condition(**values["condition"])
    values["directory"] = Path(values["directory"])
    values["evidence_directory"] = Path(values["evidence_directory"])
    spec = Trial(**values)
    if args.cpu_profile:
        profiler = cProfile.Profile()
        result = profiler.runcall(run_trial, spec)
        profiler.dump_stats(args.cpu_profile)
    else:
        result = run_trial(spec)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


def _environment() -> dict[str, object]:
    commands = {
        "head": ["git", "rev-parse", "HEAD"],
        "status": ["git", "status", "--porcelain"],
        "diff": ["git", "diff", "HEAD"],
        "compiler": ["cc", "--version"],
    }
    observed = {}
    for name, command in commands.items():
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, check=False
        )
        observed[name] = {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    sources = sorted((ROOT / "bench/queue_contention").glob("*.py"))
    library = Path(
        os.environ.get(
            "TOTEDB_LIB",
            ROOT
            / (
                "build/lib/libtotedb.dylib"
                if sys.platform == "darwin"
                else "build/lib/libtotedb.so"
            ),
        )
    )
    return {
        "timestamp": time.time(),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "load_average": os.getloadavg(),
        "git_and_tools": observed,
        "environment": {
            name: os.environ[name]
            for name in ("TOTEDB_LIB", "PYTHONPATH", "CC", "CFLAGS", "LDFLAGS")
            if name in os.environ
        },
        "source_sha256": {
            str(path.relative_to(ROOT)): digest(path) for path in sources
        },
        "native_library": {
            "path": str(library),
            "sha256": digest(library) if library.is_file() else None,
        },
    }


def _sample(pid: int, directory: Path) -> subprocess.Popen[bytes] | None:
    executable = shutil.which("sample")
    if executable is None:
        (directory / "sampling.json").write_bytes(
            json_bytes(
                {
                    "status": "unavailable",
                    "platform": sys.platform,
                    "python_worker_pid": pid,
                    "fallback": "python.prof",
                }
            )
        )
        return None
    command = [
        executable,
        str(pid),
        "2",
        "1",
        "-file",
        str((directory / "native-sample.txt").resolve()),
    ]
    (directory / "sampling.json").write_bytes(
        json_bytes({"command": command, "python_worker_pid": pid})
    )
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _finish_sample(process: subprocess.Popen[bytes], directory: Path) -> None:
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    (directory / "sample-stdout.txt").write_bytes(stdout)
    (directory / "sample-stderr.txt").write_bytes(stderr)
    metadata = json.loads((directory / "sampling.json").read_bytes())
    metadata["exit_code"] = process.returncode
    (directory / "sampling.json").write_bytes(json_bytes(metadata))


if __name__ == "__main__":
    raise SystemExit(main())
