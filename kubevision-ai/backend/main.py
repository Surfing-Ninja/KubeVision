from __future__ import annotations

import asyncio
import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from agents.kubepatch import KubePatchAgent
from agents.supervisor import SupervisorAgent
from causal.engine import CausalDiscoveryEngine
from causal.prometheus_client import PrometheusClient
from config import get_settings
from memory.store import MemoryStore
from simulator.kubetwin import KubeTwin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            connections = list(self._connections)

        stale: list[WebSocket] = []
        for websocket in connections:
            try:
                await websocket.send_json(message)
            except Exception:
                stale.append(websocket)

        if stale:
            async with self._lock:
                for websocket in stale:
                    self._connections.discard(websocket)


settings = get_settings()
prometheus = PrometheusClient(settings.prometheus_url)
causal_engine = CausalDiscoveryEngine(
    prometheus,
    namespace=settings.default_namespace,
    window_minutes=settings.causal_window_minutes,
    interval_seconds=settings.causal_interval_seconds,
)
connections = ConnectionManager()
kubepatch_agent = KubePatchAgent(settings)
memory_store = MemoryStore(settings)
supervisor_agent = SupervisorAgent(settings, prometheus, causal_engine, memory_store)
kube_twin = KubeTwin(settings, prometheus)

INCIDENTS: dict[str, dict[str, Any]] = {}
BACKGROUND_TASKS: list[asyncio.Task[None]] = []

logger.warning("INCIDENTS store is in-memory only. Pod restart will clear all incidents.")


async def dag_broadcast_loop() -> None:
    while True:
        try:
            dag = await causal_engine.refresh_once()
            await connections.broadcast({"type": "dag_update", "payload": dag})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("DAG broadcast loop failed")
        await asyncio.sleep(settings.causal_interval_seconds)


