#!/usr/bin/env bash
#
# Run every oracle test in solution/ against the codebases in codebase/.
# Each suite imports the real code from codebase/<n>, so it fails while the
# injected bug is present and passes once the fix is applied.
#
# Usage: bash solution/run_all.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS=0

run() {
  local label="$1"
  shift
  echo "=============================================================="
  echo "== $label"
  echo "=============================================================="
  if "$@"; then
    echo "-- PASS: $label"
  else
    echo "-- FAIL: $label"
    STATUS=1
  fi
  echo
}

run "1 ShopFront  (node)"  node --test "$HERE/1/test_discount_fix.js"
run "2 CalcLang   (python)" python3 "$HERE/2/test_precedence_fix.py"
run "3 PacketWatch(python)" python3 "$HERE/3/test_ipv4_endianness_fix.py"
run "4 QueueMaster(python)" python3 "$HERE/4/test_backoff_fix.py"
run "5 AuthCore   (python)" python3 "$HERE/5/test_alg_none_fix.py"

if [ "$STATUS" -eq 0 ]; then
  echo "All oracle tests passed — the injected bugs appear to be fixed."
else
  echo "One or more oracle tests failed — the codebases still contain bugs."
fi
exit "$STATUS"
