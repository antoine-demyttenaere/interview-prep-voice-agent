#!/bin/bash
# infra/deploy.sh - runs ON THE SERVER: builds .env from settings.env and the SSM secrets, pulls the app's image and (re)starts it.
#
# Where it fits: the first boot (user_data.sh.tftpl) copies it to /opt/interview/deploy.sh and
# runs it; each deploy from GitHub Actions (.github/workflows/deploy.yml) first downloads this
# file, docker-compose.prod.yml and the Caddyfile at the commit being deployed, then runs it
# with that commit's image tag. So the server always uses the version of this script in git.
#
# What it does:
# 1. With a tag argument, writes IMAGE_TAG=<tag> into settings.env (the non-secret settings).
# 2. Builds .env: settings.env, then every parameter under /interview-prep/prod in SSM Parameter
#    Store (set with infra/set-secrets.ps1) as NAME=value. Read with the server's IAM role.
# 3. Pulls the images and (re)starts the containers; Compose only recreates what changed.
#    Caddy reloads its Caddyfile, in case the deploy brought a new one.
# 4. Waits until the api answers /health (up to 3 minutes), else fails with the api's logs.
#
# Run by hand in a Session Manager shell: sudo /opt/interview/deploy.sh [IMAGE_TAG]
# Its output is also appended to /var/log/interview-deploy.log.
set -euo pipefail
exec > >(tee -a /var/log/interview-deploy.log) 2>&1

APP_DIR=/opt/interview
SSM_PREFIX=/interview-prep/prod
COMPOSE="docker compose -f docker-compose.prod.yml"
cd "$APP_DIR"
echo "=== deploy $(date -u +%FT%TZ) ${1:-}"

if [ -n "${1:-}" ]; then
  sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$1/" settings.env
fi
REGION=$(sed -n 's/^AWS_REGION=//p' settings.env)

umask 077
{
  cat settings.env
  # Each parameter /interview-prep/prod/NAME becomes NAME=value
  aws ssm get-parameters-by-path --region "$REGION" --path "$SSM_PREFIX" --with-decryption \
    --query 'Parameters[*].[Name,Value]' --output text |
    while IFS=$'\t' read -r name value; do echo "${name##*/}=$value"; done
} > .env.new
grep -q '^POSTGRES_PASSWORD=' .env.new || { echo "No POSTGRES_PASSWORD in SSM: run infra/set-secrets.ps1 first"; exit 1; }
mv .env.new .env

$COMPOSE pull
$COMPOSE up -d --no-build --remove-orphans
# Caddy reads its Caddyfile at start: reload it in case the deploy brought a new one (no downtime)
$COMPOSE exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || true

for i in $(seq 1 36); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' "$($COMPOSE ps -q api)" 2>/dev/null)" = "healthy" ]; then
    docker image prune -f > /dev/null
    echo "=== done: the api is healthy"
    exit 0
  fi
  sleep 5
done
echo "=== the api is not healthy after 3 minutes; its last logs:"
$COMPOSE logs --tail 40 api
exit 1
