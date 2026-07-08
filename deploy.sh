#!/usr/bin/env bash
# Deploy to the Hetzner VM. Usage: ./deploy.sh user@vm-host
#
# The server's .env is NEVER touched by this script (secrets live only on the
# server). Create it once on the VM before the first deploy — see README.
set -euo pipefail

TARGET="${1:?Usage: ./deploy.sh user@vm-host}"
REMOTE_DIR="andrejsala"   # relative to the SSH user's home

echo ">> Checking the server has an .env ..."
if ! ssh "$TARGET" "test -f $REMOTE_DIR/.env"; then
  echo "!! No $REMOTE_DIR/.env on the server yet."
  echo "   Create it first (see README 'Deploy to Hetzner'), then re-run."
  exit 1
fi

echo ">> Syncing code to $TARGET:~/$REMOTE_DIR (leaving .env + volumes intact)"
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude 'venv' --exclude '__pycache__' \
  --exclude '.env' --exclude '*.db' --exclude '.DS_Store' \
  ./ "$TARGET:$REMOTE_DIR/"

echo ">> Building + starting the prod stack on the VM"
ssh "$TARGET" "cd $REMOTE_DIR && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"

echo ">> Done. Status:"
ssh "$TARGET" "cd $REMOTE_DIR && docker compose ps"
