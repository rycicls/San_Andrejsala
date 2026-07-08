#!/usr/bin/env bash
# Deploy — run this ON THE VM, from inside the repo directory. Not from your Mac.
#
#   ssh into the VM once, clone the repo there, then just run ./deploy.sh
#   every time you want to update (after `git pull` or with local edits).
#
# .env lives only on the VM and is never touched by this script.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "!! No .env here. Create one first (copy .env.example, then CHANGE the"
  echo "   marked values — see README 'Deploy to Hetzner')."
  exit 1
fi

# Pull the latest code, unless this is a working copy with local edits you want
# to deploy as-is (pass --no-pull to skip).
if [ "${1:-}" != "--no-pull" ]; then
  echo ">> git pull"
  git pull
else
  echo ">> Skipping git pull (--no-pull) — deploying the working tree as-is"
fi

echo ">> Building + starting the prod stack"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

echo ">> Status:"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

echo ">> Done. Recent app logs:"
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 20 app
