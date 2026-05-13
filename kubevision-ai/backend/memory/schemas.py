from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class IncidentFingerprint:
    affected_pod: str
    namespace: str
    symptom_vector: dict[str, float | int | str]
    error_signature: str
    causal_dag_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Resolution:
    action_type: str
    change_made: str
    yaml_diff: str
    pr_url: str | None
    simulation_confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Outcome:
    verified: bool
    time_to_resolution_mins: int
    recurrence_in_24h: bool
    effectiveness_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IncidentMemoryRecord:
    incident_id: str
    timestamp: datetime
    fingerprint: IncidentFingerprint
    resolution: Resolution
    outcome: Outcome
    nl_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "timestamp": self.timestamp.isoformat(),
            "fingerprint": self.fingerprint.to_dict(),
            "resolution": self.resolution.to_dict(),
            "outcome": self.outcome.to_dict(),
            "nl_summary": self.nl_summary,
        }
