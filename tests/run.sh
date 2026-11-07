#!/usr/bin/env bash
# Startet für jeden Test einen frischen Server mit leerem Datenverzeichnis und führt den Test dagegen aus.
#   tests/run.sh                 alle Tests
#   tests/run.sh tests/test_nav.py
# Der Plugin-Test braucht zusätzlich «lupa» (Lua 5.1 für Python): venv/bin/pip install -r requirements-dev.txt
set -uo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-venv/bin/python}
PORT=${PORT:-8090}
WORK=$(mktemp -d)
PID=""
cleanup() { [[ -n "$PID" ]] && kill "$PID" 2>/dev/null; rm -rf "$WORK"; }
trap cleanup EXIT

TESTS=("$@")
[[ ${#TESTS[@]} -eq 0 ]] && TESTS=(tests/test_*.py)
FAILED=0
for t in "${TESTS[@]}"; do
  rm -rf "$WORK/data" "$WORK/files"; mkdir -p "$WORK/data" "$WORK/files"
  GALLERY_DATA="$WORK/data" GALLERY_ADMIN_PASSWORD=testadmin123 \
    "$PY" -m uvicorn app.main:app --port "$PORT" --workers 2 >"$WORK/server.log" 2>&1 &
  PID=$!
  for _ in $(seq 50); do curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null && break; sleep 0.2; done
  echo "== $t"
  B="http://127.0.0.1:$PORT" DB="$WORK/data/gallery.db" FILES="$WORK/files" \
    PLUG="app/lightroom/ModelGallery.lrplugin" "$PY" "$t" >"$WORK/out.txt" 2>&1
  rc=$?
  grep -v '^PASS' "$WORK/out.txt"
  [[ $rc -ne 0 ]] && FAILED=1
  kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; PID=""
  if grep -q Traceback "$WORK/server.log"; then echo "Serverfehler:"; cat "$WORK/server.log"; FAILED=1; fi
done
exit $FAILED
