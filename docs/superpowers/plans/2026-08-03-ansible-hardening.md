# Ansible Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the 2026-08-03 manual server hardening into the Ansible playbooks so it is reproducible, with defaults that are exact no-ops for existing users.

**Architecture:** Two server-role templates are corrected unconditionally (they encode latent bugs). All new host hardening — sshd, fail2ban, swap — is added to `roles/setup` behind variables that default to current behaviour. A golden-file render harness locks template output so the silent-reversion class of bug cannot recur.

**Tech Stack:** Ansible 4.6.0 / ansible-core 2.11.5, Python 3.9 venv, Jinja2 3.0.2, UFW, fail2ban, Docker Compose.

**Source spec:** `docs/superpowers/specs/2026-08-03-ansible-hardening-design.md`

## Global Constraints

- **Every new variable defaults to a no-op.** This is a published GPL repo; `make setup_server` on an unmodified checkout must produce behaviour identical to today.
- **Commit after each task, on the `harden-playbooks` branch only.** Never commit to `master` and never push. (This supersedes an earlier "never commit" constraint: the operator chose subagent-driven execution on 2026-08-03, whose review machinery diffs commit ranges. The branch is reviewed before any merge.)
- **`ssl_reject_handshake` requires nginx >= 1.19.4.** `docker-compose.yml` pins the floating `image: nginx` (currently 1.29.2). If ever pinned older, that block must be removed.
- **The sshd drop-in must keep its `10-` filename prefix.** `Include` is at line 12 of `sshd_config` and sshd takes the **first** value seen for most keywords, so the drop-in must sort before `60-cloudimg-settings.conf`.
- **All ansible commands must run through the venv** (`./venv/bin/ansible-playbook`) **with the collections path pinned** — see Task 1.
- **Do not touch** `roles/setup/tasks/packages.yml`, nginx access-log settings, or `fast_open`. Explicitly out of scope.
- Existing task-name language in this repo is English; playbook `name:` fields are Russian. Match the file you are editing.

---

### Task 1: Repair the collections-path breakage

**Why this is first:** `make setup_server` is **broken right now, before any change in this plan**. `~/.ansible/collections/` contains `community.general 13.0.1` (`requires_ansible: >=2.18.0`), which shadows the venv's bundled 3.7.0. ansible-core 2.11.5 cannot parse 13.0.1's `action_groups` metadata, so the bare-name `ufw` → `community.general.ufw` redirect fails. Nothing else in this plan can be verified until this is fixed.

> **Note:** This task is an addition beyond the spec's Testing section. It is a prerequisite, not scope creep — but flag it to the operator, who may prefer to fix their global environment instead.

**Files:**
- Create: `ansible.cfg`
- Modify: `Makefile`

**Interfaces:**
- Produces: a working `./venv/bin/ansible-playbook --syntax-check ...` for every later task's verification step.

- [ ] **Step 1: Reproduce the failure**

```bash
cd /home/mezinster/shadowsocks-vray-playbooks
./venv/bin/ansible-playbook --syntax-check \
  -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
```

Expected: `ERROR! couldn't resolve module/action 'ufw'`

- [ ] **Step 2: Create `ansible.cfg`**

> **Unverified:** the `ANSIBLE_COLLECTIONS_PATH` env-var form of this fix was confirmed working during planning; the `ansible.cfg` form was **not**. If Step 3 fails, delete `ansible.cfg` and rely on Step 4's Makefile export, which uses the verified mechanism. Note also that ansible-core 2.11 accepts `collections_paths` (plural); if it is ignored, try `collections_path`.

```ini
# Pin collections to the ones bundled in this repo's venv.
#
# Without this, a user-level install in ~/.ansible/collections (e.g.
# community.general 13.0.1, which requires ansible-core >= 2.18) shadows the
# venv's bundled 3.7.0. ansible-core 2.11.5 cannot parse the newer metadata,
# and the bare-name `ufw` -> `community.general.ufw` redirect silently fails
# with "couldn't resolve module/action 'ufw'".
#
# Path tracks the venv's Python version. If the venv is rebuilt on a different
# Python, update it to match: venv/lib/pythonX.Y/site-packages
[defaults]
collections_paths = ./venv/lib/python3.9/site-packages
```

- [ ] **Step 3: Verify the fix**

```bash
./venv/bin/ansible-playbook --syntax-check \
  -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
./venv/bin/ansible-galaxy collection list community.general 2>&1 | grep community.general
```

Expected: `playbook: deploy/setup.yml` with no ERROR, and `community.general 3.7.0`.

If `ansible.cfg` is ignored (ansible only reads it from the current working directory), fall back to exporting the variable in the Makefile instead — Step 4 does this anyway as a belt-and-braces measure.

- [ ] **Step 4: Make the Makefile self-locating**

Add at the top of `Makefile`, above the first target. This survives a venv rebuilt on a different Python version, unlike the hardcoded path in `ansible.cfg`.

