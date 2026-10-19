"""Compare prebuilt queue libraries or preserve separate Linux native traces."""

from __future__ import annotations

__all__ = ["Invocation", "Variant", "find_perf", "main"]

import argparse
import dataclasses
import hashlib
import json
import logging
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT))

if TYPE_CHECKING:
    from bench.queue_contention.trial import Condition, Trial

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Variant:
    name: str
    engine: str
    library: Path


@dataclass(frozen=True)
class Invocation:
    variant: Variant
    phase: str
    repetition: int
    spec: Trial


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = _arguments()
    try:
        library = args.baseline_lib if args.command == "compare" else args.library
        os.environ["TOTEDB_LIB"] = str(library.resolve(strict=True))
        result = _compare(args) if args.command == "compare" else _trace(args)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 1 if result["status"] in ("failed", "incomparable") else 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        OverflowError,
        subprocess.SubprocessError,
    ) as error:
        logger.error("diagnosis failed: %s", error)
        return 1


def _prepare_output(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)


def find_perf(root: Path = Path("/usr/lib")) -> Path | None:
    candidates = sorted(root.glob("linux-tools*/perf"))
    candidates.extend(sorted(root.glob("linux-tools/*/perf")))
    for path in candidates:
        resolved = path.resolve()
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    return None


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--baseline-lib", type=Path, required=True)
    compare.add_argument("--candidate-lib", type=Path, required=True)
    compare.add_argument("--repetitions", type=int, default=5)
    compare.add_argument("--workers", default="1,8")
    trace = commands.add_parser("trace")
    trace.add_argument("--library", type=Path, required=True)
    trace.add_argument("--engine", choices=("totedb", "sqlite-wal"), default="totedb")
    trace.add_argument("--workers", type=int, default=8)
    trace.add_argument(
        "--perf", type=Path, help="actual perf binary, not a kernel wrapper"
    )
    for command in (compare, trace):
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--jobs", type=int, default=4096)
        command.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if args.jobs < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("jobs and timeout must be positive and finite")
    if args.command == "compare":
        try:
            args.workers = [int(value) for value in args.workers.split(",")]
        except ValueError:
            parser.error("workers must be comma-separated integers")
        if args.repetitions < 1 or len(set(args.workers)) != len(args.workers):
            parser.error("repetitions must be positive and workers unique")
    workers = args.workers if args.command == "compare" else [args.workers]
    if not workers or min(workers) < 1:
        parser.error("workers must be positive")
    return args


def _variants(args: argparse.Namespace) -> list[Variant]:
    return [
        Variant("baseline", "totedb", args.baseline_lib.resolve(strict=True)),
        Variant("candidate", "totedb", args.candidate_lib.resolve(strict=True)),
        Variant("control", "sqlite-wal", args.candidate_lib.resolve(strict=True)),
    ]


def _environment() -> dict[str, Any]:
    git = {}
    for name, arguments in (
        ("head", ["rev-parse", "HEAD"]),
        ("status", ["status", "--porcelain"]),
    ):
        result = subprocess.run(
            ["git", *arguments], cwd=ROOT, capture_output=True, text=True, check=False
        )
        git[name] = {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    cpuinfo = Path("/proc/cpuinfo")
    return {
        "python": sys.version,
        "executable": sys.executable,
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpuinfo.read_text() if cpuinfo.exists() else platform.processor(),
        "cpu_count": os.cpu_count(),
        "load_average": os.getloadavg(),
        "git": git,
    }


def _manifest(args: argparse.Namespace, variants: list[Variant]) -> dict[str, Any]:
    return {
        "schema": 1,
        "kind": args.command,
        "status": "running",
        "command": sys.argv,
        "started_at": time.time(),
        "environment_before": _environment(),
        "configuration": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "variants": [
            {
                "name": v.name,
                "engine": v.engine,
                "library": str(v.library),
                "sha256": _digest(v.library),
            }
            for v in variants
        ],
        "source_sha256": {
            str(p.relative_to(ROOT)): _digest(p)
            for p in sorted((ROOT / "bench/queue_contention").glob("*.py"))
        },
        "scope": "mixed/256B public API; trace excluded from formal comparisons",
    }


def _finish_manifest(output: Path, manifest: dict[str, Any], status: str) -> None:
    manifest.update(
        {
            "status": status,
            "finished_at": time.time(),
        }
    )
    try:
        manifest["environment_after"] = _environment()
    except OSError as error:
        manifest["environment_after_error"] = str(error)
    hashes, errors = {}, {}
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name in ("SHA256SUMS", "manifest.json"):
            continue
        name = str(path.relative_to(output))
        try:
            hashes[name] = _digest(path)
        except OSError as error:
            errors[name] = f"{type(error).__name__}: {error}"
    manifest["evidence_hash_errors"] = errors
    _write(output / "manifest.json", manifest)
    hashes["manifest.json"] = _digest(output / "manifest.json")
    (output / "SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n" for name, value in sorted(hashes.items()))
    )


