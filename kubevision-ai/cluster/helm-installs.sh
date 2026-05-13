#!/usr/bin/env bash
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required." >&2
  exit 1
fi

if ! command -v helm >/dev/null 2>&1; then
  echo "helm is required. Run ./cluster/k3s-install.sh first." >&2
  exit 1
fi

if [[ -z "${MISTRAL_API_KEY:-}" ]]; then
  echo "MISTRAL_API_KEY must be set before installing kagent." >&2
  exit 1
fi

kubectl wait --for=condition=Ready node --all --timeout=120s

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add coroot https://coroot.github.io/helm-charts
helm repo add kagent https://kagent-dev.github.io/kagent/helm
helm repo update

echo "1/4 Installing kube-prometheus-stack in namespace monitoring..."
helm upgrade --install kube-prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --wait \
  --timeout 15m

kubectl get pods -n monitoring

echo "2/4 Installing Coroot in namespace coroot..."
helm upgrade --install coroot coroot/coroot \
  --namespace coroot \
  --create-namespace \
  --wait \
  --timeout 15m

kubectl get pods -n coroot

echo "3/4 Installing kagent in namespace kagent..."
helm upgrade --install kagent kagent/kagent \
  --namespace kagent \
  --create-namespace \
  --set llm.provider=mistral \
  --set llm.model=mistral-medium-latest \
  --set llm.apiKey="${MISTRAL_API_KEY}" \
  --wait \
  --timeout 15m

kubectl get pods -n kagent

echo "4/4 Installing Google Online Boutique in namespace default..."
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/main/release/kubernetes-manifests.yaml
kubectl rollout status deployment/frontend -n default --timeout=180s
kubectl get pods -n default

cat <<'EOF'
Phase 0 install commands completed.

Prometheus verification:
  kubectl port-forward svc/prometheus-service 9090:9090 -n monitoring
  curl 'http://localhost:9090/api/v1/query?query=up'

If the Prometheus service name differs in your chart version, run:
  kubectl get svc -n monitoring
EOF
