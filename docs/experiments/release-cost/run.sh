#!/bin/sh
set -eu
: "${OUTPUT:?set OUTPUT to a new evidence directory}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
COMMAND=${1:-all}
if [ "$#" -gt 0 ]; then shift; fi
exec "${PYTHON:-python3}" "$ROOT/docs/experiments/release-cost/suite.py" "$COMMAND" \
    --output "$OUTPUT" --run-id "${RUN_ID:-release-cost}" "$@"
