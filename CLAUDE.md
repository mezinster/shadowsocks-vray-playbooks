# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository contains Ansible playbooks for automated deployment of a Shadowsocks + V2Ray proxy server and generation of client configurations. The system enables secure, hard-to-detect proxy connections via WebSocket (HTTPS) to bypass network restrictions.

**Tech Stack:**
- Ansible for infrastructure automation
- Docker Compose for container orchestration
- Shadowsocks-rust as the proxy server
- V2Ray plugin for WebSocket tunneling
- Nginx as reverse proxy
- Certbot for SSL certificate management

## Repository Structure

```
deploy/
├── setup.yml          # Initial server setup playbook
├── server.yml         # Server deployment playbook
├── client.yml         # Client config generation playbook
└── roles/
    ├── setup/         # Server prerequisites (Docker, network, packages)
    ├── server/        # Proxy server deployment
    │   ├── tasks/     # Nginx, Shadowsocks, Certbot tasks
    │   ├── templates/ # Jinja2 templates for configs
    │   └── files/docker/  # Docker Compose and Dockerfiles
    └── client/        # Client config generation (JSON, QR codes)

inventories/
└── hosts.ini          # Ansible inventory (from hosts.example.ini)

variables.yml          # User configuration (from variables.example.yml)
```

## Common Commands

### Prerequisites Setup
```bash
# Activate Python virtual environment (required before running playbooks)
source venv/bin/activate

# Setup environment (first time only)
python3.7 -m virtualenv venv --python 3.7
pip install -r requirements.txt
```

> **Note:** the repo's `./ansible.cfg` is loaded instead of `~/.ansible.cfg`
> (Ansible uses only the first config file it finds; it does not merge them),
> so any personal Ansible settings you keep in your home directory are
> silently ignored while working in this repo — copy anything you rely on
> into `./ansible.cfg`.

### Server Deployment
```bash
# Initial server setup (installs Docker, opens ports 80/443)
make setup_server
# OR
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/setup.yml

# Flush iptables (required for Oracle Cloud and some providers)
make flush_iptables
# OR
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/setup.yml --tags disable-iptables

# Deploy proxy server (Nginx, Shadowsocks, Certbot)
make deploy_server
# OR
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/server.yml
```

### Template regression tests
```bash
./tests/run.sh              # render all templates, diff against goldens
./tests/run.sh --update     # regenerate goldens after an intended change
```

Server-role templates are rewritten wholesale on every `make deploy_server`, so a hand-edit on the server is reverted silently. These goldens turn that into a visible diff.

### Client Configuration
```bash
# Generate client configs (creates client-config/ directory with JSON, QR codes)
make deploy_client
# OR
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/client.yml
```

### Targeted Deployments (Tags)
```bash
# Redeploy only Nginx config (e.g., after changing endpoint)
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/server.yml --tags nginx

# Redeploy only Shadowsocks (e.g., after changing password/method)
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/server.yml --tags shadowsocks

# Regenerate SSL certificates (e.g., after changing domain)
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml" deploy/server.yml --tags keys
```

## Configuration Files

Before deployment, create from examples:

1. **variables.yml** (from `variables.example.yml`)
   - `user`: SSH username for remote server
   - `host`: Domain name for the server (required for HTTPS/SSL)
   - `email`: Email for SSL certificate generation
   - `proxy_password`: Shadowsocks authentication password
   - `method`: Encryption method (default: chacha20-ietf-poly1305)
   - `local_port`: Client-side SOCKS5 port
   - `endpoint`: WebSocket endpoint path (e.g., "ss")
   - `enable_firewall`: Whether to enable UFW firewall after setup

2. **inventories/hosts.ini** (from `inventories/hosts.example.ini`)
   - `[remote]` section: Set `ansible_host` to server domain/IP
   - `[local]` section: Local connection for client config generation

## Architecture Notes

### Three-Phase Deployment Model

1. **Setup Phase** (`deploy/setup.yml` → `roles/setup/`)
   - Installs system packages and Docker/Docker Compose
   - Configures network settings (port forwarding, firewall rules)
   - Optional: Flushes iptables for Oracle Cloud compatibility

2. **Server Phase** (`deploy/server.yml` → `roles/server/`)
   - Copies Docker Compose configuration to `~/shadowsocks_config/docker/`
   - Generates dynamic configs from Jinja2 templates:
     - Nginx reverse proxy config (routes WebSocket to Shadowsocks)
     - Shadowsocks server config (encryption, password, plugin)
     - Certbot configuration and cron task for renewal
   - Starts three containers: Nginx, Shadowsocks-rust, Certbot

3. **Client Phase** (`deploy/client.yml` → `roles/client/`)
   - Runs locally (not on remote server)
   - Generates platform-specific configs in `client-config/`:
     - `shadowsocks-libev.json`: For Ubuntu/Linux clients
     - `config.json`: For Windows/macOS clients
     - `qrcode.png`: For Android/iOS mobile apps

### Container Architecture

The server runs three Docker containers in host network mode:

