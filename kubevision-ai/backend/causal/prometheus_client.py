from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PodMetric:
    pod: str
    metric_type: str
    value: float


class PrometheusClient:
    """Async wrapper around the Prometheus HTTP API used by the causal engine."""

    STEP_SECONDS = 15

    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _namespace_matcher(namespace: str | None) -> str:
        if namespace and namespace not in {"*", "all", ".+"}:
            escaped = namespace.replace("\\", "\\\\").replace('"', '\\"')
            return f'namespace="{escaped}"'
        return 'namespace=~".+"'

    def _queries(self, namespace: str | None) -> dict[str, str]:
        ns = self._namespace_matcher(namespace)
        return {
            "cpu_usage": f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{ns},container!="",pod!=""}}[2m]))',
            "cpu_throttled": f'sum by (pod) (rate(container_cpu_throttled_seconds_total{{{ns},container!="",pod!=""}}[2m]))',
            "memory_working_set": f'sum by (pod) (container_memory_working_set_bytes{{{ns},container!="",pod!=""}})',
            "memory_limit": f'sum by (pod) (container_memory_limit_bytes{{{ns},container!="",pod!=""}})',
            "oom_events": f'sum by (pod) (container_oom_events_total{{{ns},container!="",pod!=""}})',
            "network_receive": f'sum by (pod) (rate(container_network_receive_bytes_total{{{ns},pod!=""}}[2m]))',
            "network_transmit": f'sum by (pod) (rate(container_network_transmit_bytes_total{{{ns},pod!=""}}[2m]))',
            "fs_reads": f'sum by (pod) (rate(container_fs_reads_bytes_total{{{ns},container!="",pod!=""}}[2m]))',
            "fs_writes": f'sum by (pod) (rate(container_fs_writes_bytes_total{{{ns},container!="",pod!=""}}[2m]))',
            "restart_count": f'sum by (pod) (kube_pod_container_status_restarts_total{{{ns},pod!=""}})',
        }

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        delay = 1.0
        last_error: Exception | None = None

        for attempt in range(1, 6):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                if payload.get("status") != "success":
                    raise RuntimeError(f"Prometheus returned status={payload.get('status')}: {payload}")
                return payload["data"]
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "Prometheus request failed on attempt %s/5 for %s: %s",
                    attempt,
                    path,
                    exc,
                )
                if attempt < 5:
                    await asyncio.sleep(delay)
                    delay *= 2

        raise RuntimeError(f"Prometheus request failed after 5 attempts: {last_error}")

    async def _query_range(self, query: str, start: float, end: float) -> list[dict[str, Any]]:
        data = await self._request(
            "/api/v1/query_range",
            {
                "query": query,
                "start": f"{start:.0f}",
                "end": f"{end:.0f}",
                "step": str(self.STEP_SECONDS),
            },
        )
        return list(data.get("result", []))

    async def _query_instant(self, query: str) -> list[dict[str, Any]]:
        data = await self._request("/api/v1/query", {"query": query})
        return list(data.get("result", []))

    async def fetch_window(self, namespace: str = "default", window_minutes: int = 30) -> pd.DataFrame:
        end = time.time()
        start = end - (window_minutes * 60)
        series_by_column: dict[str, pd.Series] = {}

        for metric_type, query in self._queries(namespace).items():
            try:
                result = await self._query_range(query, start, end)
            except RuntimeError as exc:
                logger.warning("Skipping metric %s because Prometheus is unavailable: %s", metric_type, exc)
                continue

            for item in result:
                pod_name = item.get("metric", {}).get("pod")
                if not pod_name:
                    continue
                values = item.get("values", [])
                points = {
                    pd.to_datetime(float(timestamp), unit="s", utc=True): float(value)
                    for timestamp, value in values
                    if value not in {None, "NaN", "+Inf", "-Inf"}
                }
                if points:
                    series_by_column[f"{pod_name}__{metric_type}"] = pd.Series(points, dtype="float64")

        if not series_by_column:
            return pd.DataFrame()

        frame = pd.DataFrame(series_by_column).sort_index()
        frame = frame.resample(f"{self.STEP_SECONDS}s").mean().interpolate(limit_direction="both")
        return frame.fillna(0.0)

    async def fetch_current(self, namespace: str = "default") -> dict[str, dict[str, float]]:
        current: dict[str, dict[str, float]] = {}

        for metric_type, query in self._queries(namespace).items():
            try:
                result = await self._query_instant(query)
            except RuntimeError as exc:
                logger.warning("Skipping current metric %s because Prometheus is unavailable: %s", metric_type, exc)
                continue

            for item in result:
                pod_name = item.get("metric", {}).get("pod")
                value = item.get("value", [None, "0"])[1]
                if not pod_name:
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    numeric_value = 0.0
                current.setdefault(pod_name, {})[metric_type] = numeric_value

        for pod_metrics in current.values():
            for metric_type in self._queries(namespace):
                pod_metrics.setdefault(metric_type, 0.0)

        return current
