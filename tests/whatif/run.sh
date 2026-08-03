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

echo "--- integrity guard: modified/FAIL, absent/PASS, unreadable/UNKNOWN ---"
# The guard's stat tasks carry become: yes for real deployments, where the
# connecting user needs root to read files like /etc/ssh/sshd_config. This
# self-test only touches /tmp probe files the invoking user already owns, so
# it forces ansible_become=false (an extra-var, which outranks the task-level
# become: yes) rather than requiring passwordless sudo on every dev machine
# that runs the test suite. test-integrity.yml holds three plays: a mutated
# file must FAIL, two files absent throughout must PASS, and a file that
# exists but can't be checksummed (chmod 000) must report UNKNOWN rather than
# silently comparing ABSENT==ABSENT and reporting PASS having verified nothing.
if $PLAYBOOK -i localhost, tests/whatif/test-integrity.yml -e "ansible_become=false" >/tmp/wi-int.log 2>&1; then
  echo "PASS: guard classified all three cases correctly"
else
  echo "FAIL: guard did not behave correctly"; tail -20 /tmp/wi-int.log; rc=1
fi
rm -f /tmp/wi-probe-*.txt

echo "--- drift normalisation ---"
if $PLAYBOOK -i localhost, tests/whatif/test-normalise.yml >/tmp/wi-norm.log 2>&1; then
  echo "PASS: normalisation distinguishes cosmetic from functional"
else
  echo "FAIL: normalisation is wrong"; tail -20 /tmp/wi-norm.log; rc=1
fi

echo "--- drift check survives malformed/empty/missing live files and a broken render ---"
# A crash in drift.yml used to abort main.yml before report.yml ever ran,
# discarding every other group's results. Each play in test-drift.yml seeds
# a sentinel result before running the real drift.yml and asserts it is
# still present afterwards, and that the broken case classifies as UNKNOWN
# rather than PASS/FAIL or a fatal error.
if $PLAYBOOK -i localhost, tests/whatif/test-drift.yml >/tmp/wi-drift.log 2>&1; then
  echo "PASS: drift check degrades to UNKNOWN without crashing or losing other results"
else
  echo "FAIL: drift check crashed or misclassified a broken input"; tail -30 /tmp/wi-drift.log; rc=1
fi

exit $rc
