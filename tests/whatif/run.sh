#!/usr/bin/env bash
# Verifies the report engine's exit-code contract.
# A preflight that cannot fail is worthless, so the FAIL case is the important one.
set -uo pipefail
cd "$(dirname "$0")/../.."
PLAYBOOK=./venv/bin/ansible-playbook
rc=0

echo "--- no failures: expect exit 0 ---"
if $PLAYBOOK -i localhost, tests/whatif/test-report-pass.yml >/tmp/wi-pass.log 2>&1; then
  echo "PASS: clean run exited 0"
else
  echo "FAIL: clean run exited non-zero"; tail -20 /tmp/wi-pass.log; rc=1
fi

echo "--- one failure: expect non-zero ---"
if $PLAYBOOK -i localhost, tests/whatif/test-report-fail.yml >/tmp/wi-fail.log 2>&1; then
  echo "FAIL: a FAIL result did not fail the play"; rc=1
else
  echo "PASS: FAIL result produced a non-zero exit"
fi

echo "--- the failing check must be named in the output ---"
if grep -q "user survives AllowUsers" /tmp/wi-fail.log; then
  echo "PASS: failing check named in output"
else
  echo "FAIL: output does not name the failing check"; tail -20 /tmp/wi-fail.log; rc=1
fi

exit $rc