```make
# Pin collections to the venv's bundled tree (see ansible.cfg for why).
# Computed rather than hardcoded so a rebuilt venv on another Python still works.
export ANSIBLE_COLLECTIONS_PATH := $(shell ./venv/bin/python -c "import ansible_collections,os;print(os.path.dirname(ansible_collections.__path__[0]))")
```

- [ ] **Step 5: Verify both playbooks and the Makefile path**

```bash
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/server.yml
make -n setup_server
```

Expected: both print `playbook: ...` with no ERROR; `make -n` prints the command without error.

---

### Task 2: Golden-file render harness

**Why:** The drift that motivated this work reverts **silently** — `template:` rewrites files unconditionally and reports success. A golden-file test turns silent reversion into a loud diff. These are characterization tests: they lock in *current* output first, so Task 3's change shows up as an intentional, reviewable diff.

**Files:**
- Create: `tests/render/render.yml`
- Create: `tests/render/targets.yml`
- Create: `tests/render/vars-defaults.yml`
- Create: `tests/render/vars-hardened.yml`
- Create: `tests/run.sh`
- Create: `tests/render/expected/{defaults,hardened}/` (generated in Step 4)
- Modify: `.gitignore`

**Interfaces:**
- Produces: `./tests/run.sh` — exit 0 = all rendered templates match goldens. Every later task calls it.
- Produces: `tests/render/targets.yml` holding `render_targets`, a list of `{src, name}`. Later tasks append entries.

- [ ] **Step 1: Write the render playbook**

Create `tests/render/render.yml`:

```yaml
# Renders role templates to a temp dir so they can be diffed against goldens.
# Run via tests/run.sh, not directly.
- name: Render role templates for golden-file comparison
  hosts: localhost
  connection: local
  gather_facts: no
  vars_files:
    - targets.yml
    - "{{ varset }}"
  tasks:
    - name: Ensure output directory exists
      file:
        path: "{{ outdir }}"
        state: directory
        mode: '0755'

    - name: Render each template
      template:
        src: "{{ item.src }}"
        dest: "{{ outdir }}/{{ item.name }}"
        mode: '0644'
      loop: "{{ render_targets }}"
      loop_control:
        label: "{{ item.name }}"
```

- [ ] **Step 2: Write the target list and both variable sets**

Create `tests/render/targets.yml`. Paths are relative to `tests/render/`:

```yaml
render_targets:
  - { src: "../../deploy/roles/server/templates/shadowsocks-config.j2", name: "shadowsocks-config.json" }
  - { src: "../../deploy/roles/server/templates/nginx-release.j2",      name: "nginx-release.conf" }
```

Create `tests/render/vars-defaults.yml` — mirrors `variables.example.yml` exactly:

```yaml
user: server_user
host: yourserver.domain.net
email: youremail@provider.com
proxy_password: yourpassword
method: chacha20-ietf-poly1305
local_port: 8000
fast_open: true
endpoint: ss
enable_firewall: yes
```

Create `tests/render/vars-hardened.yml` — production-shaped, **no real secrets**:

```yaml
user: ubuntu
host: proxy.example.test
email: admin@example.test
proxy_password: DUMMY_NOT_A_REAL_PASSWORD
method: chacha20-ietf-poly1305
local_port: 8000
fast_open: true
endpoint: ss
enable_firewall: yes
```

- [ ] **Step 3: Write the runner**

Create `tests/run.sh`, then `chmod +x tests/run.sh`:

```bash
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
```

- [ ] **Step 4: Generate the goldens from the CURRENT templates, then inspect them**

```bash
./tests/run.sh --update
cat tests/render/expected/defaults/shadowsocks-config.json
cat tests/render/expected/defaults/nginx-release.conf
```

Expected: `shadowsocks-config.json` still contains `"server":"0.0.0.0"` and `nginx-release.conf` has **no** `ssl_reject_handshake`. **This is correct at this stage** — these goldens capture the pre-fix state so Task 3 produces a visible diff. Read both files to confirm they are complete and correctly substituted (`yourserver.domain.net` appears, no raw `{{ }}` remains).

- [ ] **Step 5: Verify the harness passes**

```bash
./tests/run.sh
```

Expected: `PASS: defaults` and `PASS: hardened`, exit 0.

- [ ] **Step 6: Ignore the render output directory**

Append to `.gitignore`:

```
tests/render/out
```

- [ ] **Step 7: Prove the harness actually catches drift**

A test that has never failed is not known to work.

```bash
sed -i 's/"server":"0.0.0.0"/"server":"CANARY"/' deploy/roles/server/templates/shadowsocks-config.j2
./tests/run.sh; echo "exit=$?"
sed -i 's/"server":"CANARY"/"server":"0.0.0.0"/' deploy/roles/server/templates/shadowsocks-config.j2
./tests/run.sh; echo "exit=$?"
```

