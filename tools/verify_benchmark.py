from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def verify_library(path: Path, *, enabled: bool = False) -> set[str]:
    output = subprocess.run(
        ["nm", "-a", str(path)], capture_output=True, text=True, check=True
    ).stdout
    symbols = set(re.findall(r"\b_?(tb_bench\w*)\b", output))
    if bool(symbols) != enabled:
        raise ValueError(f"benchmark symbol mismatch in {path}: {sorted(symbols)}")
    if enabled and "tb_bench_tls" not in symbols:
        raise ValueError(f"missing benchmark TLS positive control in {path}")
    command = (
        ["otool", "-l", str(path)]
        if sys.platform == "darwin"
        else ["readelf", "-sW", str(path)]
    )
    details = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    if enabled:
        tls_present = (
            "__thread_bss" in details
            if sys.platform == "darwin"
            else bool(re.search(r"\bTLS\b.*\btb_bench_tls\b", details))
        )
        if not tls_present:
            raise ValueError(f"missing benchmark TLS section in {path}")
    exports = subprocess.run(
        ["nm", "-g", str(path)], capture_output=True, text=True, check=True
    ).stdout
    return set(re.findall(r"\b_?(tote_\w*)\b", exports))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("library", type=Path)
    parser.add_argument("--enabled", action="store_true")
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    exports = verify_library(args.library, enabled=args.enabled)
    if args.baseline:
        expected = set(re.findall(r"\b_?(tote_\w*)\b", args.baseline.read_text()))
        if exports != expected:
            raise ValueError(f"product exports changed: {exports ^ expected}")


if __name__ == "__main__":
    main()
