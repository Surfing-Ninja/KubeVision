#!/usr/bin/env bash
set -euo pipefail

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to install K3s and Helm." >&2
  exit 1
fi

if ! command -v k3s >/dev/null 2>&1; then
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable=traefik --write-kubeconfig-mode=644" sh -
else
  echo "K3s is already installed; skipping installer."
fi

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required after K3s installation, but it is not on PATH." >&2
  echo "K3s usually provides kubectl through the k3s binary; check your shell PATH." >&2
  exit 1
fi

kubectl wait --for=condition=Ready node --all --timeout=120s

if ! command -v helm >/dev/null 2>&1; then
  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
else
  echo "Helm is already installed; skipping installer."
fi

cat <<EOF
K3s foundation is ready.

Use this kubeconfig for subsequent commands:
  export KUBECONFIG=${KUBECONFIG}
EOF
