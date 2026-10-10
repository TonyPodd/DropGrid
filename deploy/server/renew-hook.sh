#!/bin/sh
set -eu
[ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/dropgrid-ip ] || exit 0
proxy_root=$(docker volume inspect personal-backend_caddy_config --format '{{.Mountpoint}}')
install -d -m 700 "$proxy_root/dropgrid/tls"
install -m 644 "$RENEWED_LINEAGE/fullchain.pem" "$proxy_root/dropgrid/tls/fullchain.pem"
install -m 600 "$RENEWED_LINEAGE/privkey.pem" "$proxy_root/dropgrid/tls/privkey.pem"
docker exec personal-backend-caddy-1 caddy validate --config /etc/caddy/Caddyfile
docker exec personal-backend-caddy-1 caddy reload --config /etc/caddy/Caddyfile
