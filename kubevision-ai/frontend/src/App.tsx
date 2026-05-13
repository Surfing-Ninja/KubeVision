import DependencyGraph from "./components/DependencyGraph";
import IncidentQueue from "./components/IncidentQueue";
import MemoryHealth from "./components/MemoryHealth";
import ToastStack from "./components/ToastStack";
import { useClusterData } from "./hooks/useClusterData";
import { useWebSocket } from "./hooks/useWebSocket";
import { useClusterStore } from "./store/clusterStore";

type StatusTone = "positive" | "warning" | "critical";

function StatusPill({
  label,
  value,
  tone = "positive",
  helper,
}: {
  label: string;
  value: string | number;
  tone?: StatusTone;
  helper?: string;
}) {
  return (
    <div className={`stat-pill stat-pill--${tone}`}>
      <div className="stat-pill__label">{label}</div>
      <div className="stat-pill__value">{value}</div>
      {helper ? <div className="mt-1 text-[11px] text-[color:var(--ink-soft)]">{helper}</div> : null}
    </div>
  );
}

export default function App() {
  useWebSocket();
  const { dagQuery, metricsQuery } = useClusterData();
  const dag = useClusterStore((state) => state.dag);
  const pods = useClusterStore((state) => state.pods);
  const incidents = useClusterStore((state) => state.incidents);

  const openIncidents = incidents.filter((incident) => !["resolved", "pr_approved"].includes(incident.status)).length;
  const backendState = dagQuery.isError || metricsQuery.isError ? "Degraded" : "Live";
  const backendTone: StatusTone = backendState === "Live" ? "positive" : "critical";
  const incidentTone: StatusTone = openIncidents > 0 ? "warning" : "positive";
  const lastDagUpdate = dag.timestamp ? new Date(dag.timestamp).toLocaleTimeString() : "Awaiting";

  return (
    <main className="min-h-full pb-10">
      <div className="mx-auto max-w-[1440px] px-6 py-6">
        <header className="panel-surface panel-surface--glass px-6 py-6 enter-rise">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
            <div className="space-y-3">
              <p className="eyebrow">KubeVision AI</p>
              <h1 className="text-3xl font-display text-[color:var(--ink-strong)] md:text-4xl">
                Causal Control Plane
              </h1>
              <p className="max-w-xl text-sm text-[color:var(--ink-soft)]">
                Live dependency and causal graph for Kubernetes pod health, incident impact, and blast radius.
              </p>
              <div className="flex flex-wrap gap-3 text-xs text-[color:var(--ink-soft)]">
                <span className="rounded-full border border-[color:var(--border-soft)] bg-white/70 px-3 py-1">
                  K3s single node
                </span>
                <span className="rounded-full border border-[color:var(--border-soft)] bg-white/70 px-3 py-1">
                  PCMCI causal edges
                </span>
                <span className="rounded-full border border-[color:var(--border-soft)] bg-white/70 px-3 py-1">
                  WebSocket live feed
                </span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatusPill label="Backend" value={backendState} tone={backendTone} helper={`DAG ${lastDagUpdate}`} />
              <StatusPill label="Pods" value={Object.keys(pods).length} tone="positive" helper="Observed" />
              <StatusPill label="Causal edges" value={dag.edges.length} tone="warning" helper="Active links" />
              <StatusPill label="Open incidents" value={openIncidents} tone={incidentTone} helper="Needs review" />
            </div>
          </div>
        </header>

        <div className="mt-6 enter-rise enter-delay-1">
          <DependencyGraph />
        </div>

        <div className="mt-6 grid gap-6 enter-rise enter-delay-2 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <IncidentQueue />
          <MemoryHealth />
        </div>
      </div>
      <ToastStack />
    </main>
  );
}