Expected: first run prints `FAIL` with a diff and `exit=1`; second prints `PASS` and `exit=0`.

---

### Task 3: Fix the two server templates

**Why:** These are the silent-reversion drift points. Both are unconditional (no variable) — see spec §3 for rationale.

**Files:**
- Modify: `deploy/roles/server/templates/shadowsocks-config.j2:2`
- Modify: `deploy/roles/server/templates/nginx-release.j2:1`
- Modify: `tests/render/expected/{defaults,hardened}/*` (regenerated)

**Interfaces:**
- Consumes: `./tests/run.sh` from Task 2.

- [ ] **Step 1: Update the goldens first, so the diff is reviewed before the change**

Edit `tests/render/expected/defaults/shadowsocks-config.json` and `tests/render/expected/hardened/shadowsocks-config.json`, changing `"server":"0.0.0.0"` to `"server":"127.0.0.1"`.

Prepend to both `nginx-release.conf` goldens (before the existing first `server {`):

```nginx
server {
  listen 443 ssl default_server;
  ssl_reject_handshake on;
}

```

- [ ] **Step 2: Run the tests to verify they now FAIL**

```bash
./tests/run.sh; echo "exit=$?"
```

Expected: `FAIL` for both sets, `exit=1`, with diffs showing exactly the two intended changes and nothing else.

- [ ] **Step 3: Fix `shadowsocks-config.j2`**

Change line 2 from `"server":"0.0.0.0",` to:

```jinja
    "server":"127.0.0.1",
```

- [ ] **Step 4: Fix `nginx-release.j2`**

Prepend to the very top of the file:

```jinja
# Reject TLS handshakes that do not present our SNI.  Probing the bare IP
# returns an unrecognized_name alert instead of the certificate, so the domain
# is not discoverable by address scanning.  Requires nginx >= 1.19.4.
server {
  listen 443 ssl default_server;
  ssl_reject_handshake on;
}

```

**Critical:** the existing `listen 443 ssl;` block must **not** also carry `default_server` — nginx permits exactly one default server per port and will refuse to start otherwise. Leave that block's `listen` line unchanged.

- [ ] **Step 5: Run the tests to verify they PASS**

```bash
./tests/run.sh; echo "exit=$?"
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/server.yml
```

Expected: `PASS: defaults`, `PASS: hardened`, `exit=0`, and a clean syntax-check.

- [ ] **Step 6: Confirm the ACME interaction is preserved**

Read the rendered `tests/render/expected/defaults/nginx-release.conf` and confirm all three still hold:
1. The `:80` server still ends with `return 301 https://$host$request_uri;`
2. The named `:443` server still contains `location ^~/.well-known`
3. Only **one** `default_server` appears on `listen 443`

Certbot renews via `--webroot`; Let's Encrypt follows the `:80` redirect into that `^~/.well-known` location. Breaking any of the three breaks renewal.

---

### Task 4: Add hardening variables

**Files:**
- Modify: `variables.example.yml`
- Modify: `inventories/hosts.example.ini`
- Modify: `tests/render/vars-defaults.yml`, `tests/render/vars-hardened.yml`

**Interfaces:**
- Produces: `harden_ssh`, `ssh_port`, `ssh_keep_port_22`, `ssh_allow_users`, `ssh_accept_lockout_risk`, `install_fail2ban`, `fail2ban_maxretry`, `fail2ban_findtime`, `fail2ban_bantime`, `swap_size_mb` — consumed by Tasks 5–8.

- [ ] **Step 1: Append the variables to `variables.example.yml`**

```yaml

# --- Host hardening (every default below is a no-op) ---------------------
# Master switch for the sshd drop-in.  Leave `no` unless you have read the
# lockout notes in README.md.
harden_ssh: no
ssh_port: 22
# Also bind port 22 when ssh_port differs.  Provider web consoles (AWS
# Lightsail and others) can often only reach 22 and cannot be retargeted, so
# leaving it bound turns break-glass into one toggle in the provider UI.
# Block 22 at the CLOUD firewall, not on the host.
ssh_keep_port_22: yes
# Empty list emits no AllowUsers directive.  Example: [ubuntu]
ssh_allow_users: []
# Permits harden_ssh with ssh_keep_port_22=no, which removes the rollback path.
ssh_accept_lockout_risk: no

install_fail2ban: no
fail2ban_maxretry: 3
fail2ban_findtime: 1h
fail2ban_bantime: 24h

# 0 disables swap handling entirely.  2048 = 2GB.
swap_size_mb: 0
```

- [ ] **Step 2: Document the `ansible_port` requirement in `hosts.example.ini`**

Replace the file contents with:

```ini
[local]
client  ansible_connection=local

[remote]
# If you set ssh_port in variables.yml, you MUST also set ansible_port here to
# match, or every make target will fail to connect once the old port is closed
# at your cloud firewall.
#   server ansible_host=yourpersonalserver.com ansible_port=<ssh-port>
server ansible_host=yourpersonalserver.com
```