- **Nginx**: Reverse proxy handling HTTPS termination, routes WebSocket traffic to Shadowsocks
- **Shadowsocks-rust**: Proxy server with V2Ray plugin for WebSocket transport
- **Certbot**: Automated SSL certificate generation/renewal (Let's Encrypt)

All containers share SSL certificates via volume mounts at `~/shadowsocks_config/certs-data/`.

### Template System

Jinja2 templates (`.j2` files) in `roles/server/templates/` generate configs dynamically:
- `nginx-certgen.j2`: Nginx config for initial certificate generation
- `nginx-release.j2`: Production Nginx config with SSL and WebSocket proxying
- `shadowsocks-config.j2`: Shadowsocks server configuration
- `env.j2`: Environment variables for Docker Compose
- `cron-task.j2`: Certificate renewal cron job

Templates use variables from `variables.yml` to customize for each deployment.

### Client Configuration Generation

The `roles/client/` role uses:
- `ss-qrcode/ss-qrcode.py`: Python script to generate QR codes for mobile clients
- Templates to format connection strings with user's host/password/encryption settings
- Local execution (no remote server interaction)

### SSH hardening and the dual-port design

`roles/setup/tasks/ssh.yml` deploys `/etc/ssh/sshd_config.d/10-hardening.conf`.
The `10-` prefix is required: `Include` sits at line 12 of `sshd_config` and
sshd takes the **first** value seen for most keywords, so the drop-in must sort
before `60-cloudimg-settings.conf`.

When `ssh_port` differs from 22 and `ssh_keep_port_22` is true, sshd binds
**both**. Blocking 22 is delegated to the cloud firewall so that provider web
consoles — which typically reach only port 22 — remain a break-glass path.

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

Three pre-flight `assert` tasks run before any change is made: the
`ssh_keep_port_22` / `ssh_accept_lockout_risk` guard, a check that the current
connection is not `ansible_user=root` (`harden_ssh` sets `PermitRootLogin no`),
and a check that the connection does not carry `ansible_password` /
`ansible_ssh_pass` (`harden_ssh` sets `AuthenticationMethods publickey` and
`PasswordAuthentication no`). All three only see what Ansible's own connection
variables expose — they cannot detect root reached via `User root` in
`~/.ssh/config`, nor a key that is offered but not yet authorised on the
server. They reduce lockout risk; they do not eliminate it.

The role verifies its own change by resetting the connection and
re-authenticating with `ping` over the new port. This re-authenticates through
Ansible's own SSH transport, so a semantic lockout fails the play; `wait_for`
would only prove a TCP socket was open. The `meta: flush_handlers` / `meta:
reset_connection` pair that does this lives in a separate file,
`deploy/roles/setup/tasks/ssh-reconnect.yml`, included only `when: harden_ssh |
bool`. That split exists because in ansible-core 2.11, those two `meta` tasks
do not honour a `when:` on their enclosing block — they run anyway, with a
warning. Gating the *include* works because an include is an ordinary task
whose `when` IS honoured. Do not "simplify" this back into `ssh.yml`.

On failure, the `rescue` block falls back to the SSH port that was in use
**before this run started** (`_ssh_pre_hardening_port`), not a hardcoded 22 —
if the operator's pre-existing connection was already on some other custom
port, jumping to 22 would itself go unreachable. Residual risk, documented but
not closed: if the failure happens **after** sshd reloads and that
pre-hardening port was neither `ssh_port` nor 22, the fallback port is not
guaranteed to still be bound.

`deploy/roles/setup/defaults/main.yml` supplies safe defaults (`harden_ssh:
no`, `ssh_port: 22`, etc.) for every hardening variable, so an operator
upgrading from a pre-hardening `variables.yml` does not hit `'harden_ssh' is
undefined`. Role defaults are Ansible's lowest-precedence source, so
`variables.yml` and `-e` extra-vars still override them.

**Verification status:** the rescue path's unreachable-handling and the `meta`
gating were empirically tested against ansible-core 2.11.5 with throwaway
playbooks (a forced failure produced `rescued=1`; `harden_ssh: no` produced
zero meta tasks and zero warnings). Execution against a real target host —
`fallocate`/`mkswap`/`swapon`, the actual `apt install fail2ban` and service
start, `sshd -t` against real host keys, and whether `service: ssh state:
reloaded` rebinds new ports under the target's systemd — has not been verified
live. Validate on a throwaway instance before pointing this at production.

## Important Considerations

### Security
- **Never commit `variables.yml` or `inventories/hosts.ini`** - they contain sensitive credentials
- Use strong, unique values for `proxy_password`
- V2Ray is designed for bypassing restrictions, NOT for securing traffic end-to-end

### Provider-Specific Issues
- **Oracle Cloud**: Requires `--tags disable-iptables` during setup due to restrictive default iptables rules
- **Azure**: See `docs/azure.md` for provider-specific firewall configuration
- Always verify ports 80 and 443 are open in provider's firewall/security group settings

### SSL Certificate Management
- Certificates are auto-renewed via cron task deployed with the server
- Manual regeneration needed if domain changes (use `--tags keys`)
- Initial certificate generation happens during first server deployment

### Python Version Requirement
- Playbooks require Python 3.7 specifically (not newer versions)
- Dependencies include Ansible 4.6.0 and pillow/qrcode for QR generation
- Always activate the virtualenv before running Ansible commands
