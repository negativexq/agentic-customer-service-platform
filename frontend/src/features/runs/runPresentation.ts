import { deriveRunSemantics } from "../../components/runSemantics";
import type { AgentRun } from "../../types";
import { UI_LOCALE } from "@/lib/utils";

export type Tone = "success" | "warning" | "danger" | "neutral" | "info";

export const toneDot: Record<Tone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
  neutral: "bg-muted-foreground/50",
};

export const toneText: Record<Tone, string> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
  neutral: "text-muted-foreground",
};

const ACRONYMS = new Set(["faq", "llm", "rag", "id", "csat", "sla", "api", "url", "pii"]);

/** "support_faq" -> "Support FAQ"; an unrecognised intent reads as such, not as "Unknown". */
export function humanize(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  if (value === "unknown") return "Unrecognised request";
  const text = value.replace(/_/g, " ").replace(/\b[a-z]+\b/gi, (word) => (ACRONYMS.has(word.toLowerCase()) ? word.toUpperCase() : word));
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function isRecorded(run: AgentRun): boolean {
  return run.run_id.startsWith("demo-");
}

export function runStatus(run: AgentRun): { label: string; tone: Tone } {
  const semantics = deriveRunSemantics(run);
  switch (semantics.status) {
    case "completed": return { label: "Completed", tone: "success" };
    case "waiting_confirmation": return { label: "Awaiting confirmation", tone: "warning" };
    case "needs_input": return { label: "Needs input", tone: "info" };
    case "failed": return { label: "Failed", tone: "danger" };
    case "blocked": return { label: "Blocked", tone: "warning" };
    case "declined": return { label: "Declined", tone: "neutral" };
    case "replayed": return { label: "Replay suppressed", tone: "neutral" };
    case "suspended": return { label: "Suspended", tone: "warning" };
    case "replaced": return { label: "Replaced", tone: "neutral" };
    default:
      if (run.failure_category) return { label: humanize(run.failure_category), tone: "danger" };
      return { label: "Answered", tone: "success" };
  }
}

export type DecisionStep = { stage: "Proposal" | "Policy" | "Authority" | "Outcome"; value: string; tone: Tone };

/** The run's decision path: what the model proposed, what policy decided, what authority it got, and what happened. */
export function decisionPath(run: AgentRun): DecisionStep[] {
  const semantics = deriveRunSemantics(run);
  const validation = run.proposal?.validation;
  const proposal: DecisionStep = {
    stage: "Proposal",
    value: run.proposal ? `${humanize(run.proposal.intent)} · ${humanize(validation)}` : humanize(run.intent),
    tone: validation === "rejected" ? "danger" : validation === "passed" ? "success" : "neutral",
  };
  const lastPolicy = run.policy?.[run.policy.length - 1]?.outcome;
  const decision = semantics.clarification ? "clarification_required" : lastPolicy ?? semantics.decision;
  const policy: DecisionStep = {
    stage: "Policy",
    value: humanize(decision),
    tone: /deny|denied|validation_failed/.test(decision) ? "danger" : /confirmation|clarification/.test(decision) ? "warning" : /allow|approved|read/.test(decision) ? "success" : "neutral",
  };
  const authority: DecisionStep = {
    stage: "Authority",
    value: humanize(semantics.authority),
    tone: semantics.authority === "controlled_execution" || semantics.authority === "read_access" ? "success" : semantics.authority === "confirmation_required" ? "warning" : "neutral",
  };
  const status = runStatus(run);
  return [proposal, policy, authority, { stage: "Outcome", value: status.label, tone: status.tone }];
}

export function runLatencyMs(run: AgentRun): number {
  return run.provider_metadata?.latency_ms ?? run.duration_ms;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function formatTokens(run: AgentRun): string {
  const meta = run.provider_metadata;
  if (!meta || (meta.input_tokens === null && meta.output_tokens === null)) return "—";
  return `${(meta.input_tokens ?? 0) + (meta.output_tokens ?? 0)}`;
}

/** The projection API emits naive UTC timestamps; treat a missing offset as UTC, not local time. */
export function parseTimestamp(iso: string): Date {
  return new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`);
}

export function formatRelative(iso: string, now = Date.now()): string {
  const seconds = Math.round((now - parseTimestamp(iso).getTime()) / 1000);
  if (!Number.isFinite(seconds)) return "—";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  return parseTimestamp(iso).toLocaleDateString(UI_LOCALE, { month: "short", day: "numeric" });
}
