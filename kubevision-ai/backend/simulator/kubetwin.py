from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kubernetes import client, config

from causal.prometheus_client import PrometheusClient
from config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PodState:
	name: str
	cpu_request: float
	cpu_limit: float
	memory_request: int
	memory_limit: int
	replicas: int
	current_cpu_utilization: float
	current_memory_utilization: float
	restart_count: int
	oom_events: int


class KubeTwin:
	def __init__(self, settings: Settings, prometheus_client: PrometheusClient) -> None:
		self.settings = settings
		self.prometheus_client = prometheus_client
		self._kubernetes_loaded = False

	def _load_kubernetes_config(self) -> None:
		if self._kubernetes_loaded:
			return
		try:
			config.load_incluster_config()
		except config.ConfigException:
			config.load_kube_config(config_file=self.settings.kubeconfig)
		self._kubernetes_loaded = True

	def _fetch_node_capacity(self) -> dict[str, float]:
		try:
			self._load_kubernetes_config()
			core = client.CoreV1Api()
			nodes = core.list_node().items
			if not nodes:
				return {"cpu": 0.0, "memory": 0.0}
			node = nodes[0]
			cpu = self._parse_quantity(str(node.status.capacity.get("cpu", "0")), kind="cpu")
			memory = self._parse_quantity(str(node.status.capacity.get("memory", "0")), kind="memory")
			return {"cpu": cpu, "memory": memory}
		except Exception as exc:
			logger.warning("Failed to fetch node capacity: %s", exc)
			return {"cpu": 0.0, "memory": 0.0}

	@staticmethod
	def _parse_quantity(value: str, kind: str) -> float:
		if not value:
			return 0.0

		suffixes = {
			"Ki": 1024,
			"Mi": 1024 ** 2,
			"Gi": 1024 ** 3,
			"Ti": 1024 ** 4,
		}

		if kind == "memory":
			for suffix, factor in suffixes.items():
				if value.endswith(suffix):
					return float(value[: -len(suffix)]) * factor
			if value.endswith("K"):
				return float(value[:-1]) * 1000
			if value.endswith("M"):
				return float(value[:-1]) * 1000 ** 2
			if value.endswith("G"):
				return float(value[:-1]) * 1000 ** 3
			return float(value)

		if value.endswith("m"):
			return float(value[:-1]) / 1000.0
		return float(value)

	@staticmethod
	def _parse_memory_input(value: Any) -> int | None:
		if value is None:
			return None
		if isinstance(value, (int, float)):
			return int(value)
		raw = str(value).strip()
		if not raw:
			return None
		suffixes = {
			"Ki": 1024,
			"Mi": 1024 ** 2,
			"Gi": 1024 ** 3,
			"Ti": 1024 ** 4,
		}
		for suffix, factor in suffixes.items():
			if raw.endswith(suffix):
				return int(float(raw[: -len(suffix)]) * factor)
		if raw.endswith("K"):
			return int(float(raw[:-1]) * 1000)
		if raw.endswith("M"):
			return int(float(raw[:-1]) * 1000 ** 2)
		if raw.endswith("G"):
			return int(float(raw[:-1]) * 1000 ** 3)
		try:
			return int(float(raw))
		except ValueError:
			return None

	async def _sync_state(self, namespace: str) -> dict[str, PodState]:
		metrics = await self.prometheus_client.fetch_current(namespace)
		state: dict[str, PodState] = {}
		for pod_name, pod_metrics in metrics.items():
			memory_limit = int(pod_metrics.get("memory_limit", 0.0))
			memory_working_set = float(pod_metrics.get("memory_working_set", 0.0))
			cpu_limit = float(pod_metrics.get("cpu_limit", 0.0))
			cpu_usage = float(pod_metrics.get("cpu_usage", 0.0))
			memory_utilization = (memory_working_set / memory_limit) if memory_limit > 0 else 0.0
			cpu_utilization = (cpu_usage / cpu_limit) if cpu_limit > 0 else 0.0

			state[pod_name] = PodState(
				name=pod_name,
				cpu_request=cpu_limit,
				cpu_limit=cpu_limit,
				memory_request=memory_limit,
				memory_limit=memory_limit,
				replicas=1,
				current_cpu_utilization=cpu_utilization,
				current_memory_utilization=memory_utilization,
				restart_count=int(pod_metrics.get("restart_count", 0.0)),
				oom_events=int(pod_metrics.get("oom_events", 0.0)),
			)
		return state

	async def simulate_fix(self, pod_name: str, proposed_changes: dict[str, Any], namespace: str) -> dict[str, Any] | None:
		state = await self._sync_state(namespace)
		pod = state.get(pod_name)
		if not pod:
			return None

		new_limit = self._parse_memory_input(proposed_changes.get("memory_limit"))
		if new_limit is None:
			new_limit = pod.memory_limit

		if new_limit <= 0:
			return None

		observed_peak = pod.current_memory_utilization * pod.memory_limit
		headroom = (new_limit - observed_peak) / new_limit

		node_capacity = self._fetch_node_capacity()
		current_node_mem_used = sum(item.memory_limit for item in state.values())
		node_available = node_capacity["memory"] - current_node_mem_used
		delta = new_limit - pod.memory_limit
		fits_on_node = node_capacity["memory"] == 0.0 or delta <= node_available

		resolves_oom = headroom > 0.20

		confidence = min(1.0, max(0.0, headroom * 2)) * (1.0 if fits_on_node else 0.3)
		if pod.oom_events > 0 and resolves_oom:
			confidence = min(confidence * 1.2, 1.0)

		return {
			"proposed_memory_limit": new_limit,
			"observed_peak_bytes": int(observed_peak),
			"headroom_pct": round(headroom * 100, 1),
			"fits_on_node": fits_on_node,
			"resolves_oom": resolves_oom,
			"confidence": round(confidence, 2),
			"downtime_expected": False,
		}
