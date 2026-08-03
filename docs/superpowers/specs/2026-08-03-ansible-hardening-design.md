# Repeatable server hardening in Ansible — design

**Date:** 2026-08-03
**Status:** approved, pending implementation plan

## Problem

On 2026-08-03 the production server (`<server-ip>`, `<your-domain>`, AWS
Lightsail) was hardened by hand. The repository was not updated, so the live host
and the playbooks have diverged in four places:

| # | Drift | Failure mode |
|---|-------|--------------|
| 1 | `roles/server/templates/shadowsocks-config.j2` still has `"server":"0.0.0.0"` | `make deploy_server` **silently** re-exposes `v2ray-plugin` on all interfaces |
| 2 | `roles/server/templates/nginx-release.j2` has no SNI-reject block | `make deploy_server` **silently** restores domain disclosure on bare-IP probes |
| 3 | `roles/setup/tasks/network.yml` hardcodes the UFW loop `[22, 443, 80]` | a fresh host is built with no route to the hardened SSH port |
| 4 | No sshd hardening, fail2ban, or swap anywhere in the roles | none of the hardening is reproducible |

Drift 1 and 2 are the dangerous ones: `template:` is declarative and total, so it
rewrites the file unconditionally and reverts without an error. Drift 3 only bites
on a fresh host, because existing UFW rules persist across re-runs.

## Goals

- Re-running any `make` target must never revert the hardening.
- Defaults must be no-ops. This is a published GPL repo; a stranger re-running
  `make setup_server` must get behaviour identical to today and must not be
  lockable out of their own server.
- A failed SSH change must roll itself back rather than stranding the operator.

## Non-goals

Explicitly declined during design and **out of scope**:

- Disabling nginx access logs (operator chose to keep client-IP logging).
- Setting `fast_open: false`.
- Any change to `roles/setup/tasks/packages.yml`.
- Committing or pushing. The implementer writes files only.

## Design

### 1. New variables

Added to `variables.example.yml`. Every default reproduces current behaviour.

```yaml
# --- SSH hardening (all defaults are no-ops) ---
harden_ssh: no            # master switch for the sshd drop-in
ssh_port: 22              # port sshd listens on
ssh_keep_port_22: yes     # ALSO bind 22, for provider-console break-glass
ssh_allow_users: []       # [] = emit no AllowUsers directive
ssh_accept_lockout_risk: no   # override the ssh_keep_port_22 safety assert
install_fail2ban: no
fail2ban_maxretry: 3
fail2ban_findtime: 1h
fail2ban_bantime: 24h
swap_size_mb: 0           # 0 = skip swap entirely
```

`ssh_accept_lockout_risk` exists only to make the unrecoverable combination
(`harden_ssh: yes` + `ssh_keep_port_22: no`) a deliberate, typed-out choice rather
than an accident. See §6.

Production `variables.yml` (vault-encrypted, operator-maintained) sets:

```yaml
harden_ssh: yes
ssh_port: <ssh-port>
ssh_allow_users: [ubuntu]
install_fail2ban: yes
swap_size_mb: 2048
```

### 2. File layout

```
deploy/roles/setup/
├── handlers/main.yml            NEW   reload sshd, restart fail2ban
├── tasks/
│   ├── main.yml                 MOD   import ssh.yml, fail2ban.yml, swap.yml
│   ├── network.yml              MOD   UFW loop becomes variable-driven
│   ├── ssh.yml                  NEW
│   ├── fail2ban.yml             NEW
│   └── swap.yml                 NEW
└── templates/
    ├── 10-hardening.conf.j2     NEW
    └── jail.local.j2            NEW

deploy/roles/server/templates/
├── nginx-release.j2             MOD
└── shadowsocks-config.j2        MOD
```

### 3. Server-template fixes (ungated, always on)

`shadowsocks-config.j2`: `"server":"0.0.0.0"` → `"server":"127.0.0.1"`.

Rationale: nginx has always proxied to `127.0.0.1:9000`, so the external bind was
never load-bearing — `0.0.0.0` was a latent bug, not a feature.

`nginx-release.j2`: prepend a default server that rejects unmatched SNI.

