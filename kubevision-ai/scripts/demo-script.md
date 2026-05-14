# KubeVision AI Demo Script

This script walks through the end-to-end flow: incident -> causal DAG -> memory -> simulation -> PR.

## Pre-flight

1. Confirm K3s and Helm installs are healthy.
2. Port-forward backend and Prometheus.
3. Start the frontend on http://localhost:3000.

## Demo Steps

1. Open the dashboard and highlight the live dependency graph.
2. Trigger a bursty workload to induce an incident:
   - HTTP burst:
     - `MODE=http BURSTS=3 DURATION_SECONDS=20 CONCURRENCY=25 ./scripts/stress-test.sh`
   - Memory spike (requires stress-ng in the target pod):
     - `MODE=memory TARGET_POD=frontend BURSTS=2 DURATION_SECONDS=20 ./scripts/stress-test.sh`
3. Watch for a new anomaly in the Anomaly Timeline and an incident in the queue.
4. Open the incident and point out memory routing (Fast/Grounded/Cold).
5. Highlight the KubeTwin simulation card and the confidence gate.
6. Show the PR link and click Approve PR once the simulation passes.
7. If a memory case was seeded, mention the reduced reasoning time and show the memory health panel.

## Optional Talking Points

- Show causal edge lag and strength on the graph.
- Explain how memory routing reduces hallucination risk.
- Point to the Simulation passed badge as a safety gate.
