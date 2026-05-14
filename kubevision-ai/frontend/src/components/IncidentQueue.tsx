import { useEffect, useMemo, useRef, useState } from "react";
import { useClusterStore } from "../store/clusterStore";
import type { Incident } from "../types";

const API_BASE_URL = "http://localhost:8000";
const SIMULATION_PASS_THRESHOLD = 0.8;

function memoryBadge(incident: Incident) {
	if (incident.memory_path === "fast") {
		return { label: "Resolved using memory", tone: "fast" } as const;
	}
	if (incident.memory_path === "grounded") {
		return { label: "AI grounded in memory", tone: "grounded" } as const;
	}
	return { label: "AI cold start", tone: "cold" } as const;
}

export default function IncidentQueue() {
	const incidents = useClusterStore((state) => state.incidents);
	const setIncidents = useClusterStore((state) => state.setIncidents);
	const pushToast = useClusterStore((state) => state.pushToast);
	const [approvingId, setApprovingId] = useState<string | null>(null);
	const [openDiffId, setOpenDiffId] = useState<string | null>(null);
	const toastedPrIds = useRef(new Set<string>());
	const sorted = useMemo(
		() => [...incidents].sort((a, b) => b.created_at.localeCompare(a.created_at)),
		[incidents],
	);

	useEffect(() => {
		incidents.forEach((incident) => {
			if (incident.status === "pr_open" && incident.pr_url && !toastedPrIds.current.has(incident.id)) {
				toastedPrIds.current.add(incident.id);
				pushToast(`PR opened for ${incident.affected_pod}.`, "success");
			}
		});
	}, [incidents, pushToast]);

	const refreshIncidents = async () => {
		const response = await fetch(`${API_BASE_URL}/api/incidents`);
		if (!response.ok) {
			throw new Error(`Fetch incidents failed: ${response.status}`);
		}
		const data = (await response.json()) as { incidents: Incident[] };
		setIncidents(data.incidents);
	};

	const fetchIncident = async (incidentId: string) => {
		const response = await fetch(`${API_BASE_URL}/api/incidents/${incidentId}`);
		if (!response.ok) {
			throw new Error(`Fetch incident failed: ${response.status}`);
		}
		return (await response.json()) as Incident;
	};

	const approveIncident = async (incident: Incident) => {
		if (!incident.pr_number || approvingId) {
			return;
		}
		setApprovingId(incident.id);
		try {
			const response = await fetch(`${API_BASE_URL}/api/incidents/${incident.id}/approve-pr`, {
				method: "POST",
			});
			if (!response.ok) {
				throw new Error(`Approve failed: ${response.status}`);
			}
			await refreshIncidents();
			pushToast("PR approved successfully.", "success");
		} catch (error) {
			pushToast("PR approval failed.", "error");
		} finally {
			setApprovingId(null);
		}
	};

	const toggleDiff = async (incident: Incident, isOpen: boolean) => {
		if (isOpen) {
			setOpenDiffId(null);
			return;
		}
		if (incident.kubepatch?.yaml_diff) {
			setOpenDiffId(incident.id);
			pushToast("YAML diff loaded.", "success");
			return;
		}
		try {
			const latest = await fetchIncident(incident.id);
			setIncidents(
				incidents.map((item) => (item.id === latest.id ? latest : item)),
			);
			if (latest.kubepatch?.yaml_diff) {
				setOpenDiffId(incident.id);
				pushToast("YAML diff loaded.", "success");
			} else {
				pushToast("YAML diff not available.", "error");
			}
		} catch (error) {
			pushToast("Failed to fetch YAML diff.", "error");
		}
	};

	return (
		<section className="panel-surface panel-surface--glass p-5">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<p className="eyebrow">Active incidents</p>
					<h2 className="text-lg font-display text-[color:var(--ink-strong)]">Incident queue</h2>
				</div>
				<div className="text-xs text-[color:var(--ink-soft)]">{sorted.length} tracked</div>
			</div>

			{sorted.length === 0 ? (
				<p className="mt-4 text-sm text-[color:var(--ink-soft)]">No incidents received yet.</p>
			) : (
				<div className="mt-4 space-y-3">
					{sorted.map((incident) => {
						const badge = memoryBadge(incident);
						const matchScore = Math.round((incident.memory_match_score || 0) * 100);
						const hasDiff = Boolean(incident.kubepatch);
						const diffOpen = openDiffId === incident.id;
						const simulation = incident.simulation_result;
						const simulationPassed = Boolean(
							simulation && simulation.confidence >= SIMULATION_PASS_THRESHOLD,
						);
						return (
							<article key={incident.id} className="incident-card">
								<div className="flex flex-wrap items-start justify-between gap-3">
									<div>
										<div className="text-sm font-semibold text-[color:var(--ink-strong)]">
											{incident.affected_pod}
											<span className="text-[color:var(--ink-soft)]"> · {incident.namespace}</span>
										</div>
										<p className="mt-1 text-sm text-[color:var(--ink-soft)]">{incident.root_cause}</p>
									</div>
									<div className="flex flex-wrap items-center gap-2">
										<span className={`badge badge--${badge.tone}`}>{badge.label}</span>
										<span className="badge badge--neutral">{incident.status.replace("_", " ")}</span>
										{incident.status === "pr_open" ? (
											<span className={`badge ${simulationPassed ? "badge--pass" : "badge--neutral"}`}>
												{simulationPassed ? "Simulation passed" : "Simulation pending"}
											</span>
										) : null}
										{incident.pr_number && incident.status === "pr_open" ? (
											<button
												className="button button--primary"
												onClick={() => approveIncident(incident)}
												type="button"
												disabled={approvingId === incident.id || !simulationPassed}
											>
												{approvingId === incident.id ? "Approving..." : "Approve PR"}
											</button>
										) : null}
									</div>
								</div>

								<div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-[color:var(--ink-soft)]">
									<span>Confidence: {Math.round(incident.confidence * 100)}%</span>
									<span>Memory match: {matchScore}%</span>
									<span>Severity: {incident.severity}</span>
									{incident.pr_url ? (
										<a className="text-[color:var(--accent-teal)] underline" href={incident.pr_url}>
											View PR
										</a>
									) : null}
								</div>

								{simulation ? (
									<div className="simulation-card">
										<div className="simulation-card__header">
											<span>Simulation confidence</span>
											<strong>{Math.round(simulation.confidence * 100)}%</strong>
										</div>
										<div className="simulation-card__body">
											<div>Headroom: {simulation.headroom_pct}%</div>
											<div>Node capacity: {simulation.fits_on_node ? "PASS" : "FAIL"}</div>
											<div>Resolves OOM: {simulation.resolves_oom ? "PASS" : "CHECK"}</div>
										</div>
									</div>
								) : null}

								{hasDiff ? (
									<div className="mt-3">
										<button
											className="button button--ghost"
												onClick={() => toggleDiff(incident, diffOpen)}
											type="button"
										>
											{diffOpen ? "Hide YAML diff" : "View YAML diff"}
										</button>
										{diffOpen ? (
											<pre className="diff-block">{incident.kubepatch?.yaml_diff}</pre>
										) : null}
									</div>
								) : null}
							</article>
						);
					})}
				</div>
			)}
		</section>
	);
}