def _check_libraries(manifest: dict[str, Any]) -> bool:
    libraries, errors = {}, {}
    for variant in manifest["variants"]:
        try:
            libraries[variant["name"]] = _digest(Path(variant["library"]))
        except OSError as error:
            libraries[variant["name"]] = None
            errors[variant["name"]] = f"{type(error).__name__}: {error}"
    unchanged = not errors and all(
        libraries[v["name"]] == v["sha256"] for v in manifest["variants"]
    )
    manifest.update(
        libraries_unchanged=unchanged,
        library_sha256_after=libraries,
        library_hash_errors=errors,
    )
    if not unchanged:
        manifest["reason"] = "library changed or unreadable during collection"
    return unchanged


@contextmanager
def _collection(
    args: argparse.Namespace, variants: list[Variant]
) -> Iterator[dict[str, Any]]:
    output = args.output.resolve()
    _prepare_output(output)
    manifest = {
        "schema": 1,
        "kind": args.command,
        "status": "running",
        "started_at": time.time(),
        "variants": [],
    }
    try:
        manifest.update(_manifest(args, variants))
        _write(output / "manifest.json", manifest)
        yield manifest
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        OverflowError,
        subprocess.SubprocessError,
    ) as error:
        manifest["reason"] = f"{type(error).__name__}: {error}"
        manifest["collection_error"] = manifest["reason"]
        _check_libraries(manifest)
        _finish_manifest(output, manifest, "failed")
        raise


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + "\n")


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _invocation(
    output: Path,
    variant: Variant,
    phase: str,
    repeat: int,
    condition: Condition,
    timeout_s: float,
) -> Invocation:
    from bench.queue_contention.trial import Trial

    identity = f"{phase}-r{repeat}-{condition.key()}-{variant.name}"
    directory = output / "trials" / identity
    directory.mkdir(parents=True, exist_ok=False)
    return Invocation(
        variant,
        phase,
        repeat,
        Trial(
            condition,
            variant.engine,
            directory / "database",
            timeout_s,
            evidence_directory=directory,
        ),
    )


def _trial_command(invocation: Invocation) -> list[str]:
    spec = invocation.spec
    values = dataclasses.asdict(spec)
    values["directory"] = str(spec.directory.resolve())
    values["evidence_directory"] = str(spec.evidence_directory.resolve())
    path = spec.evidence_directory / "spec.json"
    _write(path, values)
    return [
        sys.executable,
        str(ROOT / "bench/queue_contention/run.py"),
        "_trial",
        "--spec",
        str(path.resolve()),
    ]


def _process(
    command: list[str],
    directory: Path,
    library: Path,
    timeout_s: float,
    prefix: str = "",
) -> dict[str, Any]:
    metadata = {
        "command": command,
        "TOTEDB_LIB": str(library),
        "started_at": time.time(),
        "exit_code": None,
        "timed_out": False,
        "error": None,
    }
    with (
        (directory / f"{prefix}stdout.txt").open("xb") as stdout,
        (directory / f"{prefix}stderr.txt").open("xb") as stderr,
    ):
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env={**os.environ, "TOTEDB_LIB": str(library)},
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                metadata["timed_out"] = True
                _kill_group(process)
            metadata["exit_code"] = process.returncode
        except OSError as error:
            metadata["error"] = str(error)
    metadata["finished_at"] = time.time()
    _write(directory / f"{prefix}process.json", metadata)
    return metadata


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        logger.debug("process group already exited: %s", process.pid)
    process.wait()


