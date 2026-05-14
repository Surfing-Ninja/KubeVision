export type CausalType = "cpu_pressure" | "memory_pressure" | "network_congestion" | "io_saturation";

export type PodStatus = "healthy" | "warning" | "critical";

export interface DagEdge {
  source: string;
  target: string;
  lag_seconds: number;
  causal_strength: number;
  causal_type: CausalType;
}

export interface DagData {
  timestamp: string | null;
  edges: DagEdge[];
}

export interface PodMetrics {
  cpu_usage: number;
  cpu_throttled?: number;
  cpu_limit?: number;
  memory_working_set: number;
  memory_limit?: number;
  oom_events?: number;
  network_receive: number;
  network_transmit: number;
  fs_reads?: number;
  fs_writes?: number;
  restart_count: number;
}

export interface PodHistoryPoint {
  timestamp: string;
  metric: string;
  value: number;
}

export interface PodMetricsResponse {
  namespace: string;
  generated_at: string;
  pods: Record<string, PodMetrics>;
  history_window_minutes: number;
  history: Record<string, PodHistoryPoint[]>;
}

export interface SimulationResult {
  proposed_memory_limit: number | string;
  observed_peak_bytes: number;
  headroom_pct: number;
  fits_on_node: boolean;
  resolves_oom: boolean;
  confidence: number;
  downtime_expected: boolean;
}

export interface KubePatchResult {
  incident_id: string;
  action: "pr_opened" | "manual_pr_required" | "manual_review_required" | string;
  confidence: number;
  current_yaml: string | null;
  generated_yaml: string | null;
  recommendation: string;
  pr_url: string | null;
  pr_number: number | null;
  branch: string | null;
  file_path: string | null;
  label: string | null;
  yaml_diff: string | null;
}

export interface Incident {
  id: string;
  created_at: string;
  status: "open" | "analyzing" | "fix_ready" | "pr_open" | "pr_approved" | "resolved" | string;
  severity: "critical" | "high" | "medium" | "warning" | string;
  affected_pod: string;
  namespace: string;
  root_cause: string;
  causal_chain: string[];
  proposed_fix: Record<string, unknown>;
  confidence: number;
  memory_path: "fast" | "grounded" | "cold" | string;
  memory_match_score: number;
  memory_case_id: string | null;
  simulation_result: SimulationResult | null;
  pr_url: string | null;
  pr_number: number | null;
  kubepatch?: KubePatchResult;
}

export interface IncidentsResponse {
  incidents: Incident[];
  total?: number;
  limit?: number;
  offset?: number;
}

export interface TopPattern {
  pattern: string;
  recall_count: number;
}

export interface MemoryStats {
  total_incidents: number;
  fast_path_pct: number;
  grounded_path_pct: number;
  cold_path_pct: number;
  top_patterns: TopPattern[];
}

export type ToastTone = "success" | "error" | "info";

export interface Toast {
  id: string;
  message: string;
  tone: ToastTone;
}
export type LiveMessage =
  | { type: "dag_update"; payload: DagData }
  | {
      type: "metric_update";
      payload: {
        namespace: string;
        pods: Record<string, PodMetrics>;
        generated_at: string;
      };
    }
  | { type: "new_incident"; payload: Incident };
