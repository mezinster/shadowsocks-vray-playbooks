# `--whatif` Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only preflight that proves, against a real host, that the hardening playbook will not lock the operator out and will not break the proxy — and that exits non-zero when it cannot.

**Architecture:** A new `roles/whatif` that renders the *same* Jinja2 templates the real playbook uses (never copies of them) into a temp directory, asserts over the results, and prints a PASS/FAIL/UNKNOWN report. Separately, three edits to `roles/setup` make `ansible-playbook --check` truthful, since it currently reports `rc: 0` for validations that never ran.

**Tech Stack:** Ansible 4.6.0 / ansible-core 2.11.5, Python 3.9 venv, Jinja2 3.0.2.

**Source spec:** `docs/superpowers/specs/2026-08-03-whatif-preflight-design.md`

## Global Constraints

- **Branch from `harden-playbooks`, not `master`.** This work renders `roles/setup/templates/10-hardening.conf.j2`, which exists only on that branch (PR #1, unmerged). Branching from `master` gives a role whose templates do not exist.
- **`deploy/roles/whatif/templates/` must not exist.** The role renders the real roles' templates via relative sibling paths. Copying a template into this role reintroduces the exact drift the design exists to prevent.
- **The role must modify nothing on the target.** Every write goes under a `tempfile`-created directory, removed in an `always:` block. No task uses `notify:`. The role registers no handlers.
- **`UNKNOWN` is never rendered as `PASS`.** A check that cannot be performed reports `UNKNOWN` and is printed; it does not fail the run and does not count as success.
- **Verified 2026-08-03 on ansible-core 2.11.5** — relied on throughout: `command` in check mode returns `skipped: true` **with** `rc: 0` and `stdout: ""`; `stat` executes normally in check mode; `template` reports `changed: true` while writing nothing and registering no `backup_file`; both `../../<role>/templates/x.j2` and `{{ playbook_dir }}/roles/<role>/templates/x.j2` resolve from a sibling role.
- Task names in this repo are English; playbook `name:` fields are Russian. Match the file being edited.
- All ansible commands run via `./venv/bin/ansible-playbook` with the collections path pinned by `ansible.cfg`.

## The result record

Every check appends one dict to `whatif_results`. This shape is fixed and used by every task:

```yaml
{ group: 'LOCKOUT', status: 'PASS', name: 'rendered sshd config valid', detail: 'sshd -t exit 0' }
```

- `group` — one of `LOCKOUT`, `PROXY`, `DRIFT`, `INTEGRITY`
- `status` — one of `PASS`, `FAIL`, `UNKNOWN`
- `name` — short, stable, lowercase
- `detail` — evidence; the value observed, not a restatement of the name

---

### Task 1: Skeleton, report engine, Makefile targets

**Why first:** the report engine is the only genuinely unit-testable piece, and every later task appends to the structure it consumes. Building it first means later tasks have a working harness to verify against.

**Files:**
- Create: `deploy/whatif.yml`
- Create: `deploy/roles/whatif/tasks/main.yml`
- Create: `deploy/roles/whatif/tasks/report.yml`
- Create: `deploy/roles/whatif/tasks/report_group.yml`
- Create: `deploy/roles/whatif/defaults/main.yml`
- Create: `tests/whatif/test-report-pass.yml`
- Create: `tests/whatif/test-report-fail.yml`
- Create: `tests/whatif/run.sh`
- Modify: `Makefile`

**Interfaces:**
- Produces: `whatif_results` — the list every later task appends to.
- Produces: `deploy/roles/whatif/tasks/report.yml` — consumes `whatif_results`, prints the table, fails when any entry has `status: FAIL`.
- Produces: `./tests/whatif/run.sh` — exit 0 when the report engine behaves correctly. Later tasks re-run it.

- [ ] **Step 1: Write the failing tests**

Create `tests/whatif/test-report-pass.yml` — all PASS plus one UNKNOWN, must succeed:

```yaml
# Report engine: a run with no FAIL must succeed, even with UNKNOWN present.
- name: Report engine — no failures
  hosts: localhost
  connection: local
  gather_facts: no
  vars:
    whatif_results:
      - { group: 'LOCKOUT',   status: 'PASS',    name: 'rendered sshd config valid', detail: 'sshd -t exit 0' }
      - { group: 'LOCKOUT',   status: 'UNKNOWN', name: 'cloud firewall allows port',  detail: 'CANNOT VERIFY' }
      - { group: 'PROXY',     status: 'PASS',    name: 'tunnel serves traffic',       detail: 'HTTP/1.1 101' }
      - { group: 'INTEGRITY', status: 'PASS',    name: 'no file modified',            detail: '6 checksums unchanged' }
  tasks:
    - include_tasks: ../../deploy/roles/whatif/tasks/report.yml
```

Create `tests/whatif/test-report-fail.yml` — one FAIL, must fail the play:

```yaml
# Report engine: a single FAIL must fail the play with a non-zero exit.
- name: Report engine — one failure
  hosts: localhost
  connection: local
  gather_facts: no
  vars:
    whatif_results:
      - { group: 'LOCKOUT',   status: 'PASS', name: 'rendered sshd config valid', detail: 'sshd -t exit 0' }
      - { group: 'LOCKOUT',   status: 'FAIL', name: "user survives AllowUsers",   detail: "AllowUsers=nosuchuser, connected as ubuntu" }
      - { group: 'INTEGRITY', status: 'PASS', name: 'no file modified',           detail: '6 checksums unchanged' }
  tasks:
    - include_tasks: ../../deploy/roles/whatif/tasks/report.yml
```

Create `tests/whatif/run.sh`, then `chmod +x`:

```bash
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
chmod +x tests/whatif/run.sh
./tests/whatif/run.sh; echo "exit=$?"
```

Expected: failures, because `report.yml` does not exist yet.

- [ ] **Step 3: Write the report engine**

Create `deploy/roles/whatif/tasks/report_group.yml`:

```yaml
# Emits one section of the report. Included once per group by report.yml.
- name: Adding the group header
  set_fact:
    _wi_lines: "{{ _wi_lines | default([]) + [''] + [group_name] }}"
  when: whatif_results | selectattr('group', 'equalto', group_name) | list | length > 0

- name: Adding the group's result lines
  set_fact:
    _wi_lines: "{{ _wi_lines + ['  ' ~ '%-8s %-38s %s' | format(item.status, item.name, item.detail)] }}"
  loop: "{{ whatif_results | selectattr('group', 'equalto', group_name) | list }}"
  loop_control:
    label: "{{ item.name }}"
```

Create `deploy/roles/whatif/tasks/report.yml`:

```yaml
- name: Counting results
  set_fact:
    _wi_pass: "{{ whatif_results | selectattr('status', 'equalto', 'PASS') | list | length }}"
    _wi_fail: "{{ whatif_results | selectattr('status', 'equalto', 'FAIL') | list | length }}"
    _wi_unknown: "{{ whatif_results | selectattr('status', 'equalto', 'UNKNOWN') | list | length }}"
    _wi_lines: []

- name: Building each report section
  include_tasks: report_group.yml
  loop: "{{ ['LOCKOUT', 'PROXY', 'DRIFT', 'INTEGRITY'] }}"
  loop_control:
    loop_var: group_name

- name: Rendering the preflight report
  debug:
    msg: "{{ ['WHATIF PREFLIGHT — ' ~ inventory_hostname] + _wi_lines + ['',
              'RESULT: ' ~ _wi_pass ~ ' PASS, ' ~ _wi_fail ~ ' FAIL, ' ~ _wi_unknown ~ ' UNKNOWN'] }}"

# UNKNOWN never fails the run — it means "could not be checked", not "is broken".
- name: Failing when any check failed
  fail:
    msg: >-
      Preflight found {{ _wi_fail }} failing check(s):
      {{ whatif_results | selectattr('status', 'equalto', 'FAIL') | map(attribute='name') | join(', ') }}.
      Do NOT run the hardening playbook against this host until they are resolved.
  when: _wi_fail | int > 0
```

Create `deploy/roles/whatif/defaults/main.yml`:

```yaml
# Opt-in: start a shadow sshd and authenticate against it for real (see Task 6).
whatif_shadow_sshd: no
# Fail the certificate check below this many days remaining.
whatif_cert_min_days: 14
```

Create `deploy/roles/whatif/tasks/main.yml`:

```yaml
# Read-only preflight. Writes ONLY under a temp directory, removed in always:.
# No task here uses notify:, and this role registers no handlers.
- name: Running the preflight
  block:
    - name: Creating a working directory for rendered artefacts
      tempfile:
        state: directory
        suffix: whatif
      register: _wi_tmp
      changed_when: false

    - name: Starting with an empty result set
      set_fact:
        whatif_results: []

    - name: Reporting
      include_tasks: report.yml

  always:
    - name: Removing the working directory
      file:
        path: "{{ _wi_tmp.path }}"
        state: absent
      when: _wi_tmp.path is defined
      changed_when: false
```

Create `deploy/whatif.yml`:

```yaml
- hosts: remote
  name: Предполётная проверка (ничего не изменяет)
  remote_user: "{{ user }}"
  gather_facts: yes
  vars:
    ansible_python_interpreter: /usr/bin/python3

  roles:
    - role: whatif
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
./tests/whatif/run.sh; echo "exit=$?"
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/whatif.yml
```

Expected: three `PASS` lines and `exit=0`; clean syntax-check.

- [ ] **Step 5: Add the Makefile targets**

Append to `Makefile`:

```make
whatif:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/whatif.yml

whatif_deep:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/whatif.yml -e whatif_shadow_sshd=yes

# The other half — the REAL setup playbook in honest check mode. Produces an
# Ansible diff, not the preflight report. See Task 7.
whatif_preview:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/setup.yml --check --diff
```

- [ ] **Step 6: Verify and commit**

```bash
make -n whatif whatif_deep whatif_preview
./tests/whatif/run.sh
git add deploy/whatif.yml deploy/roles/whatif tests/whatif Makefile
git commit -m "Add whatif preflight skeleton and report engine"
```

---

### Task 2: Connection identity and the integrity guard

**Why early:** every later task adds tasks that touch the target, and the integrity guard must bracket all of them. The connection facts belong here because the guard needs the connecting user's `authorized_keys` path.

**Files:**
- Create: `deploy/roles/whatif/tasks/connection.yml`
- Create: `deploy/roles/whatif/tasks/integrity_before.yml`
- Create: `deploy/roles/whatif/tasks/integrity_after.yml`
- Modify: `deploy/roles/whatif/tasks/main.yml`
- Modify: `deploy/roles/whatif/defaults/main.yml`

**Interfaces:**
- Consumes: `whatif_results` (Task 1), `_wi_tmp.path` (Task 1).
- Produces: `_wi_user` — the username the play is actually connected as, a plain string.
- Produces: `_wi_authorized_keys` — absolute path to that user's `authorized_keys`.
- Produces: `_wi_guarded_paths` — the list of paths checksummed. Later tasks must not write to any of them.

- [ ] **Step 0: Identify the live connection**

Create `deploy/roles/whatif/tasks/connection.yml`:

```yaml
- name: Determining the connecting user
  command: id -un
  register: _wi_whoami
  changed_when: false

- name: Recording connection facts
  set_fact:
    _wi_user: "{{ _wi_whoami.stdout | trim }}"
    _wi_authorized_keys: "{{ ansible_env.HOME }}/.ssh/authorized_keys"
```

`id -un` is used rather than `ansible_user` because `ansible_user` is unset when
the username comes from `~/.ssh/config` or from `ansible_ssh_user`. The preflight
must reason about the account it is *actually* using, not the one it was
configured with.

In `main.yml`, include `connection.yml` immediately after the
`set_fact: whatif_results: []` task and before everything else.

- [ ] **Step 1: Add the guarded path list**

Append to `deploy/roles/whatif/defaults/main.yml`:

```yaml
# Files the hardening playbook can write. The preflight checksums each before
# and after its own run and asserts they are unchanged — so "modified nothing"
# is a verified output line, not a promise.
whatif_guarded_paths:
  - /etc/ssh/sshd_config
  - /etc/ssh/sshd_config.d/10-hardening.conf
  - /etc/fail2ban/jail.local
  - /etc/fstab
  - /etc/sysctl.d/99-swappiness.conf
```

- [ ] **Step 2: Write the before-snapshot**

Create `deploy/roles/whatif/tasks/integrity_before.yml`:

```yaml
- name: Recording the full guarded path list
  set_fact:
    _wi_guarded_paths: "{{ whatif_guarded_paths + [_wi_authorized_keys] }}"

- name: Snapshotting guarded files before any check runs
  become: yes
  stat:
    path: "{{ item }}"
    get_checksum: yes
    checksum_algorithm: sha256
  loop: "{{ _wi_guarded_paths }}"
  register: _wi_before
  changed_when: false

# Flatten to {path: checksum} so the after-comparison is a plain dict diff.
# A missing file is recorded as 'ABSENT' — its later appearance is a change.
- name: Reducing the snapshot to a path/checksum map
  set_fact:
    _wi_before_map: "{{ _wi_before_map | default({}) | combine({ item.item: (item.stat.checksum | default('ABSENT')) }) }}"
  loop: "{{ _wi_before.results }}"
  loop_control:
    label: "{{ item.item }}"
```

- [ ] **Step 3: Write the after-check**

Create `deploy/roles/whatif/tasks/integrity_after.yml`. The comparison is a loop
building a list of mismatched paths — deliberately not a `zip`/`selectattr` chain,
because `selectattr` cannot compare two attributes of the same item:

```yaml
- name: Snapshotting guarded files after all checks
  become: yes
  stat:
    path: "{{ item }}"
    get_checksum: yes
    checksum_algorithm: sha256
  loop: "{{ _wi_guarded_paths }}"
  register: _wi_after
  changed_when: false

- name: Collecting any path whose checksum moved
  set_fact:
    _wi_modified: "{{ _wi_modified | default([]) + [item.item] }}"
  loop: "{{ _wi_after.results }}"
  loop_control:
    label: "{{ item.item }}"
  when: (item.stat.checksum | default('ABSENT')) != _wi_before_map[item.item]

- name: Recording the integrity result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'INTEGRITY',
        'status': _wi_integrity_ok | ternary('PASS', 'FAIL'),
        'name': 'no file modified by this preflight',
        'detail': _wi_integrity_ok | ternary(
            (_wi_guarded_paths | length) ~ ' checksums unchanged',
            'MODIFIED: ' ~ (_wi_modified | default([]) | join(', '))) }] }}"
  vars:
    _wi_integrity_ok: "{{ (_wi_modified | default([]) | length) == 0 }}"
```

- [ ] **Step 4: Wire both into main.yml**

In `deploy/roles/whatif/tasks/main.yml`, inside the `block:`, place
`integrity_before.yml` immediately after the `set_fact: whatif_results: []` task,
and `integrity_after.yml` immediately before `report.yml`.

- [ ] **Step 5: Prove the guard detects a real modification**

A guard that has never fired is not known to work. Test the comparison logic on
**localhost**, with no target host and no privileged paths — create
`tests/whatif/test-integrity.yml`:

```yaml
# The integrity comparison must notice a file that changed between snapshots.
- name: Integrity guard detects modification
  hosts: localhost
  connection: local
  gather_facts: no
  tasks:
    - name: Creating two probe files
      copy:
        dest: "/tmp/wi-probe-{{ item }}.txt"
        content: "original\n"
      loop: [stable, mutated]

    # integrity_before.yml BUILDS _wi_guarded_paths from
    # whatif_guarded_paths + [_wi_authorized_keys], so feed it through those two
    # variables. Setting _wi_guarded_paths directly here would be overwritten.
    - include_tasks: ../../deploy/roles/whatif/tasks/integrity_before.yml
      vars:
        whatif_guarded_paths: ['/tmp/wi-probe-mutated.txt']
        _wi_authorized_keys: /tmp/wi-probe-stable.txt

    - name: Mutating exactly one of them
      copy:
        dest: /tmp/wi-probe-mutated.txt
        content: "CHANGED\n"

    - include_tasks: ../../deploy/roles/whatif/tasks/integrity_after.yml

    - name: The guard must report FAIL and name the mutated file
      assert:
        that:
          - whatif_results | selectattr('group','equalto','INTEGRITY') | selectattr('status','equalto','FAIL') | list | length == 1
          - "'wi-probe-mutated' in (whatif_results | selectattr('group','equalto','INTEGRITY') | map(attribute='detail') | join(' '))"
          - "'wi-probe-stable' not in (whatif_results | selectattr('group','equalto','INTEGRITY') | map(attribute='detail') | join(' '))"
        fail_msg: "integrity guard did not detect the mutated file, or falsely flagged the stable one"
```

Keep **both** assertions. Catching the mutated file proves the guard fires; not
flagging the stable one proves it is not simply reporting FAIL for everything.
A guard that always fails is as useless as one that never does.

Add to `tests/whatif/run.sh` before the final `exit`:

```bash
echo "--- integrity guard detects modification ---"
if $PLAYBOOK -i localhost, tests/whatif/test-integrity.yml >/tmp/wi-int.log 2>&1; then
  echo "PASS: guard detected the mutated file only"
else
  echo "FAIL: guard did not behave correctly"; tail -20 /tmp/wi-int.log; rc=1
fi
rm -f /tmp/wi-probe-*.txt
```

- [ ] **Step 6: Verify and commit**

```bash
./tests/whatif/run.sh
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/whatif.yml
git add deploy/roles/whatif
git commit -m "Add integrity guard proving the preflight modifies nothing"
```

---

### Task 3: The lockout checks

**Files:**
- Create: `deploy/roles/whatif/tasks/ssh_safety.yml`
- Modify: `deploy/roles/whatif/tasks/main.yml`

**Interfaces:**
- Consumes: `whatif_results`, `_wi_tmp.path` (Task 1); `_wi_user`, `_wi_authorized_keys` (Task 2).
- Produces: `_wi_sshd` — the decoded text of the rendered sshd drop-in, a plain string.
- Produces: `_wi_ss` — the registered result of `ss -tlpnH`, with `.stdout_lines`. **Task 4 reuses this**, so `ssh_safety.yml` must be included before `service_health.yml`.
- Produces: the rendered drop-in at `{{ _wi_tmp.path }}/10-hardening.conf`. **Task 6 reuses this file.**

- [ ] **Step 1: Render the real sshd template and validate it**

Create `deploy/roles/whatif/tasks/ssh_safety.yml`, first section:

```yaml
# Renders the REAL template — never a copy. A copy would drift and the report
# would go green against a template nobody runs.
- name: Rendering the sshd drop-in the playbook would install
  template:
    src: ../../setup/templates/10-hardening.conf.j2
    dest: "{{ _wi_tmp.path }}/10-hardening.conf"
    mode: '0644'
  changed_when: false

- name: Reading the rendered drop-in
  slurp:
    src: "{{ _wi_tmp.path }}/10-hardening.conf"
  register: _wi_sshd_raw

- name: Decoding the rendered drop-in
  set_fact:
    _wi_sshd: "{{ _wi_sshd_raw.content | b64decode }}"

- name: Validating the rendered drop-in with sshd itself
  become: yes
  command: "/usr/sbin/sshd -t -f {{ _wi_tmp.path }}/10-hardening.conf"
  register: _wi_sshd_t
  failed_when: false
  changed_when: false

- name: Recording the config-validity result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'LOCKOUT',
        'status': (_wi_sshd_t.rc == 0) | ternary('PASS', 'FAIL'),
        'name': 'rendered sshd config valid',
        'detail': (_wi_sshd_t.rc == 0) | ternary('sshd -t exit 0',
                   'sshd -t exit ' ~ _wi_sshd_t.rc ~ ': ' ~ (_wi_sshd_t.stderr | default('') | truncate(120))) }] }}"
```

- [ ] **Step 2: Assert the connecting user survives AllowUsers**

Append to `ssh_safety.yml`:

```yaml
- name: Extracting AllowUsers from the rendered config
  set_fact:
    _wi_allowusers: >-
      {{ _wi_sshd
         | regex_findall('(?m)^AllowUsers[ \t]+(.*)$')
         | map('trim') | join(' ') | split(' ')
         | reject('equalto', '') | list }}

- name: Recording the AllowUsers result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'LOCKOUT',
        'status': _wi_allow_ok | ternary('PASS', 'FAIL'),
        'name': 'connecting user survives AllowUsers',
        'detail': _wi_allow_ok | ternary(
            (_wi_allowusers | length == 0) | ternary('no AllowUsers directive', 'AllowUsers ' ~ (_wi_allowusers | join(' '))),
            'AllowUsers ' ~ (_wi_allowusers | join(' ')) ~ ' excludes connected user ' ~ _wi_user) }] }}"
  vars:
    _wi_allow_ok: "{{ (_wi_allowusers | length == 0) or (_wi_user in _wi_allowusers) }}"
```

- [ ] **Step 3: The remaining lockout assertions**

Append to `ssh_safety.yml`. Each follows the same record-a-result shape:

```yaml
- name: Checking authorized_keys
  become: yes
  stat:
    path: "{{ _wi_authorized_keys }}"
  register: _wi_ak

- name: Counting authorized keys
  become: yes
  command: "grep -cvE '^\\s*(#|$)' {{ _wi_authorized_keys }}"
  register: _wi_ak_count
  failed_when: false
  changed_when: false
  when: _wi_ak.stat.exists

- name: Recording the authorized_keys result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'LOCKOUT',
        'status': _wi_ak_ok | ternary('PASS', 'FAIL'),
        'name': 'authorized_keys present and non-empty',
        'detail': _wi_ak_ok | ternary((_wi_ak_count.stdout | default('0') | trim) ~ ' key(s)',
                                      'missing or empty: ' ~ _wi_authorized_keys) }] }}"
  vars:
    _wi_ak_ok: "{{ _wi_ak.stat.exists and ((_wi_ak_count.stdout | default('0') | trim | int) > 0) }}"

- name: Listing listening TCP ports
  become: yes
  command: ss -tlpnH
  register: _wi_ss
  changed_when: false

- name: Recording the target-port result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'LOCKOUT',
        'status': _wi_port_ok | ternary('PASS', 'FAIL'),
        'name': 'target ssh port is free or already sshd',
        'detail': _wi_port_ok | ternary('port ' ~ ssh_port ~ ' free or held by sshd',
                                        'port ' ~ ssh_port ~ ' held by another process') }] }}"
  vars:
    _wi_port_lines: "{{ _wi_ss.stdout_lines | select('search', ':' ~ ssh_port ~ ' ') | list }}"
    _wi_port_ok: "{{ (_wi_port_lines | length == 0) or (_wi_port_lines | select('search', 'sshd') | list | length == _wi_port_lines | length) }}"

- name: Recording the break-glass result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'LOCKOUT',
        'status': _wi_bg_ok | ternary('PASS', 'FAIL'),
        'name': 'port 22 remains bound',
        'detail': _wi_bg_ok | ternary('break-glass preserved', 'ssh_keep_port_22 is true but no Port 22 in rendered config') }] }}"
  vars:
    _wi_bg_ok: "{{ (not (ssh_keep_port_22 | bool)) or ((ssh_port | int) == 22) or ('Port 22' in _wi_sshd) }}"

- name: Recording the key-algorithm result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'LOCKOUT',
        'status': _wi_alg_ok | ternary('PASS', 'FAIL'),
        'name': 'key algorithms not narrowed',
        'detail': _wi_alg_ok | ternary('no PubkeyAcceptedAlgorithms directive',
                                       'PubkeyAcceptedAlgorithms present — may reject your key type') }] }}"
  vars:
    _wi_alg_ok: "{{ 'PubkeyAcceptedAlgorithms' not in _wi_sshd }}"

# The cloud firewall cannot be checked: there is no AWS CLI locally or on the
# host, and AmazonLightsailInstanceRole cannot call the Lightsail API. Reporting
# UNKNOWN rather than PASS is deliberate — silently passing something
# unverifiable is how a preflight becomes a liability.
- name: Recording the cloud-firewall result as unverifiable
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'LOCKOUT',
        'status': 'UNKNOWN',
        'name': 'cloud firewall allows ssh_port',
        'detail': 'CANNOT VERIFY — open port ' ~ ssh_port ~ ' in your provider console manually' }] }}"
```

- [ ] **Step 4: Wire in**

In `main.yml`, include `ssh_safety.yml` after `integrity_before.yml`.

- [ ] **Step 5: Verify and commit**

```bash
./tests/whatif/run.sh
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/whatif.yml
git add deploy/roles/whatif
git commit -m "Add connection identity and SSH lockout checks"
```

---

### Task 4: Proxy health checks

**Files:**
- Create: `deploy/roles/whatif/tasks/service_health.yml`
- Modify: `deploy/roles/whatif/tasks/main.yml`

**Interfaces:**
- Consumes: `whatif_results`, `whatif_cert_min_days` (Task 1 defaults); `_wi_user` (Task 2); `_wi_ss` (Task 3 — the `ss -tlpnH` result, reused here rather than re-run); `host` and `endpoint` from `variables.yml`.
- **Ordering requirement:** `service_health.yml` must be included *after* `ssh_safety.yml`, because `_wi_ss` is registered there.

- [ ] **Step 1: Write the health checks**

Create `deploy/roles/whatif/tasks/service_health.yml`:

```yaml
- name: Listing running containers
  command: docker ps --format '{{ '{{' }}.Names{{ '}}' }}'
  register: _wi_docker
  failed_when: false
  changed_when: false

- name: Recording the container result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'PROXY',
        'status': (_wi_missing | length == 0) | ternary('PASS', 'FAIL'),
        'name': 'proxy containers running',
        'detail': (_wi_missing | length == 0) | ternary('nginx, shadowsocks, certbot up',
                                                        'not running: ' ~ (_wi_missing | join(', '))) }] }}"
  vars:
    _wi_expected: ['nginx_container', 'shadowsocks_container', 'certbot_container']
    _wi_missing: "{{ _wi_expected | reject('in', _wi_docker.stdout_lines | default([])) | list }}"

# A real WebSocket upgrade through the live nginx, from the target itself.
# Proves the tunnel end to end, not merely that a port is open.
- name: Performing a WebSocket handshake through the proxy
  shell: |
    printf 'GET /{{ endpoint }} HTTP/1.1\r\nHost: {{ host }}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n' \
      | timeout 15 openssl s_client -connect 127.0.0.1:443 -servername {{ host }} -quiet 2>/dev/null \
      | head -1
  register: _wi_ws
  failed_when: false
  changed_when: false

- name: Recording the tunnel result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'PROXY',
        'status': _wi_ws_ok | ternary('PASS', 'FAIL'),
        'name': 'tunnel serves traffic',
        'detail': _wi_ws_ok | ternary('HTTP/1.1 101', 'expected 101, got: ' ~ (_wi_ws.stdout | default('') | trim | truncate(60))) }] }}"
  vars:
    _wi_ws_ok: "{{ '101' in (_wi_ws.stdout | default('')) }}"

- name: Recording the plugin-binding result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'PROXY',
        'status': _wi_9000_ok | ternary('PASS', 'FAIL'),
        'name': 'v2ray-plugin is loopback-only',
        'detail': _wi_9000_ok | ternary('127.0.0.1:9000', 'port 9000 is NOT loopback-only') }] }}"
  vars:
    _wi_9000: "{{ _wi_ss.stdout_lines | select('search', ':9000 ') | list }}"
    _wi_9000_ok: "{{ (_wi_9000 | length > 0) and (_wi_9000 | select('search', '127.0.0.1:9000') | list | length == _wi_9000 | length) }}"

- name: Reading the certificate expiry
  become: yes
  shell: >-
    openssl x509 -in /home/{{ _wi_user }}/shadowsocks_config/certs-data/certs/live/{{ host }}/fullchain.pem
    -noout -enddate | cut -d= -f2
  register: _wi_cert
  failed_when: false
  changed_when: false

- name: Recording the certificate result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'PROXY',
        'status': _wi_cert_status,
        'name': 'certificate healthy',
        'detail': _wi_cert_detail }] }}"
  vars:
    _wi_cert_days: "{{ ((_wi_cert.stdout | trim | to_datetime('%b %d %H:%M:%S %Y %Z')).timestamp() - ansible_date_time.epoch | int) // 86400 if _wi_cert.rc == 0 else -1 }}"
    _wi_cert_status: "{{ 'UNKNOWN' if _wi_cert.rc != 0 else (((_wi_cert_days | int) >= (whatif_cert_min_days | int)) | ternary('PASS', 'FAIL')) }}"
    _wi_cert_detail: "{{ 'could not read certificate' if _wi_cert.rc != 0 else ((_wi_cert_days | int) ~ ' days remaining') }}"
```

**Implementer note:** `_wi_ss` is registered in `ssh_safety.yml` (Task 3), so
`service_health.yml` must be included after it. The `to_datetime` date-maths above
is the intent; if it does not work under Jinja2 3.0.2, compute the remaining days
with `openssl x509 -checkend $((14*86400))` (exit 0 = more than 14 days left) and
report `PASS`/`FAIL` from the exit code instead. Prove whichever you use.

- [ ] **Step 2: Wire in**

Include `service_health.yml` after `ssh_safety.yml` in `main.yml`.

- [ ] **Step 3: Verify and commit**

```bash
./tests/whatif/run.sh
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/whatif.yml
git add deploy/roles/whatif
git commit -m "Add proxy health checks to the preflight"
```

---

### Task 5: Drift detection

**Files:**
- Create: `deploy/roles/whatif/tasks/drift.yml`
- Create: `tests/whatif/test-normalise.yml`
- Modify: `tests/whatif/run.sh`
- Modify: `deploy/roles/whatif/tasks/main.yml`

**Interfaces:**
- Consumes: `whatif_results`, `_wi_tmp.path`, `_wi_user`.
- Produces: nothing later tasks consume.

- [ ] **Step 1: Write the failing normalisation test**

Normalisation decides whether a difference is functional. Get it wrong in the
lenient direction and the check goes permanently green; wrong in the strict
direction and it cries wolf until ignored. It is the one piece here worth a unit
test.

Create `tests/whatif/test-normalise.yml`:

```yaml
- name: Drift normalisation
  hosts: localhost
  connection: local
  gather_facts: no
  vars:
    _live: |
      # a hand-written comment
      server {

        listen 443 ssl;
      }
    _rendered: |
      # a DIFFERENT generated comment
      server {
        listen 443 ssl;
      }
    _rendered_functional: |
      server {
        listen 8443 ssl;
      }
  tasks:
    - name: Normalising
      set_fact:
        _n_live: "{{ _live | regex_replace('(?m)^\\s*#.*$', '') | regex_replace('(?m)[ \\t]+$', '') | regex_replace('\\n+', '\\n') | trim }}"
        _n_rendered: "{{ _rendered | regex_replace('(?m)^\\s*#.*$', '') | regex_replace('(?m)[ \\t]+$', '') | regex_replace('\\n+', '\\n') | trim }}"
        _n_functional: "{{ _rendered_functional | regex_replace('(?m)^\\s*#.*$', '') | regex_replace('(?m)[ \\t]+$', '') | regex_replace('\\n+', '\\n') | trim }}"

    - name: Comment and blank-line differences must normalise away
      assert:
        that: _n_live == _n_rendered
        fail_msg: "comment/whitespace difference was treated as functional"

    - name: A real directive change must NOT normalise away
      assert:
        that: _n_live != _n_functional
        fail_msg: "a functional difference was normalised away — the check would go permanently green"
```

Add to `tests/whatif/run.sh` before the final `exit`:

```bash
echo "--- drift normalisation ---"
if $PLAYBOOK -i localhost, tests/whatif/test-normalise.yml >/tmp/wi-norm.log 2>&1; then
  echo "PASS: normalisation distinguishes cosmetic from functional"
else
  echo "FAIL: normalisation is wrong"; tail -20 /tmp/wi-norm.log; rc=1
fi
```

- [ ] **Step 2: Run to verify it fails**

```bash
./tests/whatif/run.sh; echo "exit=$?"
```

Expected: the normalisation section fails — the regexes above may not be correct
as written. Fix them until both assertions hold. **Do not weaken the second
assertion to make it pass.**

- [ ] **Step 3: Write the drift check**

Create `deploy/roles/whatif/tasks/drift.yml`:

```yaml
- name: Rendering the server templates the deploy would install
  template:
    src: "../../server/templates/{{ item.src }}"
    dest: "{{ _wi_tmp.path }}/{{ item.name }}"
    mode: '0644'
  loop:
    - { src: 'nginx-release.j2',      name: 'nginx-release.conf' }
    - { src: 'shadowsocks-config.j2', name: 'shadowsocks-config.json' }
  changed_when: false

- name: Reading the rendered and live files
  slurp:
    src: "{{ item }}"
  loop:
    - "{{ _wi_tmp.path }}/nginx-release.conf"
    - "/home/{{ _wi_user }}/shadowsocks_config/docker/nginx/default.conf"
    - "{{ _wi_tmp.path }}/shadowsocks-config.json"
    - "/home/{{ _wi_user }}/shadowsocks_config/docker/shadowsocks/config.json"
  register: _wi_drift_raw
  failed_when: false

- name: Recording the nginx drift result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'DRIFT',
        'status': _wi_nginx_status,
        'name': 'nginx-release.j2 vs live',
        'detail': _wi_nginx_detail }] }}"
  vars:
    _wi_norm_rendered: "{{ (_wi_drift_raw.results[0].content | b64decode) | regex_replace('(?m)^\\s*#.*$', '') | regex_replace('(?m)[ \\t]+$', '') | regex_replace('\\n+', '\\n') | trim }}"
    _wi_norm_live: "{{ (_wi_drift_raw.results[1].content | b64decode) | regex_replace('(?m)^\\s*#.*$', '') | regex_replace('(?m)[ \\t]+$', '') | regex_replace('\\n+', '\\n') | trim }}"
    _wi_nginx_status: "{{ (_wi_norm_rendered == _wi_norm_live) | ternary('PASS', 'FAIL') }}"
    _wi_nginx_detail: "{{ (_wi_norm_rendered == _wi_norm_live) | ternary('comments/whitespace only', 'FUNCTIONAL DIFFERENCE — deploy_server would change behaviour') }}"

- name: Recording the shadowsocks drift result
  set_fact:
    whatif_results: "{{ whatif_results + [{
        'group': 'DRIFT',
        'status': _wi_ss_status,
        'name': 'shadowsocks-config.j2 vs live',
        'detail': _wi_ss_detail }] }}"
  vars:
    _wi_json_rendered: "{{ _wi_drift_raw.results[2].content | b64decode | from_json }}"
    _wi_json_live: "{{ _wi_drift_raw.results[3].content | b64decode | from_json }}"
    _wi_ss_status: "{{ (_wi_json_rendered == _wi_json_live) | ternary('PASS', 'FAIL') }}"
    _wi_ss_detail: "{{ (_wi_json_rendered == _wi_json_live) | ternary('identical', 'FUNCTIONAL DIFFERENCE — keys differing: ' ~ (_wi_json_rendered.keys() | select('in', _wi_json_live.keys()) | reject('eq', '') | list | join(', '))) }}"
```

JSON is compared as parsed objects, so key order and formatting cannot cause a
false FAIL.

- [ ] **Step 4: Verify, wire in, commit**

```bash
./tests/whatif/run.sh
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/whatif.yml
git add deploy/roles/whatif tests/whatif
git commit -m "Add drift detection comparing rendered templates against live files"
```

Include `drift.yml` after `service_health.yml` in `main.yml`.

---

### Task 6: Opt-in shadow sshd

**Files:**
- Create: `deploy/roles/whatif/tasks/shadow_sshd.yml`
- Modify: `deploy/roles/whatif/tasks/main.yml`

**Interfaces:**
- Consumes: `whatif_shadow_sshd` (Task 1 defaults), `_wi_tmp.path`, `_wi_user`, `whatif_results`.

- [ ] **Step 1: Write the shadow test**

Create `deploy/roles/whatif/tasks/shadow_sshd.yml`:

```yaml
# Empirical proof that the rendered config admits this user by public key.
# Starts a SECOND sshd on loopback only; never signals the running one.
- name: Shadow sshd authentication test
  when: whatif_shadow_sshd | bool
  block:
    - name: Generating a throwaway keypair
      command: "ssh-keygen -q -t ed25519 -N '' -f {{ _wi_tmp.path }}/probe_key"
      changed_when: false

    - name: Building a temporary authorized_keys for the probe
      copy:
        src: "{{ _wi_tmp.path }}/probe_key.pub"
        dest: "{{ _wi_tmp.path }}/probe_authorized_keys"
        remote_src: yes
        mode: '0600'
      changed_when: false

    - name: Starting the shadow sshd on loopback
      become: yes
      shell: >-
        /usr/sbin/sshd -f {{ _wi_tmp.path }}/10-hardening.conf
        -o ListenAddress=127.0.0.1 -o Port={{ _wi_shadow_port }}
        -o AuthorizedKeysFile={{ _wi_tmp.path }}/probe_authorized_keys
        -o PidFile={{ _wi_tmp.path }}/shadow.pid
        -o StrictModes=no
        -E {{ _wi_tmp.path }}/shadow.log
      changed_when: false

    - name: Authenticating against the shadow sshd
      command: >-
        ssh -p {{ _wi_shadow_port }} -i {{ _wi_tmp.path }}/probe_key
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
        -o BatchMode=yes -o ConnectTimeout=10
        {{ _wi_user }}@127.0.0.1 true
      register: _wi_shadow_auth
      failed_when: false
      changed_when: false

    - name: Recording the shadow-auth result
      set_fact:
        whatif_results: "{{ whatif_results + [{
            'group': 'LOCKOUT',
            'status': (_wi_shadow_auth.rc == 0) | ternary('PASS', 'FAIL'),
            'name': 'rendered config admits user by public key',
            'detail': (_wi_shadow_auth.rc == 0) | ternary('shadow sshd authenticated ' ~ _wi_user,
                       'shadow sshd REJECTED ' ~ _wi_user ~ ' (rc ' ~ _wi_shadow_auth.rc ~ ')') }] }}"

  always:
    - name: Stopping the shadow sshd
      become: yes
      shell: "kill $(cat {{ _wi_tmp.path }}/shadow.pid) 2>/dev/null || true"
      failed_when: false
      changed_when: false
  vars:
    _wi_shadow_port: 61022
```

`StrictModes=no` is required because the temp `authorized_keys` lives outside the
user's home. The shadow sshd binds loopback only and needs no firewall change.

- [ ] **Step 2: Verify, wire in, commit**

Include `shadow_sshd.yml` after `ssh_safety.yml`. Then:

```bash
./tests/whatif/run.sh
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/whatif.yml
git add deploy/roles/whatif
git commit -m "Add opt-in shadow sshd authentication test"
```

Note in your report that the shadow test cannot be exercised without a target
host, and that port 61022 is assumed free — the port check in Task 3 does not
cover it.

---

### Task 7: Make `--check` honest

**Files:**
- Modify: `deploy/roles/setup/tasks/swap.yml`
- Modify: `deploy/roles/setup/tasks/ssh.yml`

**Interfaces:** none — this task changes only the existing role's check-mode behaviour.

- [ ] **Step 1: Make the swap probe truthful**

In `swap.yml`, add `check_mode: no` to the "Checking for active swap" task. It is
a read-only `swapon --show`, so running it in check mode is safe and makes the
result real instead of an empty string that reads as "no swap".

- [ ] **Step 2: Stop the assembled validation reporting a false pass**

In `ssh.yml`, add `when: not ansible_check_mode` to the "Validating the assembled
sshd configuration" task. In check mode the template wrote nothing, so validating
would test the *old* file and report a pass that means nothing. The preflight's
Task 3 does the meaningful version against a rendered temp file.

- [ ] **Step 3: Stop the spurious rescue**

In `ssh.yml`, add `not ansible_check_mode` to the conditions on the reconnect
include and on the `set_fact: ansible_port`, `ping:` and explicit-failure tasks.
Without this, check mode reconnects to a port sshd was never reconfigured to use,
goes unreachable, and triggers the rescue path — reporting a lockout that is not
real.

- [ ] **Step 4: Prove check mode is now honest**

```bash
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
./tests/run.sh
./venv/bin/ansible-playbook --list-tasks -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
```

Then demonstrate the behaviour change with a throwaway localhost playbook:
register a `command` with and without `check_mode: no` under `--check`, and show
that the first returns real `stdout` while the second returns `""` with `rc: 0`.
Paste the real output into your report.

- [ ] **Step 5: Commit**

```bash
git add deploy/roles/setup/tasks/swap.yml deploy/roles/setup/tasks/ssh.yml
git commit -m "Make check mode honest: real probes, no false validation, no spurious rescue"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.eng.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the three commands in `README.eng.md`**

Add after the hardening section:

```markdown
### Preflight — before you harden a real host

    make whatif          # read-only checks, exits non-zero on any problem
    make whatif_deep     # also starts a shadow sshd and authenticates for real
    make whatif_preview  # the real playbook in check mode, shows a diff

`make whatif` modifies nothing and proves it: it checksums every file the
hardening playbook can write, before and after its own run, and reports
`INTEGRITY FAIL` if any changed.

It reports `UNKNOWN` for the cloud firewall. That is not a pass — there is no way
to query it from here, so open the new SSH port in your provider console and
verify it yourself.

**A green preflight is not a substitute for testing on a throwaway instance.** It
cannot exercise `fallocate`/`mkswap`, `apt install fail2ban`, or the rescue
rollback under a real failure.
```

- [ ] **Step 2: Translate the same section into `README.md`**

`README.md` is the Russian README. Keep the table, commands and code blocks
identical; translate only prose. **If you cannot produce accurate technical
Russian, insert the English section verbatim and flag it for the operator** — a
correct English warning is safer than a mistranslated one.

- [ ] **Step 3: Document the check-mode finding in `CLAUDE.md`**

Add under Architecture Notes:

```markdown
### Why `--check` alone is not enough

In ansible-core 2.11 the `command` module returns `skipped: true` **together
with** `rc: 0` and `stdout: ""` in check mode. So a bare `--check` run reports
"swap would be created" on a host that already has swap, and reports the
assembled `sshd -t` validation passing when it never executed.

`roles/setup` therefore carries three check-mode-specific guards (`check_mode: no`
on the read-only swap probe; `when: not ansible_check_mode` on the assembled
validation and on the reconnect), and `roles/whatif` exists to answer the
questions a diff cannot: will this lock me out, and is the proxy still serving.

`roles/whatif` renders the **real** templates from `roles/setup` and
`roles/server` via relative sibling paths. Never copy a template into
`roles/whatif` — a copy drifts, and a drifted preflight reports green against a
template nobody runs.
```

- [ ] **Step 4: Final verification and commit**

```bash
./tests/whatif/run.sh
./tests/run.sh
for p in setup server client whatif; do
  ./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/$p.yml
done
git status --short
git add README.md README.eng.md CLAUDE.md
git commit -m "Document the preflight and the check-mode limitation"
```

---

## What this plan cannot verify

Record these as untested when handing back:

1. **Every check in `roles/whatif` is unexercised** without a target host. Syntax and the report engine's exit contract are the limit of what CI-less verification proves.
2. **The integrity guard has never fired.** Task 2 Step 5 asks for a negative test; if it could not be run, say so.
3. **The shadow sshd has never started.** It needs root and a real sshd binary.
4. **Port 61022** is assumed free on the target.

**Run `make whatif` against the production host first — it is designed to be safe there, and Group INTEGRITY proves it after the fact. Then run the negative tests on a throwaway instance:**

```bash
# each must produce a FAIL, not a PASS
make whatif -e ssh_allow_users='[nosuchuser]'     # lockout group must FAIL
# point ssh_port at a port held by nginx           # port check must FAIL
# revert shadowsocks-config.j2 to 0.0.0.0          # drift group must FAIL
```

A preflight that has only ever returned PASS is not known to detect anything.
