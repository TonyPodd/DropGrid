#!/bin/sh
set -eu
cd /opt/dropgrid
# Never remove volumes, run sender, or rebuild existing data.
git pull --ff-only origin main
docker compose --env-file .env.server -f docker-compose.server.yml build api frontend
docker compose --env-file .env.server -f docker-compose.server.yml run --rm migrate
docker compose --env-file .env.server -f docker-compose.server.yml up -d postgres api frontend worker reference-study
for attempt in $(seq 1 30); do
    if docker compose --env-file .env.server -f docker-compose.server.yml exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health',timeout=5)"; then exit 0; fi
    sleep 2
done
exit 1
