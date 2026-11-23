import { isRecorded, parseTimestamp, runLatencyMs, runStatus } from "../runs/runPresentation";
import type { AgentRun, TraceStage } from "../../types";
import { UI_LOCALE } from "@/lib/utils";

/**
 * Telemetry derived from persisted run projections. The page reads only this shape, so a
 * dedicated metrics API can replace the derivation without touching the views.
 */
export type TelemetrySnapshot = {
  runCount: number;
  generationCount: number;
  successRate: number | null;
  errorCount: number;
  escalationCount: number;
  latencyP50: number | null;
  latencyP95: number | null;
  inputTokens: number;
  outputTokens: number;
  costUsd: number | null;
  timeline: TimelinePoint[];
  outcomes: { label: string; count: number }[];
  models: { model: string; calls: number; avgLatency: number; tokens: number; errors: number }[];
  stages: { stage: TraceStage; avgMs: number; samples: number }[];
};

export type TimelineOutcome = "succeeded" | "failed" | "escalated";
export type TimelinePoint = { at: string; label: string; latency: number; tokensIn: number; tokensOut: number; failed: boolean; model: string; outcome: TimelineOutcome };

export function percentile(values: number[], p: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index];
}

const failedStatuses = new Set(["Failed", "Failed validation"]);

export function deriveTelemetry(allRuns: AgentRun[]): TelemetrySnapshot {
  const runs = allRuns.filter((run) => !isRecorded(run)).sort((a, b) => a.started_at.localeCompare(b.started_at));
  const generations = runs.filter((run) => run.provider_metadata?.latency_ms !== null && run.provider_metadata?.latency_ms !== undefined);
  const latencies = runs.map(runLatencyMs).filter((value) => Number.isFinite(value));
  const failed = runs.filter((run) => run.failure_category || run.status === "error" || failedStatuses.has(runStatus(run).label));

  const outcomeCounts = new Map<string, number>();
  for (const run of runs) {
    const label = runStatus(run).label;
    outcomeCounts.set(label, (outcomeCounts.get(label) ?? 0) + 1);
  }

  const modelStats = new Map<string, { calls: number; latency: number; tokens: number; errors: number }>();
  for (const run of generations) {
    const meta = run.provider_metadata!;
    const key = meta.model ?? run.model ?? "unknown";
    const entry = modelStats.get(key) ?? { calls: 0, latency: 0, tokens: 0, errors: 0 };
    entry.calls += 1;
    entry.latency += meta.latency_ms ?? 0;
    entry.tokens += (meta.input_tokens ?? 0) + (meta.output_tokens ?? 0);
    if (run.failure_category) entry.errors += 1;
    modelStats.set(key, entry);
  }

  const stageStats = new Map<TraceStage, { total: number; samples: number }>();
  for (const run of runs) {
    for (const event of run.trace) {
      if (event.stage === "internal" || event.stage === "user_request") continue;
      const entry = stageStats.get(event.stage) ?? { total: 0, samples: 0 };
      entry.total += event.duration_ms;
      entry.samples += 1;
      stageStats.set(event.stage, entry);
    }
  }

  const costs = runs.map((run) => run.provider_metadata?.cost_usd).filter((value): value is number => typeof value === "number");
  return {
    runCount: runs.length,
    generationCount: generations.length,
    successRate: runs.length ? (runs.length - failed.length) / runs.length : null,
    errorCount: failed.length,
    escalationCount: runs.filter((run) => run.intent === "human_escalation").length,
    latencyP50: percentile(latencies, 50),
    latencyP95: percentile(latencies, 95),
    inputTokens: runs.reduce((sum, run) => sum + (run.provider_metadata?.input_tokens ?? 0), 0),
    outputTokens: runs.reduce((sum, run) => sum + (run.provider_metadata?.output_tokens ?? 0), 0),
    costUsd: costs.length ? costs.reduce((sum, value) => sum + value, 0) : null,
    timeline: runs.map((run) => ({
      at: run.started_at,
      label: parseTimestamp(run.started_at).toLocaleTimeString(UI_LOCALE, { hour: "2-digit", minute: "2-digit" }),
      latency: Math.round(runLatencyMs(run)),
      tokensIn: run.provider_metadata?.input_tokens ?? 0,
      tokensOut: run.provider_metadata?.output_tokens ?? 0,
      failed: failed.includes(run),
      model: run.provider_metadata?.model ?? run.model ?? "unknown",
      outcome: failed.includes(run) ? "failed" : run.intent === "human_escalation" ? "escalated" : "succeeded",
    })),
    outcomes: [...outcomeCounts].map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count),
    models: [...modelStats].map(([model, stat]) => ({ model, calls: stat.calls, avgLatency: stat.latency / stat.calls, tokens: stat.tokens, errors: stat.errors })).sort((a, b) => b.calls - a.calls),
    stages: [...stageStats].map(([stage, stat]) => ({ stage, avgMs: stat.total / stat.samples, samples: stat.samples })).sort((a, b) => b.avgMs - a.avgMs),
  };
}