def _read_raw(directory: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads((directory / "stdout.txt").read_bytes())
    except (ValueError, UnicodeDecodeError) as error:
        return None, f"invalid trial JSON: {error}"
    if not isinstance(value, dict):
        return None, "trial result must be an object"
    try:
        _write(directory / "raw.json", value)
    except ValueError as error:
        return None, f"non-finite trial JSON: {error}"
    return value, None


def _execute(invocation: Invocation) -> dict[str, Any]:
    from bench.queue_contention.diagnose_analysis import raw_problems

    directory = invocation.spec.evidence_directory
    process = _process(
        _trial_command(invocation),
        directory,
        invocation.variant.library,
        invocation.spec.timeout_s,
    )
    raw, error = _read_raw(directory)
    problems = raw_problems(raw, invocation.spec)
    if error:
        problems.append(error)
    if process["exit_code"] != 0 or process["timed_out"]:
        problems.append("trial process failed or timed out")
    record = {
        "schema": 1,
        "variant": invocation.variant.name,
        "phase": invocation.phase,
        "repetition": invocation.repetition,
        "condition": dataclasses.asdict(invocation.spec.condition),
        "engine": invocation.variant.engine,
        "library": str(invocation.variant.library),
        "status": "failed" if problems else "ok",
        "problems": problems,
        "process": process,
        "raw": raw,
    }
    _write(directory / "record.json", record)
    return record


def _compare(args: argparse.Namespace) -> dict[str, Any]:
    from bench.queue_contention.diagnose_analysis import PREFLIGHT_JOBS
    from bench.queue_contention.trial import Condition

    output = args.output.resolve()
    variants = _variants(args)
    with _collection(args, variants) as manifest:
        records = []
        for workers in args.workers:
            condition = Condition("mixed", workers, 256, args.jobs)
            preflight = dataclasses.replace(
                condition, jobs=min(PREFLIGHT_JOBS, args.jobs)
            )
            for variant in variants:
                record = _execute(
                    _invocation(
                        output, variant, "preflight", 0, preflight, args.timeout
                    )
                )
                records.append(record)
                _write(output / "records.json", records)
                if record["status"] != "ok":
                    return _finish_compare(output, manifest, records, args)
            for repeat in range(args.repetitions):
                offset = repeat % len(variants)
                for variant in variants[offset:] + variants[:offset]:
                    records.append(
                        _execute(
                            _invocation(
                                output,
                                variant,
                                "measured",
                                repeat,
                                condition,
                                args.timeout,
                            )
                        )
                    )
                    _write(output / "records.json", records)
        return _finish_compare(output, manifest, records, args)


def _finish_compare(
    output: Path,
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    from bench.queue_contention.diagnose_analysis import summarize

    summary = summarize(records, args.workers, args.repetitions)
    if not _check_libraries(manifest):
        summary["status"] = "incomparable"
        summary["reason"] = manifest["reason"]
        summary["conditions"] = {
            key: {"status": "incomparable", "reason": summary["reason"]}
            for key in summary["conditions"]
        }
    _write(output / "summary.json", summary)
    _finish_manifest(output, manifest, summary["status"])
    return summary


def _trace(args: argparse.Namespace) -> dict[str, Any]:
    from bench.queue_contention.trial import Condition

    output = args.output.resolve()
    variant = Variant("trace", args.engine, args.library.resolve(strict=True))
    with _collection(args, [variant]) as manifest:
        manifest["formal_measurement"] = False
        manifest["scope"] = "whole-trial-process-including-setup-close-audit"
        _write(output / "manifest.json", manifest)
        results = {}
        for tool in ("strace", "perf"):
            invocation = _invocation(
                output,
                variant,
                tool,
                0,
                Condition("mixed", args.workers, 256, args.jobs),
                args.timeout,
            )
            results[tool] = _trace_tool(invocation, tool, args)
        status = (
            "failed" if any(r["status"] == "failed" for r in results.values()) else "ok"
        )
        if all(r["status"] == "unavailable" for r in results.values()):
            status = "unavailable"
        elif status == "ok" and any(
            r["status"] == "unavailable" for r in results.values()
        ):
            status = "partial"
        result = {
            "schema": 1,
            "status": status,
            "formal_measurement": False,
            "scope": manifest["scope"],
            "tools": results,
            "interpretation": "no traced throughput comparison; cumulative thread syscall times are not elapsed fractions; unsampled symbols are unknown, not zero cost",
        }
        if not _check_libraries(manifest):
            result.update(status="incomparable", reason=manifest["reason"])
        _write(output / "trace.json", result)
        _finish_manifest(output, manifest, result["status"])
        return result


def _trace_tool(
    invocation: Invocation, tool: str, args: argparse.Namespace
) -> dict[str, Any]:
    from bench.queue_contention.diagnose_analysis import raw_problems

    directory = invocation.spec.evidence_directory
    command = _trial_command(invocation)
    metadata = {
        "tool": tool,
        "variant": invocation.variant.name,
        "engine": invocation.variant.engine,
        "condition": dataclasses.asdict(invocation.spec.condition),
        "status": "unavailable",
        "phase": "trace",
        "scope": "whole-trial-process-including-setup-close-audit",
        "syscall_time_basis": "cumulative-thread-wall-clock"
        if tool == "strace"
        else None,
        "expected_library_sha256": _digest(invocation.variant.library),
        "source_sha256": _digest(ROOT / "bench/queue_contention/run.py"),
        "trial_status": "not_started",
        "reason": None,
    }
    if tool == "perf":
        selected = args.perf or find_perf()
        executable = str(selected.resolve()) if selected else shutil.which(tool)
    else:
        executable = shutil.which(tool)
    if sys.platform != "linux" or executable is None:
        metadata["reason"] = (
            "Linux required" if sys.platform != "linux" else "tool not installed"
        )
        _write(directory / "tool.json", metadata)
        return metadata
    arguments = _trace_command(tool, executable, directory)
    launcher = [
        sys.executable,
        "-c",
        "import os,pathlib,sys;pathlib.Path(sys.argv[1]).touch();os.execv(sys.argv[2],sys.argv[2:])",
        str(directory / "trial-started"),
        *command,
    ]
    process = _process(
        [*arguments, *launcher],
        directory,
        invocation.variant.library,
        invocation.spec.timeout_s,
    )
    raw, error = _read_raw(directory)
    trial_started = (directory / "trial-started").exists() or raw is not None
    problems = raw_problems(raw, invocation.spec)
    metadata.update(
        {
            "process": process,
            "trial_status": "failed"
            if trial_started and problems
            else ("ok" if trial_started else "not_started"),
            "parse_error": error,
        }
    )
    if trial_started and (problems or process["timed_out"]):
        metadata.update({"status": "failed", "reason": problems or ["trace timeout"]})
    elif process["exit_code"] != 0 or not trial_started:
        metadata["reason"] = (
            "external tool failed or prevented trial start; inspect stdout/stderr"
        )
    elif tool == "perf":
        metadata.update(_perf_report(executable, invocation))
    else:
        metadata["status"] = "ok"
    _write(directory / "tool.json", metadata)
    return metadata


def _trace_command(tool: str, executable: str, directory: Path) -> list[str]:
    if tool == "strace":
        return [
            executable,
            "-f",
            "-c",
            "-w",
            "-e",
            "trace=fsync,fdatasync,pwrite64,flock",
            "-o",
            str(directory / "strace.txt"),
        ]
    return [
        executable,
        "record",
        "-e",
        "cpu-clock:u",
        "-g",
        "--call-graph",
        "fp",
        "-o",
        str(directory / "perf.data"),
        "--",
    ]


def _perf_report(executable: str, invocation: Invocation) -> dict[str, Any]:
    directory = invocation.spec.evidence_directory
    results = {}
    for name, options in (
        ("report", ["--stdio", "--no-children", "--sort", "symbol"]),
        ("script", []),
    ):
        process = _process(
            [executable, name, "-i", str(directory / "perf.data"), *options],
            directory,
            invocation.variant.library,
            invocation.spec.timeout_s,
            f"{name}-",
        )
        process["empty_stdout"] = (directory / f"{name}-stdout.txt").stat().st_size == 0
        _write(directory / f"{name}-process.json", process)
        results[name] = process
    return {
        "status": "ok"
        if all(
            p["exit_code"] == 0 and not p["empty_stdout"] and not p["timed_out"]
            for p in results.values()
        )
        else "unavailable",
        **results,
        "symbol_attribution": "unclassified; inspect original report top functions; absent catalog samples do not establish zero cost",
    }


if __name__ == "__main__":
    raise SystemExit(main())
