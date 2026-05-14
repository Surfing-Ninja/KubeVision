import { useMemo } from "react";
import { useClusterStore } from "../store/clusterStore";
import type { Incident } from "../types";

const MAX_ITEMS = 4;

function memoryBadge(incident: Incident) {
	if (incident.memory_path === "fast") {
		return { label: "Memory", tone: "fast" } as const;
	}
	if (incident.memory_path === "grounded") {
		return { label: "Grounded", tone: "grounded" } as const;
	}
	return { label: "Cold", tone: "cold" } as const;
}

export default function AgentInsights() {
	const incidents = useClusterStore((state) => state.incidents);
	const insights = useMemo(
		() => [...incidents].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, MAX_ITEMS),
		[incidents],
	);

	return (
		<section className="panel-surface panel-surface--glass p-5">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<p className="eyebrow">Supervisor output</p>
					<h2 className="text-lg font-display text-[color:var(--ink-strong)]">Agent insights</h2>
				</div>
				<div className="text-xs text-[color:var(--ink-soft)]">Live reasoning feed</div>
			</div>

			{insights.length === 0 ? (
				<p className="mt-4 text-sm text-[color:var(--ink-soft)]">No insights available yet.</p>
			) : (
				<div className="mt-4 space-y-3">
					{insights.map((incident) => {
						const badge = memoryBadge(incident);
						const matchScore = Math.round((incident.memory_match_score || 0) * 100);
						const simulation = incident.simulation_result;
						return (
							<article key={incident.id} className="insight-card">
								<div className="insight-card__header">
									<span className={`badge badge--${badge.tone}`}>{badge.label}</span>
									<span className="text-xs text-[color:var(--ink-soft)]">
										Confidence {Math.round(incident.confidence * 100)}%
									</span>
								</div>
								<div className="insight-card__title">{incident.affected_pod}</div>
								<p className="insight-card__summary">{incident.root_cause}</p>
								<div className="insight-card__meta">
									<span>Memory match {matchScore}%</span>
									<span>Status {incident.status.replace("_", " ")}</span>
								</div>
								{incident.causal_chain?.length ? (
									<div className="insight-card__chain">{incident.causal_chain[0]}</div>
								) : null}
								{simulation ? (
									<div className="insight-card__simulation">
										Simulation {Math.round(simulation.confidence * 100)}% · Headroom {simulation.headroom_pct}%
									</div>
								) : null}
								{incident.pr_url ? (
									<a className="insight-card__link" href={incident.pr_url}>
										View PR
									</a>
								) : null}
							</article>
						);
					})}
				</div>
			)}
		</section>
	);
}