```nginx
server {
  listen 443 ssl default_server;
  ssl_reject_handshake on;
}
```

Rationale: only affects clients arriving without correct SNI, which on a
circumvention endpoint is by definition not a legitimate client.

**Constraint:** `ssl_reject_handshake` requires nginx >= 1.19.4. `docker-compose.yml`
pins the floating `image: nginx` (currently 1.29.2), so this holds. If the image is
ever pinned to an older tag this block must be removed.

**Interaction to preserve:** certbot renews via `--webroot`. The `:80` server 301s
`/.well-known/` to `:443`, and Let's Encrypt follows redirects. ACME sends correct
SNI, so the reject block does not interfere. Verified against staging on 2026-08-03
("all simulated renewals succeeded").

### 4. `network.yml` — variable-driven UFW

```yaml
- name: Compute firewall ports
  set_fact:
    _ufw_ports: "{{ ([ssh_port] + ([22] if ssh_keep_port_22 else []) + [80, 443]) | unique }}"

- name: Opening firewall ports
  become: yes
  ufw:
    rule: allow
    port: "{{ item }}"
  loop: "{{ _ufw_ports }}"
```

With defaults (`ssh_port: 22`) this yields `[22, 80, 443]` — the current set.

### 5. `10-hardening.conf.j2`

Rendered to `/etc/ssh/sshd_config.d/10-hardening.conf`. The `10-` prefix is
**required**: `Include` sits at line 12 of `sshd_config` and sshd takes the
**first** value seen for most keywords, so the drop-in must sort before
`60-cloudimg-settings.conf` to win.

```jinja
Port {{ ssh_port }}
{% if ssh_keep_port_22 and ssh_port != 22 %}
# Break-glass: provider web consoles (Lightsail, and others) often reach only
# port 22 and cannot be retargeted. Left BOUND here and blocked at the CLOUD
# firewall, so recovery is one toggle in the provider UI rather than a
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

LogLevel VERBOSE
```

Crypto (`Ciphers`/`MACs`/`KexAlgorithms`) is deliberately **not** set. Ubuntu 22.04
defaults already exclude weak algorithms, and narrowing `PubkeyAcceptedAlgorithms`
risks rejecting the operator's RSA-4096 key.

`AllowTcpForwarding no` disables `ssh -D`. This is intentional but worth knowing:
it removes a plausible emergency SOCKS fallback if the Shadowsocks stack breaks.

### 6. `ssh.yml` — validate, verify, roll back

```yaml
- name: Refuse an unrecoverable SSH configuration
  assert:
    that:
      - (not harden_ssh|bool) or ssh_keep_port_22|bool
         or ssh_accept_lockout_risk|default(false)|bool
    fail_msg: >-
      harden_ssh with ssh_keep_port_22=no removes the rollback path: the rescue
      block would have no route back to the host. Set ssh_accept_lockout_risk=yes
      to override.

- block:
    - template:
        src: 10-hardening.conf.j2
        dest: /etc/ssh/sshd_config.d/10-hardening.conf
        validate: '/usr/sbin/sshd -t -f %s'   # syntax of the fragment
        backup: yes
      register: _sshd_drop_in
      notify: reload sshd

    - command: /usr/sbin/sshd -t             # the ASSEMBLED config
      changed_when: false

    - meta: flush_handlers                    # reload before verifying

    - meta: reset_connection
    - set_fact:
        ansible_port: "{{ ssh_port }}"
    - ping:                                   # real auth over the new port

  rescue:
    # Hardcoded 22, not a variable: the assert above guarantees 22 is bound
    # whenever this path is reachable without an explicit override.
    - set_fact:
        ansible_port: 22
    - meta: reset_connection
    - name: Restore previous sshd drop-in
      copy:
        src: "{{ _sshd_drop_in.backup_file }}"
        dest: /etc/ssh/sshd_config.d/10-hardening.conf
        remote_src: yes
      when: _sshd_drop_in.backup_file is defined
    - service: name=ssh state=reloaded
    - fail:
        msg: "SSH hardening failed verification and was rolled back."
  when: harden_ssh | bool
```

