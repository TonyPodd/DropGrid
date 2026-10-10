#!/bin/sh
set -eu
umask 077
cd /opt/dropgrid
backup_root=/srv/dropgrid/backups
mkdir -p "$backup_root"
backup_file="$backup_root/dropgrid-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose --env-file .env.server -f docker-compose.server.yml exec -T postgres pg_dump -U dropgrid -d dropgrid -Fc > "$backup_file.tmp"
mv "$backup_file.tmp" "$backup_file"
sha256sum "$backup_file" > "$backup_file.sha256"
# Preserve migration backup separately; only daily dated dumps rotate.
find "$backup_root" -maxdepth 1 -name 'dropgrid-*.dump*' -mtime +7 -delete