- [ ] **Step 3: Mirror the defaults into the test var sets**

Append the exact block from Step 1 to `tests/render/vars-defaults.yml`.

Append to `tests/render/vars-hardened.yml` (the hardened counterpart):

```yaml
harden_ssh: yes
ssh_port: <ssh-port>
ssh_keep_port_22: yes
ssh_allow_users: [ubuntu]
ssh_accept_lockout_risk: no
install_fail2ban: yes
fail2ban_maxretry: 3
fail2ban_findtime: 1h
fail2ban_bantime: 24h
swap_size_mb: 2048
```

- [ ] **Step 4: Verify nothing rendered changed**

```bash
./tests/run.sh; echo "exit=$?"
```

Expected: `PASS` for both, `exit=0`. Adding unused variables must not alter any rendered template.

---

### Task 5: Variable-driven UFW ports

**Files:**
- Modify: `deploy/roles/setup/tasks/network.yml:1-13`

**Interfaces:**
- Consumes: `ssh_port`, `ssh_keep_port_22` from Task 4.

- [ ] **Step 1: Replace the hardcoded port loop**

Replace the first two tasks of `network.yml` (the `Opening firewall ports` loop and `Setting firewall`) with:

```yaml
- name: Computing firewall ports
  set_fact:
    _ufw_ports: "{{ ([ssh_port] + ([22] if ssh_keep_port_22 else []) + [80, 443]) | unique }}"

- name: Opening firewall ports
  become: yes
  ufw:
    rule: allow
    port: "{{ item }}"
  loop: "{{ _ufw_ports }}"

- name: Setting firewall
  become: yes
  ufw:
    state: enabled
```

Leave the `disable-iptables` tasks and the trailing `meta: reset_connection` untouched.

- [ ] **Step 2: Verify the default port set is unchanged**

```bash
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml

./venv/bin/ansible localhost -m debug \
  -a "msg={{ ([ssh_port] + ([22] if ssh_keep_port_22 else []) + [80, 443]) | unique }}" \
  -e "@variables.example.yml"
```

Expected: clean syntax-check, and the debug prints `['22', 80, 443]` or `[22, 80, 443]` — the same three ports the old hardcoded loop opened.

- [ ] **Step 3: Verify the hardened port set**

```bash
./venv/bin/ansible localhost -m debug \
  -a "msg={{ ([ssh_port] + ([22] if ssh_keep_port_22 else []) + [80, 443]) | unique }}" \
  -e "@tests/render/vars-hardened.yml"
```

Expected: `[<ssh-port>, 22, 80, 443]`.

---

### Task 6: sshd hardening

> ## ⚠️ THE `ssh.yml` BELOW IS DEFECTIVE — DO NOT COPY IT
>
> Review during execution (2026-08-03) found **two Critical** and three Important
> defects in the code this task specifies. The implemented version on the
> `harden-playbooks` branch deviates deliberately; the operator ruled that the
> review governs. Read `deploy/roles/setup/tasks/ssh.yml` as built, not the
> listing below, and see `.superpowers/sdd/2026-08-03-ansible-hardening/progress.md`
> for the full findings. Summary of what was wrong:
>
> - **Critical 1 — the `rescue` block can never fire on the failure it exists for.**
>   An SSH auth failure makes the host UNREACHABLE, not FAILED. In ansible-core
>   2.11 (`strategy/__init__.py:588-591`) unreachable hosts never reach
>   `mark_host_failed`, so `rescue` is skipped. The rollback was inert exactly
>   when needed. Needs `ignore_unreachable` plus an explicit failure task.
> - **Critical 2 — on a re-run where `validate` fails, the rescue deletes the
>   working drop-in.** `template` never writes dest on validate failure, so
>   `backup_file` is undefined, which routes into the "no previous version
>   existed, delete it" branch. sshd then falls back to port 22 only —
>   a permanent lockout for anyone who blocked 22 at their cloud firewall, which
>   is what this very plan recommends. Needs a guard on `_sshd_drop_in is not failed`.
> - **Important 3** — the rescue's hardcoded port 22 is wrong when the failure
>   happened before the reload; sshd is still on the operator's previous port.
> - **Important 4** — `meta: flush_handlers` and `meta: reset_connection` ignore
>   an enclosing block's `when:` in 2.11, so both run even when `harden_ssh: no`.
> - **Important 5** — no pre-flight guard for password-based or root operators.
> - **Minor 6** — `ssh_port != 22` is type-sensitive; a string `"22"` emits
>   `Port 22` twice.
> - **Minor 7** — no `roles/setup/defaults/main.yml`, so an existing user with an
>   old `variables.yml` gets `'harden_ssh' is undefined` and `make setup_server`
>   breaks — violating this plan's own no-op constraint.
>
> **Lesson for future plans:** the failure modes that only appear after something
> else has gone wrong are invisible to a successful run. They were found by
> *executing* a throwaway block/rescue and observing `rescued=0`, not by reading.

