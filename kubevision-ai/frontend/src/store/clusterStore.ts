import { create } from "zustand";
import type { DagData, Incident, MemoryStats, PodMetrics } from "../types";

interface ClusterState {
  dag: DagData;
  pods: Record<string, PodMetrics>;
  incidents: Incident[];
  memoryStats: MemoryStats;
  setDag: (dag: DagData) => void;
  updatePodMetrics: (pods: Record<string, PodMetrics>) => void;
  addIncident: (incident: Incident) => void;
  setIncidents: (incidents: Incident[]) => void;
  setMemoryStats: (memoryStats: MemoryStats) => void;
}

const emptyMemoryStats: MemoryStats = {
  total_incidents: 0,
  fast_path_pct: 0,
  grounded_path_pct: 0,
  cold_path_pct: 0,
  top_patterns: [],
};

export const useClusterStore = create<ClusterState>((set) => ({
  dag: { timestamp: null, edges: [] },
  pods: {},
  incidents: [],
  memoryStats: emptyMemoryStats,
  setDag: (dag) => set({ dag }),
  updatePodMetrics: (pods) =>
    set((state) => ({
      pods: {
        ...state.pods,
        ...pods,
      },
    })),
  addIncident: (incident) =>
    set((state) => {
      const existing = state.incidents.filter((item) => item.id !== incident.id);
      return { incidents: [incident, ...existing] };
    }),
  setIncidents: (incidents) => set({ incidents }),
  setMemoryStats: (memoryStats) => set({ memoryStats }),
}));
