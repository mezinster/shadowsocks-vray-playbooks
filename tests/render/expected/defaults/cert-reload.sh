#!/bin/sh
# Managed by Ansible (server role); local edits are overwritten on deploy.
#
# certbot renews inside certbot_container, which has no Docker access, so it
# cannot make nginx and shadowsocks load the new certificate. Both read it only
# at startup and keep serving the old one until it expires. This compares the
# certificate on disk with what each service presents and reloads only a stale
# one, so a run with nothing to do touches nothing.
set -u

DOMAIN="yourserver.domain.net"
TAG=cert-reload

fingerprint() { openssl x509 -noout -fingerprint -sha256 2>/dev/null; }

served() {
    echo | openssl s_client -connect "127.0.0.1:$1" -servername "$DOMAIN" 2>/dev/null | fingerprint
}

# live/ is root-only on the host; the certbot container runs as root
on_disk=$(docker exec certbot_container cat "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" | fingerprint)
if [ -z "$on_disk" ]; then
    logger -t "$TAG" "cannot read the certificate for $DOMAIN from certbot_container"
    exit 1
fi

if [ "$(served 443)" != "$on_disk" ]; then
    if docker exec nginx_container nginx -t >/dev/null 2>&1; then
        docker exec nginx_container nginx -s reload
        logger -t "$TAG" "nginx reloaded to pick up the renewed certificate"
    else
        logger -t "$TAG" "nginx config test failed; not reloading"
    fi
fi

# 9000 matches server_port in shadowsocks-config.j2
if [ "$(served 9000)" != "$on_disk" ]; then
    docker restart shadowsocks_container >/dev/null
    logger -t "$TAG" "shadowsocks restarted to pick up the renewed certificate"
fi
