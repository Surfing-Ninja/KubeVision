import { useMemo } from "react";
import { useClusterStore } from "../store/clusterStore";
import type { Incident } from "../types";

const MAX_ITEMS = 10;

function memoryBadge(incident: Incident) {
	if (incident.memory_path === "fast") {
		return { label: "Memory", tone: "fast" } as const;
	}
	if (incident.memory_path === "grounded") {
		return { label: "Grounded", tone: "grounded" } as const;
	}
	return { label: "Cold", tone: "cold" } as const;
}

function severityTone(severity: string) {
	if (severity === "critical") {
		return "critical";
	}
	if (severity === "high") {
		return "high";
	}
	if (severity === "warning") {
		return "warning";
	}
	return "medium";
}

function formatRelativeTime(value: string): string {
	const timestamp = new Date(value).getTime();
	if (Number.isNaN(timestamp)) {
		return "unknown";
	}
	const deltaSeconds = Math.floor((Date.now() - timestamp) / 1000);
	if (deltaSeconds < 60) {
		return "just now";
	}
	const deltaMinutes = Math.floor(deltaSeconds / 60);
	if (deltaMinutes < 60) {
		return `${deltaMinutes}m ago`;
	}
	const deltaHours = Math.floor(deltaMinutes / 60);
	return `${deltaHours}h ago`;
}

export default function AnomalyTimeline() {
	const incidents = useClusterStore((state) => state.incidents);
	const items = useMemo(() => {
		const sorted = [...incidents].sort(
			(a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
		);
		return sorted.slice(-MAX_ITEMS);
	}, [incidents]);

	return (
		<section className="panel-surface panel-surface--glass p-5">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<p className="eyebrow">Anomaly timeline</p>
					<h2 className="text-lg font-display text-[color:var(--ink-strong)]">Recent incident spikes</h2>
				</div>
				<div className="text-xs text-[color:var(--ink-soft)]">Last 30 minutes</div>
			</div>

			{items.length === 0 ? (
				<p className="mt-4 text-sm text-[color:var(--ink-soft)]">No anomalies detected yet.</p>
			) : (
				<div className="timeline mt-4">
					{items.map((incident) => {
						const badge = memoryBadge(incident);
						return (
							<article key={incident.id} className="timeline-item">
								<div className="timeline-item__time">{formatRelativeTime(incident.created_at)}</div>
								<div className="timeline-item__title">{incident.affected_pod}</div>
								<div className="timeline-item__meta">
									<span className={`badge badge--${severityTone(incident.severity)}`}>
										{incident.severity}
									</span>
									<span className={`badge badge--${badge.tone}`}>{badge.label}</span>
								</div>
								<p className="timeline-item__desc">{incident.root_cause}</p>
							</article>
						);
					})}
				</div>
			)}
		</section>
	);
}
