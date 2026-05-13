# KubeVision AI

KubeVision AI is an AI-powered, multi-agent AIOps platform for single-node Kubernetes clusters. It turns raw metrics and logs into causal graphs, validates fixes with a lightweight simulator, and can propose YAML patches via GitHub PRs.

The implementation targets K3s on commodity hardware (Ryzen 3 class CPU, 8GB RAM, 256GB SSD) and uses Mistral for LLM calls.

## What This Repo Contains

- A K3s-first cluster bootstrap workflow and Helm install scripts
- A FastAPI backend with causal discovery, memory storage, and agent orchestration
- A React dashboard with a live dependency DAG and incident views
- Kubernetes manifests for backend services and kagent agent CRDs

## Architecture At A Glance

```
K3s cluster
	Prometheus + kube-state-metrics
	Coroot service map + node agent
	kagent runtime (Metrics, Log, Topology, Supervisor agents)
	Causal Engine (PCMCI -> DAG JSON)
	Episodic Memory (ChromaDB + embeddings)
	KubeTwin simulator (in-memory validation)
	KubePatch agent (YAML patch + GitHub PR)

Frontend (React + React Flow + Recharts)
	WebSocket stream for DAG/metrics/incidents
```

## Key Capabilities

- Real-time causal DAGs (PCMCI) instead of correlation matrices
- Multi-agent reasoning grounded in live metrics, logs, and topology
- Episodic memory with quality gates and decay to reduce hallucinations
- Pre-flight simulation (KubeTwin) before generating fixes
- YAML patch generation with GitHub PR flow

## Repository Layout

```
KubeVision/
	.gitignore
	README.md
	kubevision-ai/
		.env.example
		backend/
		cluster/
		docker-compose.yml
		frontend/
		k8s/
		scripts/
```

## Prerequisites

- macOS or Linux
- Docker
- kubectl
- Helm 3
- Node.js 18+
- Python 3.11+
- Git

You will also need:
- Mistral API key
- GitHub token with repo write access for PR creation

## Configuration

Copy the example environment file and update values:

```
cp kubevision-ai/.env.example kubevision-ai/.env
```

The config file includes:

- `MISTRAL_API_KEY`
- `GITHUB_TOKEN`
- `GITHUB_REPO`
- `PROMETHEUS_URL`
- `CHROMA_PERSIST_DIR`
- `KUBECONFIG` (K3s default: `/etc/rancher/k3s/k3s.yaml`)

## Phase 0: Cluster Foundation (K3s)

From the repo root:

```
cd kubevision-ai
./cluster/k3s-install.sh
./cluster/helm-installs.sh
```

The Helm script installs:

1. kube-prometheus-stack (namespace `monitoring`)
2. Coroot (namespace `coroot`)
3. kagent (namespace `kagent`, Mistral configured)
4. Google Online Boutique demo workload (namespace `default`)

Prometheus verification:

```
kubectl port-forward svc/prometheus-service 9090:9090 -n monitoring
curl 'http://localhost:9090/api/v1/query?query=up'
```

## Backend Deployment (Kubernetes)

Build and load the backend image locally, then apply manifests:

```
cd kubevision-ai
docker build -t kubevision-backend:latest backend
kubectl apply -f k8s/kubevision-backend.yaml
kubectl apply -f k8s/causal-engine.yaml
kubectl apply -f k8s/memory-service.yaml
```

The backend service runs in namespace `kubevision` on port 8000.

## Frontend (Local Dev)

```
cd kubevision-ai/frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

## API Surface (Backend)

- `GET /api/dag`
- `GET /api/metrics/pods`
- `GET /api/incidents`
- `GET /api/incidents/{id}`
- `POST /api/incidents/{id}/approve-pr`
- `GET /api/memory/stats`
- `WS /ws/live`

WebSocket event types:

- `dag_update`
- `metric_update`
- `new_incident`

## Demo Workflow

1. Start K3s and Helm installs.
2. Deploy backend manifests.
3. Start the frontend.
4. Port-forward Prometheus and backend.
5. Generate a load spike to trigger an incident.

## Notes

Some optional files are present but not yet configured. Update as needed before use:

- `kubevision-ai/k8s/memory-service.yaml`
- `kubevision-ai/scripts/port-forward.sh`
- `kubevision-ai/scripts/stress-test.sh`
- `kubevision-ai/docker-compose.yml`

## License

No license has been specified yet.
