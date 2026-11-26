#!/bin/bash
# infra/backup.sh - runs ON THE SERVER: saves the whole database to the S3 backup bucket (pg_dump).
#
# Where it fits: copied to /opt/interview/backup.sh by the first boot and by every deploy (like
# deploy.sh). A systemd timer that deploy.sh installs runs it every night at 02:30 UTC, and
# infra/server.ps1 runs it through SSM right before `down` destroys the server. On a new
# server, deploy.sh loads latest.dump back into the empty database.
#
# pg_dump writes every table (accounts, interviews, documents and their vectors, sessions, the
# LangGraph checkpoints, usage) into one compressed file ("custom" format, read by pg_restore).
# It runs inside the db container while the app keeps running: Postgres gives it a consistent
# snapshot, so the app doesn't need to stop.
# The file goes to s3://interview-prep-backups-<account id>/interview/latest.dump and to
# interview/daily/<date>.dump (kept 30 days, infra/backup.tf).
#
# Run by hand in a Session Manager shell: sudo /opt/interview/backup.sh
# Log: /var/log/interview-backup.log
set -euo pipefail
exec > >(tee -a /var/log/interview-backup.log) 2>&1

cd /opt/interview
REGION=$(sed -n 's/^AWS_REGION=//p' settings.env)
ACCOUNT=$(aws sts get-caller-identity --region "$REGION" --query Account --output text)
BUCKET="interview-prep-backups-$ACCOUNT"
STAMP=$(date -u +%Y-%m-%dT%H%MZ)
DUMP=/var/tmp/interview-backup.dump
echo "=== backup $STAMP"

docker compose -f docker-compose.prod.yml exec -T db pg_dump -U app -d interview -Fc > "$DUMP"
SIZE=$(stat -c %s "$DUMP")
if [ "$SIZE" -lt 1024 ]; then
  echo "=== the dump is only $SIZE bytes: something is wrong, nothing uploaded"
  exit 1
fi

aws s3 cp --region "$REGION" --only-show-errors "$DUMP" "s3://$BUCKET/interview/daily/$STAMP.dump"
aws s3 cp --region "$REGION" --only-show-errors "$DUMP" "s3://$BUCKET/interview/latest.dump"
: > "$DUMP" # empty the local copy (it holds personal data); the next backup reuses the file
echo "=== done: $SIZE bytes to s3://$BUCKET/interview/latest.dump"
