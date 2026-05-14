#!/usr/bin/env bash
set -euo pipefail

NAMESPACE=${NAMESPACE:-kubevision}
PROM_NAMESPACE=${PROM_NAMESPACE:-monitoring}
BACKEND_SERVICE=${BACKEND_SERVICE:-kubevision-backend}
PROM_SERVICE=${PROM_SERVICE:-kube-prometheus-stack-prometheus}
BACKEND_PORT=${BACKEND_PORT:-8000}
PROM_PORT=${PROM_PORT:-9090}

# Create the secret once before deploying the backend:
# kubectl create secret generic kubevision-secrets -n kubevision \
#   --from-literal=MISTRAL_API_KEY=... \
#   --from-literal=GITHUB_TOKEN=... \
#   --from-literal=GITHUB_REPO=...

kubectl port-forward -n "${NAMESPACE}" "svc/${BACKEND_SERVICE}" "${BACKEND_PORT}:8000" &
kubectl port-forward -n "${PROM_NAMESPACE}" "svc/${PROM_SERVICE}" "${PROM_PORT}:9090" &

wait
