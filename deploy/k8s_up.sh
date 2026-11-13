#!/usr/bin/env bash
# Build the image, load it into your local Kubernetes cluster and deploy it. Safe to re-run:
# re-running also refreshes the expiring Learner Lab keys.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and try again."; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl not found. Turn on Kubernetes in Docker Desktop, or install kind/minikube."; exit 1; }
CTX=$(kubectl config current-context 2>/dev/null || true)
[ -n "$CTX" ] || { echo "No Kubernetes cluster is selected. Start one first."; exit 1; }
echo "Using Kubernetes context: $CTX"
IMAGE=claimguard-web:1
ENVFILE=deploy/.container.env
trap 'rm -f "$ENVFILE"' EXIT

docker build -f deploy/Dockerfile -t "$IMAGE" .
case "$CTX" in
  kind-*)   kind load docker-image "$IMAGE" --name "${CTX#kind-}" ;;
  minikube) minikube image load "$IMAGE" ;;
esac

kubectl apply -f deploy/k8s/namespace.yaml
python deploy/make_env.py "$ENVFILE"
kubectl -n claimguard create secret generic claimguard-secrets --from-env-file="$ENVFILE" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f deploy/k8s/configmap.yaml -f deploy/k8s/service.yaml -f deploy/k8s/deployment.yaml
kubectl -n claimguard rollout restart deployment/claimguard-web
kubectl -n claimguard rollout status deployment/claimguard-web --timeout=180s

kubectl -n claimguard port-forward svc/claimguard-web 8081:80 >/dev/null 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null; rm -f "$ENVFILE"' EXIT
sleep 3
bash deploy/smoke.sh http://localhost:8081
kubectl -n claimguard get pods
echo
echo "To open the site, run:  kubectl -n claimguard port-forward svc/claimguard-web 8081:80"
echo "then visit http://localhost:8081"