**Files:**
- Create: `deploy/roles/setup/templates/10-hardening.conf.j2`
- Create: `deploy/roles/setup/tasks/ssh.yml`
- Create: `deploy/roles/setup/handlers/main.yml`
- Modify: `deploy/roles/setup/tasks/main.yml`
- Modify: `tests/render/targets.yml`

**Interfaces:**
- Consumes: `harden_ssh`, `ssh_port`, `ssh_keep_port_22`, `ssh_allow_users`, `ssh_accept_lockout_risk`.
- Produces: handler named `reload sshd`, reused by no other task.

- [ ] **Step 1: Add the template to the render targets**

Append to `render_targets` in `tests/render/targets.yml`:

```yaml
  - { src: "../../deploy/roles/setup/templates/10-hardening.conf.j2", name: "10-hardening.conf" }
```

- [ ] **Step 2: Write the template**

Create `deploy/roles/setup/templates/10-hardening.conf.j2`:

```jinja
# Managed by Ansible (roles/setup/tasks/ssh.yml).  Manual edits are overwritten.
#
# The 10- prefix is REQUIRED.  Include sits at line 12 of sshd_config and sshd
# takes the FIRST value seen for most keywords, so this file must sort before
# 60-cloudimg-settings.conf to win.

Port {{ ssh_port }}
{% if ssh_keep_port_22 and ssh_port != 22 %}
# Break-glass: provider web consoles (AWS Lightsail and others) often reach
# only port 22 and cannot be retargeted.  Left BOUND here and blocked at the
# CLOUD firewall, so recovery is one toggle in the provider UI rather than a
# snapshot-and-disk-swap.
Port 22
{% endif %}

PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
AuthenticationMethods publickey
{% if ssh_allow_users %}
AllowUsers {{ ssh_allow_users | join(' ') }}
{% endif %}

MaxAuthTries 3
MaxSessions 4
LoginGraceTime 20
ClientAliveInterval 300
ClientAliveCountMax 2

X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding no
PermitTunnel no
PermitUserEnvironment no

# VERBOSE logs the key fingerprint used for each login -> forensics.
LogLevel VERBOSE
```

Crypto settings (`Ciphers`, `MACs`, `KexAlgorithms`, `PubkeyAcceptedAlgorithms`) are deliberately omitted: Ubuntu 22.04 defaults already exclude weak algorithms, and narrowing them risks rejecting an operator's RSA key.

- [ ] **Step 3: Render and inspect both variants**

```bash
./tests/run.sh --update
echo "--- defaults (ssh_port 22, must NOT emit a second Port line) ---"
cat tests/render/expected/defaults/10-hardening.conf
echo "--- hardened (must emit Port <ssh-port> AND Port 22, plus AllowUsers ubuntu) ---"
cat tests/render/expected/hardened/10-hardening.conf
```

Expected — defaults: a single `Port 22`, and **no** `AllowUsers` line (empty list). Hardened: `Port <ssh-port>` followed by `Port 22`, and `AllowUsers ubuntu`. Confirm no raw `{{ }}` or `{% %}` survives in either.

- [ ] **Step 4: Write the handlers**

Create `deploy/roles/setup/handlers/main.yml`:

```yaml
- name: reload sshd
  become: yes
  service:
    name: ssh
    state: reloaded

- name: restart fail2ban
  become: yes
  service:
    name: fail2ban
    state: restarted

- name: apply sysctl
  become: yes
  command: sysctl --system
```

- [ ] **Step 5: Write `ssh.yml`**

Create `deploy/roles/setup/tasks/ssh.yml`:

