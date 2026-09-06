#!/bin/sh
# Run the CLI from a checkout. PYTHONPATH is set explicitly because hatchling's
# editable-install .pth file is written without a trailing newline, which
# Python's site module silently ignores.
ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec uv run --project "$ROOT" python -m football_pool "$@"
