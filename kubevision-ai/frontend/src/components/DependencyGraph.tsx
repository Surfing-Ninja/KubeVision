import { useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";
import { useClusterStore } from "../store/clusterStore";
import type { DagEdge, PodMetrics, PodStatus } from "../types";

interface PodNodeData {
  podName: string;
  namespace: string;
  status: PodStatus;
  metrics?: PodMetrics;
  dimmed: boolean;
}

const formatBytes = (bytes: number | undefined): string => {
  if (!bytes || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KiB", "MiB", "GiB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
};

function metricPercent(metrics: PodMetrics | undefined): number {
  if (!metrics?.memory_limit || metrics.memory_limit <= 0) {
    return 0;
  }
  return Math.min(100, Math.round((metrics.memory_working_set / metrics.memory_limit) * 100));
}

function cpuDisplay(metrics: PodMetrics | undefined): string {
  if (!metrics) {
    return "0.00c";
  }
  if (metrics.cpu_limit && metrics.cpu_limit > 0) {
    return `${Math.min(999, Math.round((metrics.cpu_usage / metrics.cpu_limit) * 100))}%`;
  }
  return `${metrics.cpu_usage.toFixed(2)}c`;
}

function getStatus(metrics: PodMetrics | undefined): PodStatus {
  if (!metrics) {
    return "healthy";
  }
  if ((metrics.restart_count ?? 0) > 0 || (metrics.oom_events ?? 0) > 0) {
    return "critical";
  }
  if (metricPercent(metrics) >= 80 || (metrics.cpu_throttled ?? 0) > 0.1) {
    return "warning";
  }
  return "healthy";
}

function PodNode({ data }: NodeProps<PodNodeData>) {
  const statusClass =
    data.status === "critical"
      ? "pod-node--critical"
      : data.status === "warning"
        ? "pod-node--warning"
        : "pod-node--healthy";
  const dotClass =
    data.status === "critical"
      ? "status-dot status-dot--critical"
      : data.status === "warning"
        ? "status-dot status-dot--warning"
        : "status-dot";
  const meterColor =
    data.status === "critical"
      ? "var(--accent-rose)"
      : data.status === "warning"
        ? "var(--accent-amber)"
        : "var(--accent-emerald)";
  const pulseStyle = data.status === "critical" ? { animation: "critical-pulse 1.4s ease-in-out infinite" } : undefined;

  return (
    <div className={`pod-node ${statusClass} ${data.dimmed ? "is-dimmed" : ""}`} style={pulseStyle}>
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center gap-2">
        <span className={dotClass} />
        <div className="pod-node__title truncate" title={data.podName}>
          {data.podName}
        </div>
      </div>
      <div className="pod-node__meta">
        <span>{data.namespace}</span>
        <span>{data.status}</span>
      </div>
      <div className="pod-node__metrics">
        <div>
          <div>CPU</div>
          <span>{cpuDisplay(data.metrics)}</span>
        </div>
        <div>
          <div>MEM</div>
          <span>{metricPercent(data.metrics)}%</span>
        </div>
      </div>
      <div className="pod-node__meter">
        <div
          className="pod-node__meter-bar"
          style={{ width: `${Math.max(4, metricPercent(data.metrics))}%`, background: meterColor }}
        />
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = {
  healthy: PodNode,
  warning: PodNode,
  critical: PodNode,
};

function downstreamFrom(root: string, edges: DagEdge[]): Set<string> {
  const affected = new Set<string>([root]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const edge of edges) {
      if (affected.has(edge.source) && !affected.has(edge.target)) {
        affected.add(edge.target);
        changed = true;
      }
    }
  }
  return affected;
}

function selectedEdgeIds(root: string, edges: DagEdge[], affected: Set<string>): Set<string> {
  return new Set(
    edges
      .filter((edge) => affected.has(edge.source) && affected.has(edge.target) && (edge.source === root || affected.has(edge.source)))
      .map((edge) => `${edge.source}-${edge.target}-${edge.lag_seconds}-${edge.causal_type}`),
  );
}

export default function DependencyGraph() {
  const dag = useClusterStore((state) => state.dag);
  const pods = useClusterStore((state) => state.pods);
  const incidents = useClusterStore((state) => state.incidents);
  const [selectedPod, setSelectedPod] = useState<string | null>(null);

  const podNames = useMemo(() => {
    const names = new Set<string>(Object.keys(pods));
    dag.edges.forEach((edge) => {
      names.add(edge.source);
      names.add(edge.target);
    });
    return Array.from(names).sort();
  }, [dag.edges, pods]);

  const affected = useMemo(() => (selectedPod ? downstreamFrom(selectedPod, dag.edges) : new Set<string>()), [dag.edges, selectedPod]);
  const highlightedEdges = useMemo(
    () => (selectedPod ? selectedEdgeIds(selectedPod, dag.edges, affected) : new Set<string>()),
    [affected, dag.edges, selectedPod],
  );

  const nodes: Node<PodNodeData>[] = useMemo(
    () =>
      podNames.map((podName, index) => {
        const column = index % 4;
        const row = Math.floor(index / 4);
        const metrics = pods[podName];
        return {
          id: podName,
          type: getStatus(metrics),
          position: { x: 40 + column * 260, y: 60 + row * 170 },
          data: {
            podName,
            namespace: "default",
            status: getStatus(metrics),
            metrics,
            dimmed: selectedPod !== null && !affected.has(podName),
          },
        };
      }),
    [affected, podNames, pods, selectedPod],
  );

  const edges: Edge[] = useMemo(
    () =>
      dag.edges.map((edge) => {
        const id = `${edge.source}-${edge.target}-${edge.lag_seconds}-${edge.causal_type}`;
        const highlighted = highlightedEdges.has(id);
        return {
          id,
          source: edge.source,
          target: edge.target,
          label: `${edge.lag_seconds}s lag | ${edge.causal_strength.toFixed(2)} strength`,
          animated: true,
          className: "causal-edge",
          markerEnd: { type: MarkerType.ArrowClosed, color: highlighted ? "#dc2626" : "#ef4444" },
          style: {
            stroke: highlighted ? "#dc2626" : "#ef4444",
            strokeWidth: highlighted ? 3 : 2,
            opacity: selectedPod === null || highlighted ? 1 : 0.18,
          },
          labelBgPadding: [8, 4] as [number, number],
          labelBgBorderRadius: 4,
          labelStyle: { fill: "#7f1d1d", fontSize: 11, fontWeight: 600 },
        };
      }),
    [dag.edges, highlightedEdges, selectedPod],
  );

  const selectedMetrics = selectedPod ? pods[selectedPod] : undefined;
  const lastIncident = selectedPod
    ? incidents.find((incident) => incident.affected_pod === selectedPod || incident.causal_chain.some((item) => item.includes(selectedPod)))
    : undefined;

  return (
    <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="panel-surface panel-surface--glass overflow-hidden">
        <div className="flex flex-col gap-4 border-b border-[color:var(--border-soft)] px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="eyebrow">Live dependency and causal graph</p>
            <h2 className="text-lg font-display text-[color:var(--ink-strong)]">Cluster topology and blast radius</h2>
          </div>
          <div className="text-xs text-[color:var(--ink-soft)]">
            Updated {dag.timestamp ? new Date(dag.timestamp).toLocaleTimeString() : "Waiting for backend"}
          </div>
          <div className="legend">
            <span className="legend-item">
              <span className="legend-dot legend-dot--healthy" /> Healthy
            </span>
            <span className="legend-item">
              <span className="legend-dot legend-dot--warning" /> Warning
            </span>
            <span className="legend-item">
              <span className="legend-dot legend-dot--critical" /> Critical
            </span>
            <span className="legend-item">
              <span className="legend-dot legend-dot--causal" /> Causal edge
            </span>
          </div>
        </div>

        <div className="relative min-h-[560px]">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            minZoom={0.35}
            maxZoom={1.5}
            className="flow-surface"
            onNodeClick={(_, node) => setSelectedPod(node.id)}
            onPaneClick={() => setSelectedPod(null)}
          >
            <Background color="#d7d0c6" gap={20} />
            <MiniMap pannable zoomable nodeStrokeWidth={3} />
            <Controls />
          </ReactFlow>
          <div className="pointer-events-none absolute left-4 top-4 rounded-full border border-[color:var(--border-soft)] bg-white/80 px-3 py-1 text-[11px] text-[color:var(--ink-soft)]">
            Click a pod to highlight downstream impact.
          </div>
        </div>
      </div>

      <aside className="panel-surface panel-surface--glass p-4">
        {selectedPod ? (
          <div className="space-y-4">
            <div>
              <div className="eyebrow">Selected pod</div>
              <h2 className="mt-2 break-words text-lg font-display text-[color:var(--ink-strong)]">{selectedPod}</h2>
            </div>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-lg border border-[color:var(--border-soft)] bg-white/80 p-3">
                <dt className="text-xs text-[color:var(--ink-soft)]">CPU</dt>
                <dd className="mt-1 font-semibold text-[color:var(--ink-strong)]">{cpuDisplay(selectedMetrics)}</dd>
              </div>
              <div className="rounded-lg border border-[color:var(--border-soft)] bg-white/80 p-3">
                <dt className="text-xs text-[color:var(--ink-soft)]">Memory</dt>
                <dd className="mt-1 font-semibold text-[color:var(--ink-strong)]">{metricPercent(selectedMetrics)}%</dd>
              </div>
              <div className="rounded-lg border border-[color:var(--border-soft)] bg-white/80 p-3">
                <dt className="text-xs text-[color:var(--ink-soft)]">Working set</dt>
                <dd className="mt-1 font-semibold text-[color:var(--ink-strong)]">
                  {formatBytes(selectedMetrics?.memory_working_set)}
                </dd>
              </div>
              <div className="rounded-lg border border-[color:var(--border-soft)] bg-white/80 p-3">
                <dt className="text-xs text-[color:var(--ink-soft)]">Restarts</dt>
                <dd className="mt-1 font-semibold text-[color:var(--ink-strong)]">{selectedMetrics?.restart_count ?? 0}</dd>
              </div>
            </dl>
            <div className="rounded-lg border border-[color:var(--border-soft)] bg-white/80 p-3">
              <div className="text-xs uppercase text-[color:var(--ink-soft)]">Last anomaly</div>
              <p className="mt-2 text-sm text-[color:var(--ink-strong)]">
                {lastIncident?.root_cause ?? "No incident recorded for this pod."}
              </p>
            </div>
          </div>
        ) : (
          <div className="flex h-full min-h-[320px] items-center justify-center text-center text-sm text-[color:var(--ink-soft)]">
            <p>Select a pod to inspect metrics, restart count, and blast radius.</p>
          </div>
        )}
      </aside>
    </section>
  );
}