async def metric_broadcast_loop() -> None:
    while True:
        try:
            metrics = await prometheus.fetch_current(settings.default_namespace)
            await connections.broadcast(
                {
                    "type": "metric_update",
                    "payload": {
                        "namespace": settings.default_namespace,
                        "pods": metrics,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Metric broadcast loop failed")
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(_: FastAPI):
    BACKGROUND_TASKS.append(asyncio.create_task(dag_broadcast_loop()))
    BACKGROUND_TASKS.append(asyncio.create_task(metric_broadcast_loop()))
    yield
    for task in BACKGROUND_TASKS:
        task.cancel()
    await asyncio.gather(*BACKGROUND_TASKS, return_exceptions=True)


app = FastAPI(title="KubeVision AI Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dag")
async def get_dag() -> dict[str, Any]:
    return await causal_engine.get_last_dag()


@app.get("/api/metrics/pods")
async def get_pod_metrics(namespace: str | None = None) -> dict[str, Any]:
    selected_namespace = namespace or settings.default_namespace
    metrics = await prometheus.fetch_current(selected_namespace)
    history_frame = await prometheus.fetch_window(selected_namespace, window_minutes=30)
    history: dict[str, list[dict[str, Any]]] = {}

    for timestamp, row in history_frame.iterrows():
        timestamp_value = timestamp.isoformat()
        for column, value in row.items():
            if "__" not in column:
                continue
            pod_name, metric_type = column.rsplit("__", 1)
            history.setdefault(pod_name, []).append(
                {
                    "timestamp": timestamp_value,
                    "metric": metric_type,
                    "value": float(value),
                }
            )

    return {
        "namespace": selected_namespace,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pods": metrics,
        "history_window_minutes": 30,
        "history": history,
    }


@app.post("/api/simulate")
async def simulate_fix(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    pod_name = payload.get("pod_name") or payload.get("pod") or payload.get("affected_pod")
    if not pod_name:
        raise HTTPException(status_code=422, detail="pod_name is required")

    proposed_changes = (
        payload.get("proposed_changes")
        or payload.get("proposed_fix")
        or payload.get("patch")
        or {}
    )
    if not isinstance(proposed_changes, dict):
        raise HTTPException(status_code=422, detail="proposed_changes must be an object")

    namespace = payload.get("namespace") or settings.default_namespace
    simulation_result = await kube_twin.simulate_fix(pod_name, proposed_changes, namespace)
    if not simulation_result:
        raise HTTPException(status_code=404, detail="Simulation could not run for the requested pod")

    return {
        "pod_name": pod_name,
        "namespace": namespace,
        "proposed_changes": proposed_changes,
        "simulation_result": simulation_result,
    }


@app.get("/api/incidents")
async def list_incidents(
    status: str | None = None,
    severity: str | None = None,
    namespace: str | None = None,
    affected_pod: str | None = None,
    memory_path: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort: str = "desc",
) -> dict[str, Any]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be 0 or greater")
    if sort not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="sort must be asc or desc")

    incidents = list(INCIDENTS.values())
    if status:
        incidents = [item for item in incidents if item.get("status") == status]
    if severity:
        incidents = [item for item in incidents if item.get("severity") == severity]
    if namespace:
        incidents = [item for item in incidents if item.get("namespace") == namespace]
    if affected_pod:
        incidents = [item for item in incidents if item.get("affected_pod") == affected_pod]
    if memory_path:
        incidents = [item for item in incidents if item.get("memory_path") == memory_path]

    incidents.sort(key=lambda item: item["created_at"], reverse=(sort == "desc"))
    total = len(incidents)
    window = incidents[offset : offset + limit]
    return {
        "incidents": window,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str) -> dict[str, Any]:
    incident = INCIDENTS.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@app.post("/api/incidents/{incident_id}/approve-pr")
async def approve_pr(incident_id: str) -> dict[str, Any]:
    incident = INCIDENTS.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    pr_number = incident.get("pr_number")
    if not pr_number:
        raise HTTPException(status_code=409, detail="Incident does not have an open PR to approve")

    try:
        review_url = await asyncio.to_thread(kubepatch_agent.approve_pr, int(pr_number))
    except Exception as exc:
        logger.exception("GitHub PR approval failed")
        raise HTTPException(status_code=502, detail=f"GitHub PR approval failed: {exc}") from exc

    incident["status"] = "pr_approved"
    incident["approved_at"] = datetime.now(timezone.utc).isoformat()
    incident["review_url"] = review_url
    return {
        "incident_id": incident_id,
        "status": incident["status"],
        "pr_url": incident.get("pr_url"),
        "review_url": review_url,
    }


@app.get("/api/memory/stats")
async def get_memory_stats() -> dict[str, Any]:
    routing_counts = Counter(
        incident.get("memory_path", "cold") for incident in INCIDENTS.values()
    )
    total_routed = sum(routing_counts.values())
    fast_pct = (routing_counts.get("fast", 0) / total_routed * 100.0) if total_routed else 0.0
    grounded_pct = (routing_counts.get("grounded", 0) / total_routed * 100.0) if total_routed else 0.0
    cold_pct = (routing_counts.get("cold", 0) / total_routed * 100.0) if total_routed else 0.0

    return {
        "total_incidents": memory_store.count(),
        "fast_path_pct": round(fast_pct, 1),
        "grounded_path_pct": round(grounded_pct, 1),
        "cold_path_pct": round(cold_pct, 1),
        "top_patterns": memory_store.top_patterns(),
    }


@app.post("/api/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    incident = INCIDENTS.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    record = memory_store.build_record_from_incident(
        incident,
        payload,
        payload.get("nl_summary"),
    )
    stored = memory_store.store_if_qualified(record)

    incident["status"] = "resolved"
    incident["resolved_at"] = datetime.now(timezone.utc).isoformat()
    incident["memory_stored"] = stored
    return {
        "incident_id": incident_id,
        "status": incident["status"],
        "memory_stored": stored,
    }


@app.post("/api/debug/test-incident")
async def create_test_incident() -> dict[str, Any]:
    incident, recommendation = await supervisor_agent.analyze_incident(
        affected_pod="frontend",
        namespace=settings.default_namespace,
    )
    simulation_result = await kube_twin.simulate_fix(
        incident["affected_pod"],
        recommendation.proposed_fix,
        incident["namespace"],
    )
    incident["simulation_result"] = simulation_result
    simulation_confidence = (
        float(simulation_result.get("confidence")) if simulation_result else recommendation.confidence
    )
    incident["confidence"] = simulation_confidence
    supervisor_recommendation = {
        "proposed_changes": recommendation.proposed_fix,
        "confidence": simulation_confidence,
        "root_cause": recommendation.root_cause,
        "causal_chain": recommendation.causal_chain,
    }
    try:
        patch_result = await kubepatch_agent.generate_and_pr(incident, supervisor_recommendation)
        incident["kubepatch"] = patch_result.to_dict()
        incident["pr_url"] = patch_result.pr_url
        incident["pr_number"] = patch_result.pr_number
        if patch_result.pr_url:
            incident["status"] = "pr_open"
        elif patch_result.generated_yaml:
            incident["status"] = "fix_ready"
    except Exception as exc:
        logger.exception("Debug incident KubePatch flow failed")
        raise HTTPException(status_code=502, detail=f"KubePatch flow failed: {exc}") from exc

    incident_id = incident["id"]
    INCIDENTS[incident_id] = incident
    await connections.broadcast({"type": "new_incident", "payload": incident})
    return incident


@app.post("/api/debug/clear-incidents")
async def clear_incidents() -> dict[str, Any]:
    cleared = len(INCIDENTS)
    INCIDENTS.clear()
    return {"cleared": cleared}


@app.post("/api/debug/seed-memory")
async def seed_memory_case(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = payload or {}
    incident_id = payload.get("incident_id")
    if incident_id:
        incident = INCIDENTS.get(str(incident_id))
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")
    else:
        incident = {
            "id": f"demo-{uuid4().hex[:8]}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "resolved",
            "severity": "high",
            "affected_pod": payload.get("affected_pod", "frontend"),
            "namespace": payload.get("namespace", settings.default_namespace),
            "root_cause": payload.get("root_cause", "Memory pressure causing OOMKilled events"),
            "causal_chain": payload.get(
                "causal_chain",
                ["frontend -> checkoutservice (75s, memory_pressure)"],
            ),
            "proposed_fix": payload.get("proposed_fix", {"memory_limit": "2Gi"}),
            "confidence": payload.get("confidence", 0.92),
            "memory_path": "fast",
            "memory_match_score": 0.98,
            "memory_case_id": None,
            "symptom_vector": payload.get(
                "symptom_vector",
                {
                    "cpu_spike_ratio": 2.8,
                    "memory_pressure": 0.93,
                    "restart_count_delta": 2,
                    "causal_source": "frontend",
                },
            ),
            "error_signature": payload.get("error_signature", "OOMKilled exit code 137"),
            "simulation_result": None,
            "pr_url": None,
            "pr_number": None,
        }

    outcome_payload = {
        "verified": payload.get("verified", True),
        "time_to_resolution_mins": payload.get("time_to_resolution_mins", 4),
        "recurrence_in_24h": payload.get("recurrence_in_24h", False),
        "effectiveness_score": payload.get("effectiveness_score", 0.97),
    }
    record = memory_store.build_record_from_incident(
        incident,
        outcome_payload,
        payload.get(
            "nl_summary",
            "OOMKilled in checkout-service caused by memory pressure. Resolved by increasing memory limit.",
        ),
    )
    stored = memory_store.store_if_qualified(record)

    return {
        "incident_id": record.incident_id,
        "stored": stored,
        "total_incidents": memory_store.count(),
    }


@app.websocket("/ws/live")
async def live_websocket(websocket: WebSocket) -> None:
    await connections.connect(websocket)
    try:
        await websocket.send_json({"type": "dag_update", "payload": await causal_engine.get_last_dag()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await connections.disconnect(websocket)
