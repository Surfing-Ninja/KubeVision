import { useEffect } from "react";
import { useClusterStore } from "../store/clusterStore";
import type { LiveMessage } from "../types";

const DEFAULT_WS_URL = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws/live";

export function useWebSocket(url = DEFAULT_WS_URL): void {
  const setDag = useClusterStore((state) => state.setDag);
  const updatePodMetrics = useClusterStore((state) => state.updatePodMetrics);
  const addIncident = useClusterStore((state) => state.addIncident);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let closedByEffect = false;
    let reconnectAttempt = 0;

    const connect = () => {
      socket = new WebSocket(url);

      socket.onopen = () => {
        reconnectAttempt = 0;
      };

      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as LiveMessage;
        if (message.type === "dag_update") {
          setDag(message.payload);
        }
        if (message.type === "metric_update") {
          updatePodMetrics(message.payload.pods);
        }
        if (message.type === "new_incident") {
          addIncident(message.payload);
        }
      };

      socket.onclose = () => {
        if (closedByEffect) {
          return;
        }
        const delay = Math.min(30000, 1000 * 2 ** reconnectAttempt);
        reconnectAttempt += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();

    return () => {
      closedByEffect = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
  }, [addIncident, setDag, updatePodMetrics, url]);
}