```yaml
- name: Refusing an unrecoverable SSH configuration
  assert:
    that:
      - (not harden_ssh | bool)
        or (ssh_keep_port_22 | bool)
        or (ssh_accept_lockout_risk | default(false) | bool)
    fail_msg: >-
      harden_ssh with ssh_keep_port_22=no removes the rollback path: the rescue
      block below would have no route back to the host. Set
      ssh_accept_lockout_risk=yes to override deliberately.

- name: Hardening sshd
  when: harden_ssh | bool
  block:
    - name: Deploying sshd hardening drop-in
      become: yes
      template:
        src: 10-hardening.conf.j2
        dest: /etc/ssh/sshd_config.d/10-hardening.conf
        owner: root
        group: root
        mode: '0644'
        backup: yes
        # Validates this fragment's syntax in isolation, using built-in
        # defaults for anything unspecified.  Does not catch conflicts with
        # the main config -- the next task does that.
        validate: '/usr/sbin/sshd -t -f %s'
      register: _sshd_drop_in
      notify: reload sshd

    - name: Validating the assembled sshd configuration
      become: yes
      command: /usr/sbin/sshd -t
      changed_when: false

    - name: Reloading sshd before verifying it
      meta: flush_handlers

    - name: Dropping the old connection
      meta: reset_connection

    - name: Switching to the new SSH port
      set_fact:
        ansible_port: "{{ ssh_port }}"

    # A real authentication over the new port.  wait_for would only prove a
    # TCP socket is open -- an sshd that accepts connections and rejects every
    # key would pass it.  This re-authenticates through Ansible's own SSH
    # transport, so a semantic lockout (wrong AllowUsers, AuthenticationMethods
    # excluding the key) fails the play instead of passing it.
    - name: Verifying authentication over the new port
      ping:

  rescue:
    # Hardcoded 22, not a variable: the assert above guarantees 22 is bound
    # whenever this path is reachable without an explicit override.
    - name: Falling back to port 22
      set_fact:
        ansible_port: 22

    - name: Dropping the failed connection
      meta: reset_connection

    - name: Restoring the previous sshd drop-in
      become: yes
      copy:
        src: "{{ _sshd_drop_in.backup_file }}"
        dest: /etc/ssh/sshd_config.d/10-hardening.conf
        remote_src: yes
      when:
        - _sshd_drop_in is defined
        - _sshd_drop_in.backup_file is defined

    # First run: no prior version existed, so there is nothing to restore and
    # the broken file must simply go.
    - name: Removing the failed drop-in when no previous version existed
      become: yes
      file:
        path: /etc/ssh/sshd_config.d/10-hardening.conf
        state: absent
      when:
        - _sshd_drop_in is defined
        - _sshd_drop_in.backup_file is not defined

    - name: Reloading sshd with the restored configuration
      become: yes
      service:
        name: ssh
        state: reloaded

    - name: Failing loudly
      fail:
        msg: >-
          SSH hardening failed verification and was rolled back. The host is
          reachable on port 22. Check AllowUsers and AuthenticationMethods in
          roles/setup/templates/10-hardening.conf.j2 before retrying.
```

- [ ] **Step 6: Wire it into the role**

Append to `deploy/roles/setup/tasks/main.yml`:

```yaml
- name: Hardening ssh
  import_tasks: ssh.yml
```

- [ ] **Step 7: Verify**

```bash
./tests/run.sh; echo "exit=$?"
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
./venv/bin/ansible-playbook --list-tasks -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml | grep -i -A2 "ssh"
```

Expected: tests pass, syntax-check clean, and `list-tasks` shows the assert plus the hardening block.

- [ ] **Step 8: Verify the assert actually fires**

```bash
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini \
  -e "@variables.example.yml" -e "harden_ssh=yes" -e "ssh_keep_port_22=no" deploy/setup.yml
```

Expected: syntax-check still passes (asserts are runtime, not syntax). The assert's real behaviour can only be confirmed on a live host — record this as untested and cover it in Task 9's manual checklist.

---

### Task 7: fail2ban

**Files:**
- Create: `deploy/roles/setup/templates/jail.local.j2`
- Create: `deploy/roles/setup/tasks/fail2ban.yml`
- Modify: `deploy/roles/setup/tasks/main.yml`
- Modify: `tests/render/targets.yml`

**Interfaces:**
- Consumes: `install_fail2ban`, `fail2ban_maxretry`, `fail2ban_findtime`, `fail2ban_bantime`, `ssh_port`, `ssh_keep_port_22`.
- Consumes: handler `restart fail2ban` from Task 6 Step 4.

- [ ] **Step 1: Add to render targets**

Append to `render_targets` in `tests/render/targets.yml`:

```yaml
  - { src: "../../deploy/roles/setup/templates/jail.local.j2", name: "jail.local" }
```

- [ ] **Step 2: Write the template**

Create `deploy/roles/setup/templates/jail.local.j2`:

```jinja
# Managed by Ansible (roles/setup/tasks/fail2ban.yml).  Manual edits are overwritten.
[DEFAULT]
bantime  = {{ fail2ban_bantime }}
findtime = {{ fail2ban_findtime }}
maxretry = {{ fail2ban_maxretry }}
ignoreip = 127.0.0.1/8 ::1
# ufw rather than the default iptables-multiport, so bans appear in
# `ufw status` and survive a `ufw reload`.  Note the ufw action bans the
# source address on ALL ports, not just SSH.
banaction          = ufw
banaction_allports = ufw

[sshd]
enabled  = true
port     = {{ ssh_port }}{% if ssh_keep_port_22 and ssh_port != 22 %},22{% endif %}

# backend/logpath pinned explicitly: persistent journald storage is not
# configured on the Ubuntu cloud image, whereas /var/log/auth.log is populated.
backend  = auto
logpath  = /var/log/auth.log
maxretry = {{ fail2ban_maxretry }}

# Second-order jail: catches hosts that return after their first ban expires.
# Values are intentionally not parameterised -- they only make sense relative
# to [sshd], and four more variables to tune them would be YAGNI.
[recidive]
enabled   = true
logpath   = /var/log/fail2ban.log
banaction = ufw
bantime   = 4w
findtime  = 1d
maxretry  = 3
```

