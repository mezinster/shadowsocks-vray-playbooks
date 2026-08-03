# `--whatif` preflight mode — design

**Date:** 2026-08-03
**Status:** approved, pending implementation plan

## Problem

The hardening role can lock an operator out of their own server. Nothing in it has
been executed against a real host, and the reviews that found three Critical
defects found all of them in error-handling and re-run paths — the paths that only
execute after something has already gone wrong.

The obvious safety net, `ansible-playbook --check`, does not work here. It does not
crash; **it reports false results in both directions.**

Measured on ansible-core 2.11.5: in check mode the `command` module returns

```json
{"msg": "Command would have run if not in check mode",
 "rc": 0, "stdout": "", "skipped": true}
```

`skipped: true`, but also `rc: 0` and `stdout: ""`. Consequences:

| Task | `--check` reports | Reality |
|---|---|---|
| `swapon --show` probe in `swap.yml` | `stdout: ""` → "swap would be created" | **False** on a host that already has swap |
| assembled `sshd -t` in `ssh.yml` | `rc: 0` → validation passed | **Never ran.** A broken config reports clean |
| `ping` after `set_fact: ansible_port` | attempts the *new* port | sshd was never reconfigured → unreachable → **spurious rescue** |

Verified separately, and relied on below: `stat` **does** execute correctly in check
mode, and `template` reports `changed: true` while writing nothing and registering
no `backup_file`.

So `--check` gives false reassurance about the sshd validation and a false lockout
alarm at the same time. A purpose-built mode is justified.

## Goals

1. Prove the operator will not be locked out of SSH.
2. Prove the proxy keeps serving traffic.
3. Show every file that would change.
4. Guarantee — not assert — that the preflight itself modifies nothing.
5. Exit non-zero on any failure, so it can gate a deploy.

## Non-goals

- Replacing a throwaway-instance test. This raises confidence; it does not make
  the untested paths tested.
- Checking the cloud firewall. Impossible here — see §7.
- Any change to `roles/server/` templates or to the proxy itself.

## Approach

Two halves, because neither alone meets all five goals:

- **An honest `--check`** supplies goal 3 with zero drift, because it *is* the real
  playbook.
- **A read-only preflight role** supplies goals 1, 2, 4 and 5, which a diff cannot
  express.

## Design

### 1. Layout

```
deploy/whatif.yml                    orchestrator; hosts: remote; changes nothing
deploy/roles/whatif/tasks/
  main.yml                           phases + cleanup in always:
  connection.yml                     identify the live connection
  ssh_safety.yml                     the lockout proof
  service_health.yml                 proxy alive right now
  drift.yml                          what deploy_server would overwrite
  shadow_sshd.yml                    opt-in empirical auth test
  report.yml                         PASS/FAIL table, fail: on any FAIL
```

`deploy/roles/whatif/templates/` **must not exist.** See §2.

```make
whatif:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/whatif.yml

whatif_deep:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/whatif.yml -e whatif_shadow_sshd=yes

# The other half: the real setup playbook in honest check mode (see Group D).
# Separate target because it runs a DIFFERENT playbook and produces a diff,
# not the preflight report.
whatif_preview:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/setup.yml --check --diff
```

**These are two distinct commands with two distinct outputs.** `make whatif`
produces the PASS/FAIL report in §6. `make whatif_preview` produces an Ansible
diff. Group D exists only to make the second one truthful; it contributes nothing
to the report.

### 2. Anti-drift: reuse the real templates

The role renders the **same template files the real playbook uses**, into a temp
directory:

- `../../setup/templates/10-hardening.conf.j2`
- `../../setup/templates/jail.local.j2`
- `../../server/templates/nginx-release.j2`
- `../../server/templates/shadowsocks-config.j2`

A preflight that *describes* what the playbook would do is free to diverge from it,
and a stale preflight's green result is the most dangerous output in the system.
Rendering the same template objects makes divergence structurally impossible; only
the assertions are implemented independently, which is unavoidable.

**Implementation note — verified 2026-08-03 on ansible-core 2.11.5.** Both forms
resolve correctly from a sibling role:

```yaml
src: ../../alpha/templates/shared.j2                      # OK
src: "{{ playbook_dir }}/roles/alpha/templates/shared.j2" # OK
```

Use the relative form. Do **not** copy the templates into the `whatif` role under
any circumstances — that reintroduces exactly the drift this design exists to
prevent.

### 3. Check groups

Each check appends `{name, status, detail}` to a `whatif_results` list.
`status` is one of `PASS`, `FAIL`, `UNKNOWN`.

Groups **A, B, C and E** run under `make whatif` and populate the report.
Group **D** is not part of the report at all — it is the set of edits that make
`make whatif_preview` truthful, and it lives in the existing `roles/setup`.

**Group A — lockout (`ssh_safety.yml`)**

| Check | Method | FAIL condition |
|---|---|---|
| Rendered sshd config is valid | `sshd -t -f <temp>` as root | non-zero exit |
| Connecting user survives `AllowUsers` | parse rendered file; compare to `ansible_user` | `AllowUsers` present and user absent |
| `authorized_keys` non-empty | `stat` + line count on `~{{ ansible_user }}/.ssh/authorized_keys` | missing or empty |
| New port is free | `ss -tlpnH` on the target | bound by a process that is not sshd |
| Port 22 still bound | parse rendered file | `ssh_keep_port_22` true but no `Port 22` |
| Key algorithms not narrowed | grep rendered file | `PubkeyAcceptedAlgorithms` present |
| UFW will allow the new port | `ufw status` + the `_ufw_ports` expression | port absent from both |

**Group B — proxy health (`service_health.yml`)**

| Check | Method | FAIL condition |
|---|---|---|
| Three containers up | `docker ps` | any of nginx/shadowsocks/certbot not running |
| Tunnel serves traffic | raw WebSocket handshake from the target to `127.0.0.1:443` with correct SNI and `Host` | response is not `101` |
| Plugin is loopback-only | `ss -tlpnH` | port 9000 bound to anything but `127.0.0.1` |
| Certificate healthy | `openssl x509 -enddate` | fewer than 14 days remaining |

**Group C — drift (`drift.yml`)**

Render both server templates and diff against the live files
(`~/shadowsocks_config/docker/nginx/default.conf`,
`~/shadowsocks_config/docker/shadowsocks/config.json`). Report every difference.

This is the check that would have caught the original `"server":"0.0.0.0"`
regression.

"Functional difference" must be mechanical, not a judgement call. Normalise both
sides before comparing:

1. strip whole-line comments (`#` for nginx and INI, none for JSON)
2. strip trailing whitespace and blank lines
3. for JSON, compare parsed objects rather than text

`FAIL` when the normalised forms differ; `PASS` when only the raw forms differ.
Attach the **raw** diff either way, so a comment-only difference is still visible.

This matters because the live files were hand-edited on 2026-08-03 and are not
byte-identical to a fresh render — a naive byte comparison would report `FAIL`
permanently and train the operator to ignore it.

**Group D — change preview (honest `--check`)**

Three edits to the existing role, so `--check --diff` stops lying:

```yaml
# swap.yml — read-only probe, safe and truthful to run in check mode
- name: Checking for active swap
  command: swapon --show --noheadings
  check_mode: no

# ssh.yml — in check mode the template wrote nothing, so validating the
# assembled config would test the OLD file and report a false PASS.
- name: Validating the assembled sshd configuration
  command: /usr/sbin/sshd -t
  when: not ansible_check_mode

# ssh.yml — the reconnect must not run when nothing was changed, or it
# attempts a port that was never bound and triggers a spurious rescue.
- name: Reconnecting after applying the sshd hardening drop-in
  include_tasks: ssh-reconnect.yml
  when:
    - harden_ssh | bool
    - not ansible_check_mode
```

The `set_fact: ansible_port` / `ping` / explicit-failure tasks are gated the same way.

### 4. Opt-in shadow sshd (`shadow_sshd.yml`)

Runs only when `whatif_shadow_sshd` is true. Sequence:

