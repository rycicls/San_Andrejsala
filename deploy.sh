#!/usr/bin/env bash
# Usage: ./deploy.sh user@vm-host
# Rsyncs the project to the VM and (re)starts the prod stack.
set -euo pipefail

TARGET="${1:?Usage: ./deploy.sh user@vm-host}"
REMOTE_DIR="~/andrejsala"

echo ">> Syncing to $TARGET:$REMOTE_DIR"
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  ./ "$TARGET:$REMOTE_DIR/"

echo ">> Building + starting on the VM"
ssh "$TARGET" "cd $REMOTE_DIR && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"

echo ">> Done. Check: ssh $TARGET 'cd $REMOTE_DIR && docker compose ps'"
