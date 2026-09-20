#!/usr/bin/env bash
# Reproduce every result that needs nothing but a Python interpreter.
# Output lands in out/, one file per experiment. Exits non-zero if any fails.
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p out
fail=0

run () {                                   # run <name> <command...>
  local name=$1; shift
  printf '%-26s ' "$name"
  if "$@" > "../out/$name.txt" 2>&1; then
    echo "ok    -> out/$name.txt"
  else
    echo "FAILED (see out/$name.txt)"; fail=1
  fi
}

cd experiments

echo "=== assurance contract ==="
run contract              python3 contract.py

echo
echo "=== pilot re-analysis (thesis latency data) ==="
run reanalyse_pilot       python3 reanalyse_pilot.py
run loaded_arm_test       python3 loaded_arm_test.py

echo
echo "=== attestation envelope and sensitivity ==="
run envelope              python3 envelope.py
run interval_sweep        python3 interval_sweep.py
run contention_sensitivity python3 contention_sensitivity.py
run boundary              python3 boundary.py

echo
echo "=== rig sweep analysis (prints a notice until rig data exists) ==="
run analyse               python3 analyse.py

echo
echo "NOTE: carla_host.py, rsu_engine.py and load_gen.py need the HIL rig"
echo "      (CARLA host, physical ESP32 OBU, TPM/Keylime RSU, MQTT broker)"
echo "      and are deliberately not run here. See README.md."
echo
[ $fail -eq 0 ] && echo "ALL OK" || echo "SOME FAILED"
exit $fail