1. Generate a throwaway keypair in the temp dir.
2. Write a temp `AuthorizedKeysFile` containing only that public key.
3. Start `sshd -D -p <ephemeral> -o ListenAddress=127.0.0.1 -o AuthorizedKeysFile=<temp> -f <rendered>` as a background process.
4. From the target itself, `ssh -p <ephemeral> -i <temp key> <ansible_user>@127.0.0.1 true`.
5. Kill the process; delete the temp material.

Exit 0 proves the rendered config admits that user by public key — exercising
`AllowUsers`, `AuthenticationMethods` and `PermitRootLogin` for real. Entirely
self-contained on the target; no port forwarding.

It does **not** test the operator's own key. That is acceptable: their key is
already known-working, and the playbook never writes `authorized_keys` — a fact
Group E verifies.

Steps 3–5 must sit in a `block`/`always` so the process is killed even on failure.

### 5. Group E — self-proving harmlessness

Before any check, `stat` with `get_checksum: yes` over:

```
/etc/ssh/sshd_config
/etc/ssh/sshd_config.d/
/etc/fail2ban/jail.local
/etc/fstab
/etc/sysctl.d/99-swappiness.conf
~{{ ansible_user }}/.ssh/authorized_keys
```

Repeat at the end and assert every checksum is unchanged. Any difference is a
`FAIL` — the preflight has violated its own contract.

Everything the role writes goes under a directory created by the `tempfile` module,
removed in `main.yml`'s `always:` block. No task in the role uses `notify:`, and the
role registers no handlers.

### 6. Report format

```
WHATIF PREFLIGHT — <host>                              2026-08-03 14:52 UTC

LOCKOUT
  PASS   rendered sshd config valid            sshd -t exit 0
  PASS   user 'ubuntu' survives AllowUsers     AllowUsers ubuntu
  PASS   authorized_keys intact                1 key, sha256 unchanged
  PASS   port <ssh-port> free                       bound by sshd only
  PASS   port 22 remains bound                 break-glass preserved
  UNKNOWN cloud firewall allows <ssh-port>          CANNOT VERIFY — check Lightsail

PROXY
  PASS   containers running                    nginx, shadowsocks, certbot
  PASS   tunnel serves traffic                 HTTP/1.1 101
  PASS   plugin loopback-only                  127.0.0.1:9000
  PASS   certificate healthy                   87 days remaining

DRIFT (what `make deploy_server` would overwrite)
  PASS   nginx-release.j2                      comments/whitespace only
  PASS   shadowsocks-config.j2                 identical

INTEGRITY
  PASS   no file modified by this preflight    6 checksums unchanged

RESULT: 12 PASS, 0 FAIL, 1 UNKNOWN
```

`report.yml` ends with `fail:` when any `FAIL` exists. `UNKNOWN` never fails the
run but is always printed, and is never rendered as `PASS`.

### 7. What this deliberately cannot prove

The **cloud firewall**. There is no AWS CLI locally or on the host, and the attached
`AmazonLightsailInstanceRole` cannot call the Lightsail API. This is reported as
`UNKNOWN — verify manually`, never `PASS`. Silently passing an unverifiable check is
how a preflight becomes a liability.

It also does not prove the untested execution paths — `fallocate`/`mkswap`,
`apt install fail2ban`, the rescue rollback under a real failure. Those still need a
throwaway instance.

## Testing

- `ansible-playbook --syntax-check deploy/whatif.yml`.
- Run against the live production host — it must be safe there by construction, and
  Group E proves it after the fact.
- Negative tests, on a throwaway instance only: set `ssh_allow_users: [nosuchuser]`
  and confirm the lockout group reports `FAIL`; point `ssh_port` at a port already
  bound by another service and confirm the port check reports `FAIL`; revert
  `shadowsocks-config.j2` to `0.0.0.0` and confirm Group C reports `FAIL`.

**The negative tests are the important ones.** A preflight that has only ever
returned `PASS` is not known to detect anything — the same reasoning that made
`tests/run.sh` prove itself by failing on injected drift.
