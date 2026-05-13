from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import grangercausalitytests

from config import get_settings
from causal.prometheus_client import PrometheusClient

logger = logging.getLogger(__name__)


class CausalDiscoveryEngine:
    """Runs PCMCI causal discovery over Prometheus pod metrics."""

    def __init__(
        self,
        prometheus_client: PrometheusClient,
        namespace: str = "default",
        window_minutes: int = 5,
        interval_seconds: int = 60,
    ) -> None:
        self.prometheus_client = prometheus_client
        self.namespace = namespace
        self.window_minutes = window_minutes
        self.interval_seconds = interval_seconds
        self._last_dag: dict[str, Any] = self._empty_dag()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def _empty_dag(self) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "edges": [],
        }

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def get_last_dag(self) -> dict[str, Any]:
        async with self._lock:
            return json.loads(json.dumps(self._last_dag))

    async def run_forever(self) -> None:
        while True:
            try:
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Causal DAG refresh failed")
            await asyncio.sleep(self.interval_seconds)

    async def refresh_once(self) -> dict[str, Any]:
        dag = await self.compute_dag()
        async with self._lock:
            self._last_dag = dag
        return dag

    async def compute_dag(self) -> dict[str, Any]:
        frame = await self.prometheus_client.fetch_window(self.namespace, self.window_minutes)
        if frame.empty:
            return self._empty_dag()

        clean = self._prepare_frame(frame)
        if len(clean) < 2 or len(clean.columns) < 2:
            return self._dataframe_to_empty_graph(clean, "insufficient_variables")

        if len(clean) < 15:
            logger.warning("Only %s data points available; falling back to Granger causality", len(clean))
            return self._run_granger(clean)

        try:
            return self._run_pcmci(clean)
        except Exception:
            logger.exception("PCMCI failed; falling back to Granger causality")
            return self._run_granger(clean)

    @staticmethod
    def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
        numeric = frame.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
        numeric = numeric.dropna(axis=1, how="all").interpolate(limit_direction="both").fillna(0.0)
        varying_columns = [column for column in numeric.columns if numeric[column].nunique(dropna=False) > 1]
        return numeric[varying_columns]

    def _dataframe_to_empty_graph(self, frame: pd.DataFrame, method: str) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "edges": [],
        }

    def _run_pcmci(self, frame: pd.DataFrame) -> dict[str, Any]:
        from tigramite import data_processing as pp
        from tigramite.independence_tests.parcorr import ParCorr
        from tigramite.pcmci import PCMCI

        dataframe = pp.DataFrame(frame.to_numpy(dtype=float), var_names=list(frame.columns))
        pcmci = PCMCI(dataframe=dataframe, cond_ind_test=ParCorr(significance="analytic"), verbosity=0)
        results = pcmci.run_pcmci(tau_max=4, pc_alpha=0.05)
        return self._results_to_dag(frame, results, method="pcmci")

    def _results_to_dag(self, frame: pd.DataFrame, results: dict[str, Any], method: str) -> dict[str, Any]:
        columns = list(frame.columns)
        q_matrix = results.get("q_matrix")
        val_matrix = results.get("val_matrix")
        graph = results.get("graph")
        edges: list[dict[str, Any]] = []

        if q_matrix is None or val_matrix is None:
            return self._dataframe_to_empty_graph(frame, method)

        for source_idx, source_column in enumerate(columns):
            for target_idx, target_column in enumerate(columns):
                if source_idx == target_idx:
                    continue
                for lag in range(1, min(4, q_matrix.shape[2] - 1) + 1):
                    q_value = float(q_matrix[source_idx, target_idx, lag])
                    strength = float(abs(val_matrix[source_idx, target_idx, lag]))
                    if not np.isfinite(q_value) or not np.isfinite(strength):
                        continue
                    if q_value > 0.05 or strength <= 0:
                        continue
                    if graph is not None:
                        marker = graph[source_idx, target_idx, lag]
                        if isinstance(marker, str) and marker == "":
                            continue
                    source_pod, source_metric = self._split_column(source_column)
                    target_pod, target_metric = self._split_column(target_column)
                    if source_pod == target_pod:
                        continue
                    edges.append(
                        {
                            "id": f"{source_column}->{target_column}@{lag}",
                            "source": source_pod,
                            "target": target_pod,
                            "source_metric": source_metric,
                            "target_metric": target_metric,
                            "lag_seconds": lag * PrometheusClient.STEP_SECONDS,
                            "causal_strength": round(strength, 4),
                            "causal_type": self._causal_type(source_metric, target_metric),
                        }
                    )

        return self._build_graph(frame, edges, method)

    def _run_granger(self, frame: pd.DataFrame) -> dict[str, Any]:
        edges: list[dict[str, Any]] = []
        columns = list(frame.columns)
        max_lag = min(4, max(1, (len(frame) // 3) - 1))

        for source_column, target_column in combinations(columns, 2):
            edges.extend(self._granger_pair_edges(frame, source_column, target_column, max_lag))
            edges.extend(self._granger_pair_edges(frame, target_column, source_column, max_lag))

        return self._build_graph(frame, edges, "granger")

    def _granger_pair_edges(
        self,
        frame: pd.DataFrame,
        source_column: str,
        target_column: str,
        max_lag: int,
    ) -> list[dict[str, Any]]:
        source_pod, source_metric = self._split_column(source_column)
        target_pod, target_metric = self._split_column(target_column)
        if source_pod == target_pod:
            return []

        pair = frame[[target_column, source_column]].to_numpy(dtype=float)
        try:
            results = grangercausalitytests(pair, maxlag=max_lag, verbose=False)
        except Exception:
            return []

        candidates: list[tuple[int, float, float]] = []
        for lag, lag_result in results.items():
            test = lag_result[0].get("ssr_ftest")
            if not test:
                continue
            statistic = float(test[0])
            p_value = float(test[1])
            if not np.isfinite(statistic) or not np.isfinite(p_value):
                continue
            if p_value <= 0.05:
                candidates.append((lag, p_value, statistic))

        if not candidates:
            return []

        best_lag, p_value, statistic = min(candidates, key=lambda item: item[1])
        strength = min(1.0, statistic / (statistic + 10.0)) if statistic > 0 else 0.0
        return [
            {
                "id": f"{source_column}->{target_column}@{best_lag}",
                "source": source_pod,
                "target": target_pod,
                "source_metric": source_metric,
                "target_metric": target_metric,
                "lag_seconds": best_lag * PrometheusClient.STEP_SECONDS,
                "causal_strength": round(strength, 4),
                "causal_type": self._causal_type(source_metric, target_metric),
            }
        ]

    def _build_graph(self, frame: pd.DataFrame, edges: list[dict[str, Any]], method: str) -> dict[str, Any]:
        deduped_edges = {edge["id"]: edge for edge in edges}
        output_edges = []
        for edge in sorted(deduped_edges.values(), key=lambda item: (item["source"], item["target"], item["lag_seconds"])):
            output_edges.append(
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "lag_seconds": edge["lag_seconds"],
                    "causal_strength": edge["causal_strength"],
                    "causal_type": edge["causal_type"],
                }
            )
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "edges": output_edges,
        }

    @staticmethod
    def _split_column(column: str) -> tuple[str, str]:
        if "__" not in column:
            return column, "unknown"
        return tuple(column.rsplit("__", 1))  # type: ignore[return-value]

    @staticmethod
    def _metric_names(frame: pd.DataFrame) -> list[str]:
        return sorted({column.rsplit("__", 1)[1] for column in frame.columns if "__" in column})

    @staticmethod
    def _causal_type(source_metric: str, target_metric: str) -> str:
        metrics = {source_metric, target_metric}
        if metrics & {"memory_working_set", "memory_limit", "oom_events", "restart_count"}:
            return "memory_pressure"
        if "network_receive" in metrics or "network_transmit" in metrics:
            return "network_congestion"
        if "cpu_usage" in metrics or "cpu_throttled" in metrics:
            return "cpu_pressure"
        if "fs_reads" in metrics or "fs_writes" in metrics:
            return "io_saturation"
        return "io_saturation"


async def _worker_main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    client = PrometheusClient(settings.prometheus_url)
    engine = CausalDiscoveryEngine(
        client,
        namespace=settings.default_namespace,
        window_minutes=settings.causal_window_minutes,
        interval_seconds=settings.causal_interval_seconds,
    )
    await engine.run_forever()


if __name__ == "__main__":
    asyncio.run(_worker_main())