- [ ] **Step 3: Render and inspect**

```bash
./tests/run.sh --update
grep -A2 '^\[sshd\]' tests/render/expected/defaults/jail.local
grep -A2 '^\[sshd\]' tests/render/expected/hardened/jail.local
```

Expected — defaults: `port     = 22` (no comma, because `ssh_port == 22`). Hardened: `port     = <ssh-port>,22`.

- [ ] **Step 4: Write the tasks**

Create `deploy/roles/setup/tasks/fail2ban.yml`:

```yaml
- name: Installing fail2ban
  when: install_fail2ban | bool
  block:
    - name: Installing the fail2ban package
      become: yes
      apt:
        name: fail2ban
        state: present
        update_cache: yes
        cache_valid_time: 86400

    - name: Configuring fail2ban jails
      become: yes
      template:
        src: jail.local.j2
        dest: /etc/fail2ban/jail.local
        owner: root
        group: root
        mode: '0644'
      notify: restart fail2ban

    - name: Enabling fail2ban
      become: yes
      service:
        name: fail2ban
        enabled: yes
        state: started
```

- [ ] **Step 5: Wire it in**

Append to `deploy/roles/setup/tasks/main.yml`:

```yaml
- name: Installing fail2ban
  import_tasks: fail2ban.yml
```

- [ ] **Step 6: Verify**

```bash
./tests/run.sh; echo "exit=$?"
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
```

Expected: `PASS` both sets, `exit=0`, clean syntax-check.

---

### Task 8: Swap

**Files:**
- Create: `deploy/roles/setup/tasks/swap.yml`
- Modify: `deploy/roles/setup/tasks/main.yml`

**Interfaces:**
- Consumes: `swap_size_mb` from Task 4; handler `apply sysctl` from Task 6 Step 4.

- [ ] **Step 1: Write the tasks**

Create `deploy/roles/setup/tasks/swap.yml`:

```yaml
- name: Configuring swap
  when: swap_size_mb | int > 0
  block:
    - name: Checking for active swap
      become: yes
      command: swapon --show --noheadings
      register: _swap_show
      changed_when: false
      failed_when: false

    - name: Creating the swap file
      when: _swap_show.stdout | trim == ""
      block:
        - name: Allocating the swap file
          become: yes
          command: "fallocate -l {{ swap_size_mb }}M /swapfile"
          args:
            creates: /swapfile

        - name: Restricting swap file permissions
          become: yes
          file:
            path: /swapfile
            owner: root
            group: root
            mode: '0600'

        - name: Formatting the swap file
          become: yes
          command: mkswap /swapfile

        - name: Enabling swap
          become: yes
          command: swapon /swapfile

    - name: Persisting swap across reboots
      become: yes
      lineinfile:
        path: /etc/fstab
        line: "/swapfile none swap sw 0 0"
        state: present

    - name: Lowering swappiness for a small proxy host
      become: yes
      copy:
        dest: /etc/sysctl.d/99-swappiness.conf
        content: "vm.swappiness=10\n"
        owner: root
        group: root
        mode: '0644'
      notify: apply sysctl
```

Idempotency: `creates: /swapfile` makes allocation a no-op on re-run, and the `_swap_show.stdout` guard skips the whole create block whenever swap is already active. `lineinfile` and `copy` are idempotent by nature.

- [ ] **Step 2: Wire it in**

Append to `deploy/roles/setup/tasks/main.yml`:

```yaml
- name: Configuring swap
  import_tasks: swap.yml
```

- [ ] **Step 3: Verify**

```bash
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
./venv/bin/ansible-playbook --list-tasks -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
```

Expected: clean syntax-check; `list-tasks` lists packages, docker, network, ssh, fail2ban and swap tasks in that order.

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`
- Modify: `README.eng.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a hardening section to `README.eng.md`**

Insert after the existing configuration section:

