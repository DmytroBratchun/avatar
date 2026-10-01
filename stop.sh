#!/usr/bin/env bash
# Stops the backend (uvicorn) and frontend (Vite dev server) started by start.sh.
# Matches by process command line via PowerShell rather than trusting PID files -
# npm on Windows launches vite through a wrapper process, so the PID start.sh
# records isn't always the real node process; command-line matching finds it
# regardless of how it was launched.
#
# The paid captioning worker (app.pipeline.worker), if you started it yourself,
# is left untouched - stop it separately (Ctrl+C in its terminal, or kill it by
# pid the same way) so you never lose in-progress captioning by accident.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"

echo "Stopping backend (uvicorn) and frontend (vite)..."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ROOT_DIR/stop.ps1"

rm -f "$RUN_DIR/backend.pid" "$RUN_DIR/frontend.pid"
echo "Done."
