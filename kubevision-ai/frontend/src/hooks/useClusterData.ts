import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useClusterStore } from "../store/clusterStore";
import type { DagData, IncidentsResponse, MemoryStats, PodMetricsResponse } from "../types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`${path} failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function useClusterData() {
  const setDag = useClusterStore((state) => state.setDag);
  const updatePodMetrics = useClusterStore((state) => state.updatePodMetrics);
  const setIncidents = useClusterStore((state) => state.setIncidents);
  const setMemoryStats = useClusterStore((state) => state.setMemoryStats);

  const dagQuery = useQuery({
    queryKey: ["dag"],
    queryFn: () => fetchJson<DagData>("/api/dag"),
  });

  const metricsQuery = useQuery({
    queryKey: ["pod-metrics"],
    queryFn: () => fetchJson<PodMetricsResponse>("/api/metrics/pods"),
  });

  const incidentsQuery = useQuery({
    queryKey: ["incidents"],
    queryFn: () => fetchJson<IncidentsResponse>("/api/incidents"),
  });

  const memoryStatsQuery = useQuery({
    queryKey: ["memory-stats"],
    queryFn: () => fetchJson<MemoryStats>("/api/memory/stats"),
  });

  useEffect(() => {
    if (dagQuery.data) {
      setDag(dagQuery.data);
    }
  }, [dagQuery.data, setDag]);

  useEffect(() => {
    if (metricsQuery.data) {
      updatePodMetrics(metricsQuery.data.pods);
    }
  }, [metricsQuery.data, updatePodMetrics]);

  useEffect(() => {
    if (incidentsQuery.data) {
      setIncidents(incidentsQuery.data.incidents);
    }
  }, [incidentsQuery.data, setIncidents]);

  useEffect(() => {
    if (memoryStatsQuery.data) {
      setMemoryStats(memoryStatsQuery.data);
    }
  }, [memoryStatsQuery.data, setMemoryStats]);

  return {
    dagQuery,
    metricsQuery,
    incidentsQuery,
    memoryStatsQuery,
  };
}
