from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
from mistralai import Mistral

from causal.engine import CausalDiscoveryEngine
from causal.prometheus_client import PrometheusClient
from config import Settings
from memory.store import MemoryMatch, MemoryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SupervisorRecommendation:
	root_cause: str
	causal_chain: list[str]
	proposed_fix: dict[str, Any]
	confidence: float
	confidence_rationale: str
	memory_path: str = "cold"
	memory_match_score: float = 0.0
	memory_case_id: str | None = None

	def to_dict(self) -> dict[str, Any]:
		return {
			"root_cause": self.root_cause,
			"causal_chain": self.causal_chain,
			"proposed_fix": self.proposed_fix,
			"confidence": self.confidence,
			"confidence_rationale": self.confidence_rationale,
			"memory_path": self.memory_path,
			"memory_match_score": self.memory_match_score,
			"memory_case_id": self.memory_case_id,
		}


class SupervisorAgent:
	"""MVP supervisor that synthesizes metrics and DAG data into a fix proposal."""

	def __init__(
		self,
		settings: Settings,
		prometheus_client: PrometheusClient,
		causal_engine: CausalDiscoveryEngine,
		memory_store: MemoryStore,
	) -> None:
		self.settings = settings
		self.prometheus_client = prometheus_client
		self.causal_engine = causal_engine
		self.memory_store = memory_store

	async def analyze_incident(self, affected_pod: str, namespace: str) -> tuple[dict[str, Any], SupervisorRecommendation]:
		evidence = await self._collect_evidence(affected_pod, namespace)
		recommendation = await self._generate_recommendation(evidence)
		incident = {
			"id": str(uuid4()),
			"created_at": datetime.now(timezone.utc).isoformat(),
			"status": "open",
			"severity": evidence["severity"],
			"affected_pod": affected_pod,
			"namespace": namespace,
			"root_cause": recommendation.root_cause,
			"causal_chain": recommendation.causal_chain,
			"proposed_fix": recommendation.proposed_fix,
			"confidence": recommendation.confidence,
			"memory_path": recommendation.memory_path,
			"memory_match_score": recommendation.memory_match_score,
			"memory_case_id": recommendation.memory_case_id,
			"symptom_vector": evidence.get("symptom_vector", {}),
			"error_signature": evidence.get("error_signature", "unknown"),
			"simulation_result": None,
			"pr_url": None,
			"pr_number": None,
		}
		return incident, recommendation

	async def _collect_evidence(self, affected_pod: str, namespace: str) -> dict[str, Any]:
		current_metrics = await self.prometheus_client.fetch_current(namespace)
		history_frame = await self.prometheus_client.fetch_window(namespace, window_minutes=30)
		dag = await self.causal_engine.get_last_dag()
		if not dag.get("edges"):
			try:
				dag = await self.causal_engine.refresh_once()
			except Exception:
				logger.exception("Supervisor failed to refresh DAG; using last cached value")

		anomalies = self._detect_anomalies(history_frame, affected_pod)
		severity = self._severity_from_anomalies(anomalies)
		causal_chain = self._derive_causal_chain(dag, affected_pod)
		pod_metrics = current_metrics.get(affected_pod, {})

		symptom_vector = self._build_symptom_vector(pod_metrics, anomalies, causal_chain)
		error_signature = self._derive_error_signature(pod_metrics, anomalies)

		return {
			"affected_pod": affected_pod,
			"namespace": namespace,
			"metrics": pod_metrics,
			"anomalies": anomalies,
			"causal_chain": causal_chain,
			"dag": dag,
			"severity": severity,
			"symptom_vector": symptom_vector,
			"error_signature": error_signature,
		}

	async def _generate_recommendation(self, evidence: dict[str, Any]) -> SupervisorRecommendation:
		memory_match = await asyncio.to_thread(self._find_memory_match, evidence)
		memory_path = self._select_memory_path(memory_match)

		if memory_match and memory_path == "fast":
			return self._recommend_from_memory(memory_match)

		if not self.settings.mistral_api_key:
			fallback = self._fallback_recommendation(evidence)
			return SupervisorRecommendation(
				root_cause=fallback.root_cause,
				causal_chain=fallback.causal_chain,
				proposed_fix=fallback.proposed_fix,
				confidence=fallback.confidence,
				confidence_rationale=fallback.confidence_rationale,
				memory_path=memory_path,
				memory_match_score=memory_match.similarity if memory_match else 0.0,
				memory_case_id=memory_match.record.incident_id if memory_match else None,
			)

		prompt = self._build_prompt(evidence, memory_match, memory_path)
		response = await asyncio.to_thread(self._call_mistral, prompt)
		try:
			payload = json.loads(response)
		except json.JSONDecodeError:
			logger.warning("Supervisor returned non-JSON response; falling back to heuristic output")
			fallback = self._fallback_recommendation(evidence)
			return SupervisorRecommendation(
				root_cause=fallback.root_cause,
				causal_chain=fallback.causal_chain,
				proposed_fix=fallback.proposed_fix,
				confidence=fallback.confidence,
				confidence_rationale=fallback.confidence_rationale,
				memory_path=memory_path,
				memory_match_score=memory_match.similarity if memory_match else 0.0,
				memory_case_id=memory_match.record.incident_id if memory_match else None,
			)

		root_cause = str(payload.get("root_cause", "Insufficient evidence for root cause")).strip()
		causal_chain = payload.get("causal_chain", [])
		if not isinstance(causal_chain, list):
			causal_chain = [str(causal_chain)]
		proposed_fix = payload.get("proposed_fix", {})
		if not isinstance(proposed_fix, dict):
			proposed_fix = {"recommendation": str(proposed_fix)}
		confidence = self._clamp_confidence(payload.get("confidence"))
		rationale = str(payload.get("confidence_rationale", ""))

		return SupervisorRecommendation(
			root_cause=root_cause,
			causal_chain=[str(item) for item in causal_chain],
			proposed_fix=proposed_fix,
			confidence=confidence,
			confidence_rationale=rationale,
			memory_path=memory_path,
			memory_match_score=memory_match.similarity if memory_match else 0.0,
			memory_case_id=memory_match.record.incident_id if memory_match else None,
		)

	def _call_mistral(self, prompt: str) -> str:
		client = Mistral(api_key=self.settings.mistral_api_key)
		response = client.chat.complete(
			model="mistral-medium-latest",
			messages=[{"role": "user", "content": prompt}],
			max_tokens=2048,
			temperature=0.1,
		)
		content = response.choices[0].message.content
		if not isinstance(content, str) or not content.strip():
			raise RuntimeError("Supervisor received empty response from Mistral")
		return content.strip()

	def _build_prompt(
		self,
		evidence: dict[str, Any],
		memory_match: MemoryMatch | None,
		memory_path: str,
	) -> str:
		system_prompt = (
			"You are a Senior SRE Agent operating inside a Kubernetes cluster.\n"
			"You have access to real-time metrics, causal graphs, and verified incident evidence.\n"
			"Your job is root cause analysis and fix generation for Kubernetes workloads.\n\n"
			"NON-NEGOTIABLE RULES:\n"
			"1. Ground every claim in the evidence provided. Do not invent metrics, logs, pods, or fixes.\n"
			"2. If evidence is insufficient, say so explicitly and lower confidence.\n"
			"3. Always describe the causal chain before proposing a fix.\n"
			"4. Proposed fixes must be specific and minimal (single change where possible).\n"
			"5. If confidence < 0.60, clearly state that human review is required.\n"
			"6. Output MUST be valid JSON only. No prose, no markdown, no extra keys.\n"
			"7. Required keys: root_cause, causal_chain, proposed_fix, confidence, confidence_rationale.\n\n"
			"OUTPUT SPEC:\n"
			"root_cause: string, evidence-based sentence.\n"
			"causal_chain: array of strings, ordered cause -> effect.\n"
			"proposed_fix: object with minimal change (e.g., {\"memory_limit\": \"2Gi\"}).\n"
			"confidence: number between 0.0 and 1.0.\n"
			"confidence_rationale: short sentence that cites evidence.\n"
		)

		dag_edges = evidence.get("dag", {}).get("edges", [])
		sorted_edges = sorted(dag_edges, key=lambda edge: edge.get("causal_strength", 0), reverse=True)
		condensed_edges = [
			{
				"source": edge.get("source"),
				"target": edge.get("target"),
				"lag_seconds": edge.get("lag_seconds"),
				"causal_strength": edge.get("causal_strength"),
				"causal_type": edge.get("causal_type"),
			}
			for edge in sorted_edges[:12]
		]

		memory_context = None
		if memory_match and memory_path in {"grounded", "fast"}:
			memory_context = {
				"memory_case_id": memory_match.record.incident_id,
				"similarity": round(memory_match.similarity, 3),
				"effective_confidence": round(memory_match.effective_confidence, 3),
				"age_days": memory_match.age_days,
				"fingerprint": memory_match.record.fingerprint.to_dict(),
				"resolution": memory_match.record.resolution.to_dict(),
				"nl_summary": memory_match.record.nl_summary,
			}

		evidence_summary = {
			"affected_pod": evidence.get("affected_pod"),
			"namespace": evidence.get("namespace"),
			"severity": evidence.get("severity"),
			"metrics": evidence.get("metrics", {}),
			"anomalies": evidence.get("anomalies", [])[:8],
			"causal_chain": evidence.get("causal_chain", []),
			"dag_top_edges": condensed_edges,
			"error_signature": evidence.get("error_signature"),
			"symptom_vector": evidence.get("symptom_vector"),
			"memory_context": memory_context,
		}

		payload = json.dumps(evidence_summary, indent=2, default=str)
		return (
			f"{system_prompt}\n"
			"If memory_context is provided, ground the recommendation in that case and avoid inventing a new approach.\n"
			"Return ONLY valid JSON with the required keys.\n"
			"Evidence summary:\n"
			f"{payload}\n"
		)

	def _find_memory_match(self, evidence: dict[str, Any]) -> MemoryMatch | None:
		query_text = self._build_memory_query_text(evidence)
		return self.memory_store.find_best_match(query_text)

	@staticmethod
	def _select_memory_path(memory_match: MemoryMatch | None) -> str:
		if not memory_match:
			return "cold"
		if memory_match.similarity >= 0.90 and memory_match.effective_confidence >= 0.85:
			return "fast"
		if memory_match.similarity >= 0.70 and memory_match.effective_confidence >= 0.60:
			return "grounded"
		return "cold"

	def _recommend_from_memory(self, memory_match: MemoryMatch) -> SupervisorRecommendation:
		record = memory_match.record
		proposed_fix = {"change_made": record.resolution.change_made}
		confidence = min(1.0, max(0.0, record.outcome.effectiveness_score))
		return SupervisorRecommendation(
			root_cause=record.nl_summary,
			causal_chain=[
				f"Memory case {record.incident_id} referenced for {record.fingerprint.affected_pod}"
			],
			proposed_fix=proposed_fix,
			confidence=confidence,
			confidence_rationale=(
				"Resolved using verified memory case with "
				f"similarity {memory_match.similarity:.2f} and effective confidence "
				f"{memory_match.effective_confidence:.2f}."
			),
			memory_path="fast",
			memory_match_score=memory_match.similarity,
			memory_case_id=record.incident_id,
		)

	@staticmethod
	def _build_memory_query_text(evidence: dict[str, Any]) -> str:
		anomalies = evidence.get("anomalies", [])
		anomaly_summary = ", ".join(
			f"{item.get('metric')} z={item.get('z_score'):.2f}" for item in anomalies[:6]
		)
		symptom_vector = evidence.get("symptom_vector", {})
		symptom_summary = ", ".join(f"{key}={value}" for key, value in symptom_vector.items())
		chain_summary = "; ".join(evidence.get("causal_chain", [])[:3])
		return "\n".join(
			[
				f"affected_pod: {evidence.get('affected_pod')}",
				f"namespace: {evidence.get('namespace')}",
				f"severity: {evidence.get('severity')}",
				f"error_signature: {evidence.get('error_signature')}",
				f"symptom_vector: {symptom_summary}",
				f"anomalies: {anomaly_summary}",
				f"causal_chain: {chain_summary}",
			]
		)

	@staticmethod
	def _build_symptom_vector(
		metrics: dict[str, Any],
		anomalies: list[dict[str, Any]],
		causal_chain: list[str],
	) -> dict[str, Any]:
		vector: dict[str, Any] = {}
		cpu_limit = metrics.get("cpu_limit") or 0
		cpu_usage = metrics.get("cpu_usage") or 0
		memory_limit = metrics.get("memory_limit") or 0
		memory_working_set = metrics.get("memory_working_set") or 0
		if cpu_limit:
			vector["cpu_spike_ratio"] = round(cpu_usage / cpu_limit, 3)
		if memory_limit:
			vector["memory_pressure"] = round(memory_working_set / memory_limit, 3)
		vector["restart_count_delta"] = int(metrics.get("restart_count", 0))

		if causal_chain:
			first = causal_chain[0].split("->", 1)[0].strip()
			if first:
				vector["causal_source"] = first

		if anomalies:
			max_anomaly = max(anomalies, key=lambda item: abs(item.get("z_score", 0)))
			vector["top_anomaly_metric"] = max_anomaly.get("metric")
			vector["top_anomaly_z"] = round(float(max_anomaly.get("z_score", 0.0)), 2)

		return vector

	@staticmethod
	def _derive_error_signature(metrics: dict[str, Any], anomalies: list[dict[str, Any]]) -> str:
		if metrics.get("oom_events", 0):
			return "OOMKilled exit code 137"
		for anomaly in anomalies:
			metric = str(anomaly.get("metric", ""))
			if "memory" in metric:
				return "memory_pressure"
			if "cpu" in metric:
				return "cpu_pressure"
			if "fs" in metric or "io" in metric:
				return "io_saturation"
			if "network" in metric:
				return "network_congestion"
		return "unknown"

	@staticmethod
	def _clamp_confidence(value: Any) -> float:
		try:
			numeric = float(value)
		except (TypeError, ValueError):
			return 0.5
		return max(0.0, min(1.0, numeric))

	def _fallback_recommendation(self, evidence: dict[str, Any]) -> SupervisorRecommendation:
		anomalies = evidence.get("anomalies", [])
		if anomalies:
			top = max(anomalies, key=lambda item: abs(item.get("z_score", 0)))
			root = f"Anomalous {top.get('metric')} detected for {evidence['affected_pod']}"
		else:
			root = f"No strong anomaly detected for {evidence['affected_pod']}"

		proposed_fix: dict[str, Any] = {}
		if "memory_working_set" in root:
			proposed_fix = {"memory_limit": "2Gi"}
		elif "cpu_usage" in root:
			proposed_fix = {"cpu_limit": "1"}

		return SupervisorRecommendation(
			root_cause=root,
			causal_chain=evidence.get("causal_chain", []),
			proposed_fix=proposed_fix,
			confidence=0.55,
			confidence_rationale="Fallback heuristic due to invalid LLM output.",
		)

	@staticmethod
	def _detect_anomalies(frame: pd.DataFrame, affected_pod: str) -> list[dict[str, Any]]:
		if frame.empty:
			return []
		anomalies: list[dict[str, Any]] = []
		pod_columns = [col for col in frame.columns if col.startswith(f"{affected_pod}__")]
		for column in pod_columns:
			series = frame[column].astype(float)
			if series.empty:
				continue
			mean = series.mean()
			std = series.std(ddof=0)
			if std == 0 or np.isnan(std):
				continue
			current = float(series.iloc[-1])
			z_score = (current - mean) / std
			if abs(z_score) >= 3:
				metric = column.rsplit("__", 1)[-1]
				anomalies.append(
					{
						"metric": metric,
						"current_value": current,
						"baseline_mean": float(mean),
						"z_score": float(z_score),
					}
				)
		return anomalies

	@staticmethod
	def _severity_from_anomalies(anomalies: list[dict[str, Any]]) -> str:
		if not anomalies:
			return "medium"
		max_score = max(abs(item.get("z_score", 0)) for item in anomalies)
		if max_score >= 6:
			return "critical"
		if max_score >= 4:
			return "high"
		return "medium"

	@staticmethod
	def _derive_causal_chain(dag: dict[str, Any], affected_pod: str) -> list[str]:
		edges = dag.get("edges", [])
		incoming = [edge for edge in edges if edge.get("target") == affected_pod]
		incoming.sort(key=lambda item: item.get("causal_strength", 0), reverse=True)
		chain = [
			f"{edge.get('source')} -> {edge.get('target')} ({edge.get('lag_seconds')}s, {edge.get('causal_type')})"
			for edge in incoming[:3]
		]
		return chain or [f"No upstream causal edges detected for {affected_pod}."]
