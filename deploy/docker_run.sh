#!/usr/bin/env bash
# Build the image and run it on this computer at http://localhost:18080
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and try again."; exit 1; }
ENVFILE=deploy/.container.env
trap 'rm -f "$ENVFILE"' EXIT
python deploy/make_env.py "$ENVFILE"
docker build -f deploy/Dockerfile -t claimguard-web:1 .
docker rm -f claimguard-web >/dev/null 2>&1 || true
docker run -d --name claimguard-web -p 18080:8080 --env-file "$ENVFILE" \
  -e TABLE=Claims -e ACCOUNTS_TABLE=ClaimGuardAccounts -e ENFORCE_AUTH=true \
  -e AWS_DEFAULT_REGION=us-east-1 -e AWS_REGION=us-east-1 \
  --read-only --tmpfs /tmp --restart unless-stopped claimguard-web:1 >/dev/null
echo "Container started."
bash deploy/smoke.sh http://localhost:18080
echo "Open http://localhost:18080   (stop it with: docker rm -f claimguard-web)"
