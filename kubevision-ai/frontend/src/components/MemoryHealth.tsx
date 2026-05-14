import { useMemo, useState } from "react";
import { useClusterStore } from "../store/clusterStore";

type Tone = "fast" | "grounded" | "cold";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function MemoryBar({ label, value, tone }: { label: string; value: number; tone: Tone }) {
	const safeValue = Math.max(0, Math.min(100, value));
	return (
		<div className="memory-bar">
			<div className="flex items-center justify-between text-xs text-[color:var(--ink-soft)]">
				<span>{label}</span>
				<span>{safeValue.toFixed(1)}%</span>
			</div>
			<div className="memory-bar__track">
				<span className={`memory-bar__fill memory-bar__fill--${tone}`} style={{ width: `${safeValue}%` }} />
			</div>
		</div>
	);
}

export default function MemoryHealth() {
	const memoryStats = useClusterStore((state) => state.memoryStats);
	const setMemoryStats = useClusterStore((state) => state.setMemoryStats);
	const pushToast = useClusterStore((state) => state.pushToast);
	const [seeding, setSeeding] = useState(false);
	const topPatterns = useMemo(() => memoryStats.top_patterns.slice(0, 5), [memoryStats.top_patterns]);

	const handleSeedMemory = async () => {
		if (seeding) {
			return;
		}
		setSeeding(true);
		try {
			const response = await fetch(`${API_BASE_URL}/api/debug/seed-memory`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({}),
			});
			if (!response.ok) {
				throw new Error(`Seed failed: ${response.status}`);
			}
			const statsResponse = await fetch(`${API_BASE_URL}/api/memory/stats`);
			if (statsResponse.ok) {
				setMemoryStats(await statsResponse.json());
			}
			pushToast("Memory case seeded for demo.", "success");
		} catch (error) {
			pushToast("Failed to seed memory case.", "error");
		} finally {
			setSeeding(false);
		}
	};

	return (
		<section className="panel-surface panel-surface--glass p-5">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<p className="eyebrow">Episodic Memory</p>
					<h2 className="text-lg font-display text-[color:var(--ink-strong)]">Memory health</h2>
				</div>
				<div className="flex flex-wrap items-center gap-3">
					<div className="text-xs text-[color:var(--ink-soft)]">
						{memoryStats.total_incidents} stored incidents
					</div>
					<button className="button button--ghost" type="button" onClick={handleSeedMemory} disabled={seeding}>
						{seeding ? "Seeding..." : "Seed memory"}
					</button>
				</div>
			</div>

			<div className="mt-4 grid gap-4">
				<MemoryBar label="Fast path" value={memoryStats.fast_path_pct} tone="fast" />
				<MemoryBar label="Grounded path" value={memoryStats.grounded_path_pct} tone="grounded" />
				<MemoryBar label="Cold path" value={memoryStats.cold_path_pct} tone="cold" />
			</div>

			<div className="mt-5">
				<div className="text-xs font-semibold uppercase tracking-[0.2em] text-[color:var(--ink-soft)]">
					Top recalled patterns
				</div>
				{topPatterns.length === 0 ? (
					<p className="mt-3 text-sm text-[color:var(--ink-soft)]">No incidents stored yet.</p>
				) : (
					<ul className="mt-3 space-y-2">
						{topPatterns.map((pattern) => (
							<li key={pattern.pattern} className="memory-pattern">
								<span className="memory-pattern__label" title={pattern.pattern}>
									{pattern.pattern.length > 36 ? `${pattern.pattern.slice(0, 33)}...` : pattern.pattern}
								</span>
								<span className="memory-pattern__count">{pattern.recall_count}</span>
							</li>
						))}
					</ul>
				)}
			</div>
		</section>
	);
}
