#!/bin/sh
set -eu
cd /opt/dropgrid
install -m 644 deploy/server/systemd/dropgrid-* /etc/systemd/system/
install -d /etc/letsencrypt/renewal-hooks/deploy
install -m 700 deploy/server/renew-hook.sh /etc/letsencrypt/renewal-hooks/deploy/dropgrid-caddy
systemctl daemon-reload
systemctl enable --now dropgrid-cert-renew.timer dropgrid-db-backup.timer