```markdown
## Host hardening (optional)

All hardening defaults to **off**. An unmodified checkout behaves exactly as
before.

| Variable | Default | Purpose |
|---|---|---|
| `harden_ssh` | `no` | Deploy the sshd hardening drop-in |
| `ssh_port` | `22` | Port sshd listens on |
| `ssh_keep_port_22` | `yes` | Also bind 22 for provider-console break-glass |
| `ssh_allow_users` | `[]` | `AllowUsers` list; empty emits no directive |
| `ssh_accept_lockout_risk` | `no` | Permit the unrecoverable combination |
| `install_fail2ban` | `no` | Install and configure fail2ban |
| `fail2ban_maxretry` | `3` | Failures before a ban |
| `fail2ban_findtime` | `1h` | Window those failures are counted in |
| `fail2ban_bantime` | `24h` | Ban duration |
| `swap_size_mb` | `0` | Swap file size; `0` disables |

### Before you set `ssh_port`

**Add a matching `ansible_port` to `inventories/hosts.ini`:**

    server ansible_host=yourserver.com ansible_port=<ssh-port>

Without it, every `make` target stops connecting the moment the old port is
closed at your cloud firewall.

**Open the new port at your cloud firewall before running the playbook**, and
close the old one only after you have confirmed a login on the new port.

### Why port 22 stays bound

With `ssh_keep_port_22: yes` (the default) sshd listens on both the new port
and 22. Blocking 22 is your **cloud firewall's** job, not the host's. Provider
web consoles — AWS Lightsail's browser SSH among them — can often only reach
port 22 and cannot be retargeted. Leaving it bound means recovering from a
lockout is one toggle in the provider UI instead of a snapshot-and-disk-swap.

Setting `ssh_keep_port_22: no` removes the playbook's own rollback path, so it
is refused unless you also set `ssh_accept_lockout_risk: yes`.

### fail2ban bans are not SSH-only

The `ufw` ban action blocks the offending address on **all ports**, including
443. A host that fails SSH authentication will also lose the proxy. Unban with:

    sudo fail2ban-client set sshd unbanip <IP>
```

- [ ] **Step 2: Add the same section to `README.md`**

`README.md` is the Russian-language README. Add the same section there with an
identical table, identical variable names, and identical code blocks — only the
prose translated.

**If you cannot produce accurate technical Russian, insert the English section
from Step 1 verbatim and flag it for the operator to translate.** A correct
English warning about SSH lockout is safer than a mistranslated one. Do not
paraphrase or shorten the three warnings (`ansible_port`, cloud-firewall
ordering, all-ports fail2ban bans) in either language — they are the parts that
prevent losing access to the server.

- [ ] **Step 3: Update `CLAUDE.md`**

Add under "Architecture Notes":

```markdown
### SSH hardening and the dual-port design

`roles/setup/tasks/ssh.yml` deploys `/etc/ssh/sshd_config.d/10-hardening.conf`.
The `10-` prefix is required: `Include` sits at line 12 of `sshd_config` and
sshd takes the **first** value seen for most keywords, so the drop-in must sort
before `60-cloudimg-settings.conf`.

When `ssh_port` differs from 22 and `ssh_keep_port_22` is true, sshd binds
**both**. Blocking 22 is delegated to the cloud firewall so that provider web
consoles — which typically reach only port 22 — remain a break-glass path.

The role verifies its own change by resetting the connection, repointing
`ansible_port`, and running `ping`. This re-authenticates through Ansible's own
SSH transport, so a semantic lockout fails the play; `wait_for` would only
prove a TCP socket was open. On failure the `rescue` block restores the backup
over port 22.
```

Add under "Common Commands":

```markdown
### Template regression tests

    ./tests/run.sh              # render all templates, diff against goldens
    ./tests/run.sh --update     # regenerate goldens after an intended change

Server-role templates are rewritten wholesale on every `make deploy_server`, so
a hand-edit on the server is reverted silently. These goldens turn that into a
visible diff.
```

- [ ] **Step 4: Final verification**

```bash
./tests/run.sh; echo "exit=$?"
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/setup.yml
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/server.yml
./venv/bin/ansible-playbook --syntax-check -i inventories/hosts.example.ini -e "@variables.example.yml" deploy/client.yml
git status --short
```

Expected: tests pass, all three playbooks syntax-check clean, and `git status` shows only intended files. Commit to the `harden-playbooks` branch; do not push.

---

## What this plan cannot verify

Record these as untested when handing back:

1. **The `rescue` rollback path never executes.** `--check` does not exercise it and it cannot be safely triggered against the production host, whose access it manages.
2. **The `assert` guard** is runtime-only; syntax-check does not fire it.
3. **fail2ban, swap and UFW tasks are never actually run** — only syntax-checked and, for templates, render-diffed.

**Validate on a throwaway Lightsail instance before pointing this at production.** Manual checklist for that instance:

```bash
# after: make setup_server with harden_ssh=yes, ssh_port=<ssh-port>
ssh -p <ssh-port> <host> 'sudo sshd -T | grep -E "^(port|allowusers|maxauthtries|permitrootlogin)"'
ssh -p <ssh-port> <host> 'sudo fail2ban-client status sshd'
ssh -p <ssh-port> <host> 'swapon --show'
ssh -p <ssh-port> <host> 'sudo ufw status | grep -E "^(22|<ssh-port>|80|443)"'

# force the rescue path: set ssh_allow_users to a nonexistent user and re-run.
# Expect the play to FAIL with the rollback message and the host to stay
# reachable on port 22.
```
