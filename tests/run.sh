#!/usr/bin/env bash
# Render every role template under two variable sets and diff against goldens.
# Regenerate goldens deliberately with: tests/run.sh --update
set -uo pipefail
cd "$(dirname "$0")/.."

PLAYBOOK=./venv/bin/ansible-playbook
UPDATE=0
[ "${1:-}" = "--update" ] && UPDATE=1

rc=0
for set in defaults hardened; do
  out="$PWD/tests/render/out/$set"
  exp="tests/render/expected/$set"
  rm -rf "$out"

  if ! $PLAYBOOK -i localhost, tests/render/render.yml \
       -e "varset=vars-$set.yml" -e "outdir=$out" >/dev/null; then
    echo "FAIL: $set (render error)"
    rc=1
    continue
  fi

  if [ "$UPDATE" = "1" ]; then
    rm -rf "$exp"; mkdir -p "$exp"; cp -a "$out"/. "$exp"/
    echo "UPDATED: $set"
    continue
  fi

  if diff -ru "$exp" "$out"; then
    echo "PASS: $set"
  else
    echo "FAIL: $set (output differs from golden)"
    rc=1
  fi
done

exit $rc
