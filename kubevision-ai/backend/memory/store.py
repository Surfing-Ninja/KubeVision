from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

from config import Settings
from memory.schemas import IncidentFingerprint, IncidentMemoryRecord, Outcome, Resolution

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryMatch:
	record: IncidentMemoryRecord
	similarity: float
	effective_confidence: float
	age_days: int


class MemoryStore:
	def __init__(self, settings: Settings, collection_name: str = "incident_memory") -> None:
		self.settings = settings
		self._lock = Lock()
		self._client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
		self._collection = self._client.get_or_create_collection(
			name=collection_name,
			metadata={"hnsw:space": "cosine"},
		)
		self._embedder = SentenceTransformer("all-MiniLM-L6-v2")

	def _embed_texts(self, texts: list[str]) -> list[list[float]]:
		embeddings = self._embedder.encode(texts, normalize_embeddings=True)
		if hasattr(embeddings, "tolist"):
			return embeddings.tolist()
		return [list(vector) for vector in embeddings]

	@staticmethod
	def _hash_causal_chain(causal_chain: list[str]) -> str:
		if not causal_chain:
			return "dag_none"
		payload = "|".join(causal_chain).encode("utf-8")
		digest = hashlib.sha256(payload).hexdigest()[:12]
		return f"dag_{digest}"

	@staticmethod
	def _record_document(record: IncidentMemoryRecord) -> str:
		fingerprint = record.fingerprint
		resolution = record.resolution
		outcome = record.outcome
		symptom_vector = ", ".join(f"{key}={value}" for key, value in fingerprint.symptom_vector.items())
		return "\n".join(
			[
				f"summary: {record.nl_summary}",
				f"affected_pod: {fingerprint.affected_pod}",
				f"namespace: {fingerprint.namespace}",
				f"error_signature: {fingerprint.error_signature}",
				f"causal_dag_hash: {fingerprint.causal_dag_hash}",
				f"symptom_vector: {symptom_vector}",
				f"resolution_action: {resolution.action_type}",
				f"resolution_change: {resolution.change_made}",
				f"simulation_confidence: {resolution.simulation_confidence}",
				f"outcome_verified: {outcome.verified}",
				f"outcome_effectiveness: {outcome.effectiveness_score}",
				f"outcome_recurrence: {outcome.recurrence_in_24h}",
			]
		)

	def _effective_confidence(self, record: IncidentMemoryRecord) -> tuple[float, int]:
		age_days = max(0, (datetime.now(timezone.utc) - record.timestamp).days)
		decay_factor = (1.0 - self.settings.memory_decay_rate) ** age_days
		effective = record.outcome.effectiveness_score * decay_factor
		return effective, age_days

	def store_record(self, record: IncidentMemoryRecord) -> None:
		document = self._record_document(record)
		embeddings = self._embed_texts([document])
		metadata = {
			"incident_id": record.incident_id,
			"timestamp": record.timestamp.isoformat(),
			"pattern": record.fingerprint.error_signature,
			"affected_pod": record.fingerprint.affected_pod,
			"namespace": record.fingerprint.namespace,
			"action_type": record.resolution.action_type,
			"change_made": record.resolution.change_made,
			"effectiveness_score": record.outcome.effectiveness_score,
			"verified": record.outcome.verified,
			"recurrence_in_24h": record.outcome.recurrence_in_24h,
			"record_json": json.dumps(record.to_dict(), default=str),
		}

		with self._lock:
			self._collection.add(
				ids=[record.incident_id],
				documents=[document],
				metadatas=[metadata],
				embeddings=embeddings,
			)

	def store_if_qualified(self, record: IncidentMemoryRecord) -> bool:
		if not record.outcome.verified:
			return False
		if record.outcome.recurrence_in_24h:
			return False
		if record.outcome.effectiveness_score < self.settings.memory_quality_threshold:
			return False
		self.store_record(record)
		return True

	def find_best_match(self, query_text: str) -> MemoryMatch | None:
		if self._collection.count() == 0:
			return None

		embeddings = self._embed_texts([query_text])
		with self._lock:
			results = self._collection.query(
				query_embeddings=embeddings,
				n_results=1,
				include=["documents", "metadatas", "distances"],
			)

		if not results.get("ids") or not results["ids"][0]:
			return None

		metadata = results["metadatas"][0][0]
		if not metadata:
			return None

		record_json = metadata.get("record_json")
		if not record_json:
			return None

		try:
			record_dict = json.loads(record_json)
			record = self._record_from_dict(record_dict)
		except json.JSONDecodeError:
			logger.warning("Memory record JSON could not be decoded")
			return None

		distance = float(results["distances"][0][0])
		similarity = max(0.0, min(1.0, 1.0 - distance))
		effective_confidence, age_days = self._effective_confidence(record)
		return MemoryMatch(record=record, similarity=similarity, effective_confidence=effective_confidence, age_days=age_days)

	def count(self) -> int:
		return self._collection.count()

	def top_patterns(self, limit: int = 5) -> list[dict[str, Any]]:
		if self._collection.count() == 0:
			return []

		with self._lock:
			results = self._collection.get(include=["metadatas"])

		patterns = [metadata.get("pattern") for metadata in results.get("metadatas", []) if metadata]
		counts = Counter(pattern for pattern in patterns if pattern)
		return [
			{"pattern": pattern, "recall_count": count}
			for pattern, count in counts.most_common(limit)
		]

	def build_record_from_incident(
		self,
		incident: dict[str, Any],
		outcome_payload: dict[str, Any],
		nl_summary: str | None = None,
	) -> IncidentMemoryRecord:
		created_at = incident.get("created_at")
		if isinstance(created_at, str):
			timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
		else:
			timestamp = datetime.now(timezone.utc)

		symptom_vector = incident.get("symptom_vector") or {"confidence": incident.get("confidence", 0.0)}
		error_signature = incident.get("error_signature") or incident.get("root_cause", "unknown")
		causal_chain = incident.get("causal_chain") or []

		fingerprint = IncidentFingerprint(
			affected_pod=incident.get("affected_pod", "unknown"),
			namespace=incident.get("namespace", "default"),
			symptom_vector=symptom_vector,
			error_signature=str(error_signature),
			causal_dag_hash=self._hash_causal_chain(causal_chain),
		)

		kubepatch = incident.get("kubepatch") or {}
		change_made = kubepatch.get("recommendation") or json.dumps(incident.get("proposed_fix", {}), default=str)
		resolution = Resolution(
			action_type="yaml_patch" if kubepatch else "recommendation",
			change_made=change_made,
			yaml_diff=kubepatch.get("yaml_diff") or "",
			pr_url=incident.get("pr_url") or kubepatch.get("pr_url"),
			simulation_confidence=float(incident.get("confidence", 0.0)),
		)

		outcome = Outcome(
			verified=bool(outcome_payload.get("verified", False)),
			time_to_resolution_mins=int(outcome_payload.get("time_to_resolution_mins", 0)),
			recurrence_in_24h=bool(outcome_payload.get("recurrence_in_24h", False)),
			effectiveness_score=float(outcome_payload.get("effectiveness_score", 0.0)),
		)

		summary = nl_summary or f"{incident.get('root_cause', 'Incident')} resolved by {change_made}."

		return IncidentMemoryRecord(
			incident_id=incident.get("id", "unknown"),
			timestamp=timestamp,
			fingerprint=fingerprint,
			resolution=resolution,
			outcome=outcome,
			nl_summary=summary,
		)

	@staticmethod
	def _record_from_dict(payload: dict[str, Any]) -> IncidentMemoryRecord:
		fingerprint_payload = payload.get("fingerprint", {})
		resolution_payload = payload.get("resolution", {})
		outcome_payload = payload.get("outcome", {})
		timestamp_value = payload.get("timestamp")
		if timestamp_value:
			timestamp = datetime.fromisoformat(str(timestamp_value).replace("Z", "+00:00"))
		else:
			timestamp = datetime.now(timezone.utc)
		return IncidentMemoryRecord(
			incident_id=str(payload.get("incident_id", "unknown")),
			timestamp=timestamp,
			fingerprint=IncidentFingerprint(
				affected_pod=str(fingerprint_payload.get("affected_pod", "unknown")),
				namespace=str(fingerprint_payload.get("namespace", "default")),
				symptom_vector=fingerprint_payload.get("symptom_vector", {}),
				error_signature=str(fingerprint_payload.get("error_signature", "unknown")),
				causal_dag_hash=str(fingerprint_payload.get("causal_dag_hash", "dag_none")),
			),
			resolution=Resolution(
				action_type=str(resolution_payload.get("action_type", "unknown")),
				change_made=str(resolution_payload.get("change_made", "")),
				yaml_diff=str(resolution_payload.get("yaml_diff", "")),
				pr_url=resolution_payload.get("pr_url"),
				simulation_confidence=float(resolution_payload.get("simulation_confidence", 0.0)),
			),
			outcome=Outcome(
				verified=bool(outcome_payload.get("verified", False)),
				time_to_resolution_mins=int(outcome_payload.get("time_to_resolution_mins", 0)),
				recurrence_in_24h=bool(outcome_payload.get("recurrence_in_24h", False)),
				effectiveness_score=float(outcome_payload.get("effectiveness_score", 0.0)),
			),
			nl_summary=str(payload.get("nl_summary", "")),
		)
