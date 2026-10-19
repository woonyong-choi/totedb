#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
id=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=7 HEAD)}
export RUN_ID="$id"
log() {
    local f="logs/$1-$id.log"
    [[ ! -e $f ]] || { echo "$f exists; use another RUN_ID" >&2; return 1; }
    mkdir -p logs
    { printf '$ RUN_ID=%s ./run.sh %s\n' "$id" "$1"; "${@:2}"; } 2>&1 |
        sed "s|$HOME|~|g" | tee "$f"
}
case "${1:-all}" in
    collect|process|analyze|verify|all) log "${1:-all}" python3 scripts/measure.py "${1:-all}" ;;
    *) echo "usage: $0 {collect|process|analyze|verify|all}" >&2; exit 2 ;;
esac
