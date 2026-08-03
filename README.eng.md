
[Here](docs/azure.md) there is an example of setting a  microsoft azure web server (eng thranslation is in progress)

# Script for automated deploy of Shadowsocks + v2ray on remote server. Also generates client configs for ios/android/pc

1. [About](#about)
2. [What's inside?](#tech-details)
3. [Before starting](#pre-setup)
4. [Local environment setup](#local-setup)
5. [Server configs](#server-setup)
6. [Host hardening (optional)](#hardening)
7. [Deploy and tags](#deploy)
8. [Client setup](#client)
9. [Typical errors](#faq)


# About <a id="about"></a>
- [Shadowsocks](https://shadowsocks.org/en/index.html) - fast, secure, customizable proxy-server
- [v2ray](https://www.v2ray.com/en/#project-v-) - instrument for client-server communication

The script deploys Shadowsocks server and v2ray plugin. The connection is established through websocket(https), so you'll have to register a domain for your server  
  
Together these instruments form a secure and hard-to-track network for proxying and avoiding gouvernment restriction.

V2ray is known as a working way of passing the Great Chineese Firewall. Unlike VPN, your internet provider cannot track it that easy. It also minimizes speed vaste and mobile phone battery consumption.
**v2ray is not designed to secure the traffic**. It's main purpose is to avoid the gouvernment restrictions.

# What's inside? <a id="tech-details"></a>

- Server side:

  Long story short: docker is installed, ports opened, Nginx, Shadowsocks-rust and Certbot containers started. You can get more details from [Schema](docs/server-eng.png)

- Client side:

  Config.json for ss-local client and qr-code for mobile devices generated


# Before starting <a id="pre-setup"></a>

1. You need a vps server. Minimal possible configuration is enough, just check if the monthly bandwith limit is enough for you. Google, Amazon and Oracle provide free VM instances from month to life-long usage. You can check it [here](https://github.com/ripienaar/free-for-dev#major-cloud-providers)

2. You also need a domain name for the server. There are a lot of services with free domain names. [list](https://github.com/ripienaar/free-for-dev#dns), [the one I use](https://www.dnsexit.com/)

# Local environment setup <a id="local-setup"></a>

You need:
- Python 3.7
- Virtualenv
  
## Installing python 3.7 

### For windows:
  1. Download [Windows x86-64 executable installer](https://www.python.org/downloads/release/python-370/)
  2. Launch 
  3. Install

### Ubuntu linux
```bash
sudo apt update
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install python3.7
```

### MacOS
```bash
brew install python@3.7
```

## virtualenv install
```bash
python3.7 -m pip install virtualenv
```

## Creating virtual environment and installing dependencies
In project directory type:
```bash
python3.7 -m virtualenv venv --python 3.7
source venv/bin/activate
pip install -r requirements.txt
```

Use `source venv/bin/activate' to activate virtual environment before deployment

Note: this repo ships its own `ansible.cfg`, and Ansible only ever loads the
first config file it finds (it does not merge several) — so it completely
shadows any personal `~/.ansible.cfg` you may have; copy over anything you
rely on there into `./ansible.cfg`.


# Server configuration <a id="server-setup"></a>

1. Duplicate file `variables.example.yml` and rename it into `variables.yml`. Modify it with your own values:

- user: username you use to login on the server
- host: domain you rented in the "Before starting" step
- email: email used for SSL keys generation
- proxy_password: proxy password, use something complicated
- method: encryption metod, you can let it be as in example
- local_port: local available port on your pc
- fast_open: true to make things faster. Works on linux kernel 3.1+
- endpoint: proxy endpoint, use whatever you want
- enable_firewall: yes to enable firewall on server after setup. If you have other projects on the server it may stop working

2. Duplicate `inventories/hosts.example.ini` and rename to `hosts.ini`. Modify it with your own values:

- server ansible_host= domain you registered in "Before starting" step

# Host hardening (optional) <a id="hardening"></a>

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

All of these variables already have safe defaults in
`deploy/roles/setup/defaults/main.yml`, so an existing `variables.yml` created
before hardening was added does not need to be edited to keep working.

### Before you set `ssh_port`

**Add a matching `ansible_port` to `inventories/hosts.ini`:**

    server ansible_host=yourserver.com ansible_port=52200

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

### Pre-flight checks (and their limits)

Before touching sshd, the role refuses to run if:
- `harden_ssh` is on, `ssh_keep_port_22` is off, and `ssh_accept_lockout_risk`
  is not set — see above.
- the current connection is `ansible_user=root` — `harden_ssh` sets
  `PermitRootLogin no`, which would lock root out.
- the current connection authenticates with a password (`ansible_password` /
  `ansible_ssh_pass`) — `harden_ssh` sets `AuthenticationMethods publickey`
  and `PasswordAuthentication no`, which would lock it out.

These checks only see what Ansible's own connection variables expose. They
cannot detect root reached via `User root` in `~/.ssh/config`, nor a key that
is offered but not yet authorised on the server. They reduce the risk of a
lockout; they do not eliminate it.

### fail2ban bans are not SSH-only

The `ufw` ban action blocks the offending address on **all ports**, including
443. A host that fails SSH authentication will also lose the proxy. Unban with:

    sudo fail2ban-client set sshd unbanip <IP>

### A re-run can ban yourself: `MaxAuthTries 3` + a loaded ssh-agent

The hardening drop-in sets `MaxAuthTries 3`, and fail2ban's `ignoreip` only
covers loopback. If your `ssh-agent` offers four or more keys, sshd can burn
through the auth budget before your intended key is even tried — on a
**re-run** of this playbook (the first run is safe, since fail2ban is
installed only after ssh.yml). Once that happens, fail2ban's `ufw` banaction
blocks you on all ports, proxy included, exactly as above. Add
`IdentitiesOnly yes` to the relevant `Host` block in your `~/.ssh/config` so
only the intended key is offered, and consider extending fail2ban's
`ignoreip` (in `jail.local.j2` / `fail2ban_*` variables) with a trusted
address you connect from. Recover with the same `unbanip` command above.

### Preflight — before you harden a real host

    make whatif          # read-only checks, exits non-zero on any FAIL
    make whatif_deep     # also starts a shadow sshd and authenticates for real
    make whatif_preview  # the real setup.yml in --check --diff mode, shows a diff — not the preflight report

`make whatif` modifies nothing and proves it: it checksums every file the
hardening playbook can write (`whatif_guarded_paths` in
`deploy/roles/whatif/defaults/main.yml`, plus your `authorized_keys`) before
and after its own run. The INTEGRITY result is `PASS` only if every checksum
is unchanged, `FAIL` if any file changed, and `UNKNOWN` if a guarded file
exists but could not be read (a permissions problem, say) — that file was
never actually compared, so `UNKNOWN` is never counted as a pass.

`make whatif_deep` additionally starts a second sshd on loopback (port
61022) and authenticates against it with a throwaway keypair, to prove the
rendered config really admits you. It tests the **assembled** config — the
candidate `10-hardening.conf` drop-in concatenated with the real
`/etc/ssh/sshd_config` — not the drop-in alone. The drop-in by itself is
missing directives that live only in the main config (`UsePAM yes` on
Debian/Ubuntu); without PAM, sshd does its own account check and refuses a
key-only account with a locked password, which is the correct state for a
hardened account. Testing the fragment alone therefore reports FAIL on a
host that is already correctly hardened. A successful probe authenticates
through PAM for real, so `make whatif_deep` is not entirely inert: it
additionally records a login in the host's auth accounting
(`wtmp`/`lastlog`/`utmp`) — `make whatif` (without `_deep`) does not.

If `make whatif_deep` is interrupted (Ctrl-C) mid-probe, `always:` cleanup
does not run on SIGINT, so the shadow sshd on `127.0.0.1:61022` and its
throwaway key in the temp dir can be left behind. Recover with:

    sudo ss -tlpnH | grep :61022    # find the leaked shadow sshd's PID
    sudo kill <pid>                 # then remove it

`make whatif_preview` is a different command with different output: it runs
the real `setup.yml` with `--check --diff` and shows Ansible's own diff of
what would change. It does not produce the PASS/FAIL/UNKNOWN report above.

It reports `UNKNOWN` for the cloud firewall. That is **not** a pass — there
is no way to query your provider's firewall from here, so open the new SSH
port in your provider console and verify it yourself.

The DRIFT checks compare the real templates against what is actually
installed on the target. A malformed, empty, or unreadable live file
degrades that check to `UNKNOWN` rather than crashing the preflight, so the
rest of the report — INTEGRITY included — is still produced.

**A green preflight is not a substitute for testing on a throwaway
instance.** It cannot exercise `fallocate`/`mkswap`, `apt install fail2ban`,
or the hardening role's rescue rollback under a real failure.

# Deploy and tags <a id="deploy"></a>

## Server deploy

Pre-setup requires docker and docker-compose to be installed on the server, ports 80 and 443 also must be open. You can do it automaticly, typing:

```bash
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/setup.yml  
```

Available tags: 

- disable-iptables - Flushes iptables config. Actual for the oracle cloud instances


After successful pre-setup deploy server and SSL-keys generator typing:

```bash
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/server.yml   
```

Available tags:

- nginx - deploy nginx config. Run it if you changed proxy server endpoint
- shadowsocks - Run it if you need to change proxy-server configs
- keys - SSL-keys generation service deploy. Run it if you changed domain

### tags:
type the command like

```bash
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/setup.yml --tags disable-iptables 
```
to list the tags

### Example

setup server
```bash
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/setup.yml  
```
flush iptables
```bash
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/setup.yml --tags disable-iptables 
```

deploy shadowsocks
```bash
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/server.yml   
```


## Client deploy

```bash
ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/client.yml
```

Run it to generate the client config. In the `client-config` directory you'll be able to see the next files:
- shadowsocks-libev.config For ubuntu client
- config.json For windows and mac clients
- qrcode.png For android and ios cleints [Author](https://github.com/OriginCode/shadowsocks-libev-qrcode)

# Client setup<a id="client"></a>

## Ubuntu linux

```bash
sudo apt update
sudo apt install shadowsocks-libev
sudo apt install shadowsocks-v2ray-plugin
```

Copy client config to the ss-client root directory:
```bash
cp client-config/shadowsocks-libev.json /etc/shadowsocks-libev/config.json
```

Run server using command:
```bash
ss-local
```

## Android

Install [shadowsocks](https://play.google.com/store/apps/details?id=com.github.shadowsocks) and 
[v2ray](https://play.google.com/store/apps/details?id=com.github.shadowsocks.plugin.v2ray)

Press "add new" icon

![](docs/android.png)

and choose "scan qr code"

Use qr code from `client-config/qrcode.png`

## Windows
-Download the latest version of [shadowsocks](https://github.com/shadowsocks/shadowsocks-windows/releases) and unpack it. 
- Download the latest version of [v2ray](https://github.com/shadowsocks/v2ray-plugin/releases) and unpack it. Rename the file inside to v2ray.exe, save it to the shadowsocks directory
- Run Shadowsocks.exe, fill the fields the same as in client-config/shadowsocks-libev.json or just import it

## iOS
use Shadowrocket from appstore, scan qr code and enjoy

# Typical errors <a id="faq"></a>

## Oracle

Oracle has complicated iptables config which will not allow you to setup proxy that easy. Just add `disable-iptables` tag during server setup

## Provider-side firewalls

Check if the VPS provider has its own firewall and allow ports 80, 443 or disable it