from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASES = [
    "request",
    "parse",
    "exec_including_parse",
    "metadata",
    "publish",
    "row_index_partial",
    "handle_mutex_acquire",
    "file_lock_acquire",
    "pread",
    "pwrite",
    "fsync",
    "flock_syscall",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--n", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.n <= 100000:
        parser.error("n must be between 1 and 100000")
    build = args.build_dir.resolve()
    driver = build / "counter_driver"
    library = (
        build
        / "lib"
        / ("libtotedb.dylib" if sys.platform == "darwin" else "libtotedb.so")
    )
    flags = (build / ".build-fingerprint").read_text()
    files = sorted(
        {
            str(path.relative_to(ROOT))
            for folder in (ROOT / "src", ROOT / "include")
            for path in folder.rglob("*")
            if path.suffix in {".c", ".h", ".mk"}
        }
        | {"Makefile", "bench/counter_driver.c"}
    )
    source_hash = hashlib.sha256()
    for name in files:
        source_hash.update(name.encode() + b"\0" + (ROOT / name).read_bytes())
    metadata = {
        "schema_version": 1,
        "source_sha256": source_hash.hexdigest(),
        "library_sha256": digest(library),
        "driver_sha256": digest(driver),
        "build_fingerprint": flags,
        "compiler_version": subprocess.run(
            shlex.split(
                next(line[3:] for line in flags.splitlines() if line.startswith("CC="))
            )
            + ["--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout,
        "n": args.n,
        "clients": 1,
        "transaction": "explicit begin/update/commit per increment; durable",
        "workload": "UPDATE counters SET counter=counter+1 WHERE id=1",
        "schema": "CREATE TABLE counters (id INTEGER PRIMARY KEY, counter INTEGER NOT NULL)",
        "sqlite": "DELETE/FULL; prepare-each and prepared-reuse are separate configurations",
        "phase_names": PHASES,
        "request_order": ["begin", "exec", "commit"],
        "sqlite_internal_phases": None,
        "tote_prepare_ns": None,
        "output_cost": "Tote wire encoding is inside exec; decoding/free and SQLite changes/reset/finalize are output_client_ns",
        "io_bytes": "bytes accepted by kernel calls, not physical device bytes",
        "wall_minus_cpu": "unclassified residual, not an OS off-CPU or lock attribution",
    }
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    failed = False
    for engine in ("tote", "sqlite-each", "sqlite-reuse"):
        db = (args.output / f"{engine}.db").resolve()
        command = [str(driver), engine, str(db), str(args.n)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        (args.output / f"{engine}.stderr").write_text(result.stderr)
        payload = (
            json.loads(result.stdout)
            if result.stdout
            else {"error": "no driver result"}
        )
        payload["validated_persisted_counter"] = (
            args.n if result.returncode == 0 else None
        )
        payload.update(engine=engine, exit_code=result.returncode, command=command)
        (args.output / f"{engine}.json").write_text(
            json.dumps(payload, indent=2) + "\n"
        )
        failed |= result.returncode != 0 or payload.get("completed") != args.n
    if failed:
        sys.exit("counter validation failed; databases retained")


if __name__ == "__main__":
    main()