**Why `ping:` and not `wait_for:`** — `wait_for: port=<ssh-port>` proves only that a TCP
socket is open. An sshd that accepts connections and rejects every key would pass
it. Resetting the connection, repointing `ansible_port`, and running a trivial
module re-authenticates through Ansible's own SSH transport, so the verification
and the thing being verified are the same mechanism. A semantic lockout (wrong
`AllowUsers`, `AuthenticationMethods` excluding the key) therefore fails the play.

**Known limitation:** `validate: sshd -t -f %s` checks the fragment in isolation,
using built-in defaults for anything unspecified. It catches syntax errors but not
conflicts with the main config — hence the separate full `sshd -t` task.

### 7. `fail2ban.yml` + `jail.local.j2`

Install `fail2ban`, template `/etc/fail2ban/jail.local`, enable the service.
Guarded by `when: install_fail2ban | bool`.

```jinja
[DEFAULT]
bantime  = {{ fail2ban_bantime }}
findtime = {{ fail2ban_findtime }}
maxretry = {{ fail2ban_maxretry }}
ignoreip = 127.0.0.1/8 ::1
banaction          = ufw
banaction_allports = ufw

[sshd]
enabled  = true
port     = {{ ssh_port }}{% if ssh_keep_port_22 and ssh_port != 22 %},22{% endif %}

backend  = auto
logpath  = /var/log/auth.log
maxretry = {{ fail2ban_maxretry }}

[recidive]
enabled   = true
logpath   = /var/log/fail2ban.log
banaction = ufw
bantime   = 4w
findtime  = 1d
maxretry  = 3
```

`banaction = ufw` is chosen over the default `iptables-multiport` so bans appear in
`ufw status` and survive a `ufw reload`. `backend`/`logpath` are pinned explicitly
because persistent journald storage is not configured on this image, whereas
`/var/log/auth.log` is known-populated.

The `[recidive]` values are deliberately hardcoded rather than parameterised: it is
a second-order jail whose thresholds only make sense relative to `[sshd]`, and
exposing four more variables to tune it would be YAGNI.

### 8. `swap.yml`

Idempotent; runs only when `swap_size_mb > 0` and no swap is currently active.
Creates `/swapfile`, `chmod 600`, `mkswap`, `swapon`, appends an `fstab` entry, and
writes `vm.swappiness=10` to `/etc/sysctl.d/99-swappiness.conf`.

### 9. Docs

- `README.md` and `README.eng.md`: a hardening-variables section plus the
  `ansible_port` warning below.
- `CLAUDE.md`: the dual-bind rationale in Architecture Notes.

## Operator actions this design cannot automate

1. **`inventories/hosts.ini` needs `ansible_port`.** The file is vault-encrypted and
   `hosts.example.ini` carries no port. Once port 22 is closed at the cloud firewall,
   every `make` target fails to connect. Fix via `ansible-vault edit`:
   `server ansible_host=... ansible_port=<ssh-port>`. `hosts.example.ini` should gain a
   commented example.
2. **Lightsail firewall rules.** There is no AWS CLI locally or on the host, and the
   attached `AmazonLightsailInstanceRole` cannot call the Lightsail API. Opening
   <ssh-port> and closing 22 are manual web-console actions.

## Testing

`ansible-playbook --syntax-check` and `--check` for all touched playbooks.

`--check` does **not** exercise the `rescue` path, and the SSH role cannot be
end-to-end tested against production without risking the access it manages.
**Validate on a throwaway Lightsail instance before pointing this at production.**

Post-run verification on a real host:

```bash
ssh -p <ssh-port> <host> 'sudo sshd -T | grep -E "^(port|allowusers|maxauthtries)"'
ssh -p <ssh-port> <host> 'sudo fail2ban-client status sshd'
ssh -p <ssh-port> <host> 'sudo ss -tlpn | grep 9000'   # expect 127.0.0.1:9000

# proxy still tunnelling — expect HTTP/1.1 101
printf 'GET /ss HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n' <your-domain> \
  | openssl s_client -connect <your-domain>:443 -servername <your-domain> -quiet | head -3

# bare-IP probe reveals nothing — expect "unrecognized name"
openssl s_client -connect <ip>:443 -noservername </dev/null 2>&1 | grep -i unrecognized
```
