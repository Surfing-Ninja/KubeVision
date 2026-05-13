from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from agents.kubepatch import KubePatchAgent
from agents.supervisor import SupervisorAgent
from causal.engine import CausalDiscoveryEngine
from causal.prometheus_client import PrometheusClient
from config import get_settings

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
supervisor_agent = SupervisorAgent(settings, prometheus, causal_engine)

INCIDENTS: dict[str, dict[str, Any]] = {}
BACKGROUND_TASKS: list[asyncio.Task[None]] = []


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


@app.get("/api/incidents")
async def list_incidents() -> dict[str, Any]:
    incidents = sorted(INCIDENTS.values(), key=lambda item: item["created_at"], reverse=True)
    return {"incidents": incidents}


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
    return {
        "total_incidents": 0,
        "fast_path_pct": 0.0,
        "grounded_path_pct": 0.0,
        "cold_path_pct": 0.0,
        "top_patterns": [],
    }


@app.post("/api/debug/test-incident")
async def create_test_incident() -> dict[str, Any]:
    incident, recommendation = await supervisor_agent.analyze_incident(
        affected_pod="frontend",
        namespace=settings.default_namespace,
    )
    supervisor_recommendation = {
        "proposed_changes": recommendation.proposed_fix,
        "confidence": recommendation.confidence,
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

    INCIDENTS[incident_id] = incident
    await connections.broadcast({"type": "new_incident", "payload": incident})
    return incident


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
