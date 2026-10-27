import { Clock3, GitBranch, LockKeyhole, Radio, Server, ShieldCheck } from "lucide-react";
import type { AgentRun } from "../../types";
import { Badge, Card, EmptyState, StatusIndicator } from "../../components/ui";
import { TraceTimeline } from "../../components/TraceTimeline";
import { deriveRunSemantics } from "../../components/runSemantics";

function authorityState(run: AgentRun): { label: string; tone: "success" | "warning" | "danger" | "neutral" } {
  const authority = run.evidence.authority ?? "not_granted";
  if (authority === "confirmation_required") return { label: "Confirmation required", tone: "warning" };
  if (authority === "controlled_execution" || authority === "read_access") return { label: authority.replace(/_/g, " "), tone: "success" };
  return { label: authority.replace(/_/g, " "), tone: authority === "not_granted" ? "danger" : "neutral" };
}

export function TracePanel({ run }: { run: AgentRun | null }) {
  if (!run) return <Card className="p-4" data-testid="trace-panel"><div className="mb-3 flex items-center gap-2"><Radio size={15} className="text-info" aria-hidden="true" /><span className="text-sm font-medium text-main">Runtime details</span></div><EmptyState title="Trace pending" description="Runtime details and bounded trace metadata appear after the agent returns a projection." icon={Radio} /></Card>;
  const authority = authorityState(run);
  const semantics = deriveRunSemantics(run);
  const decision = run.evidence.decision ?? "not_recorded";
  const validationStage = run.evidence.validation_stage ?? "not_recorded";
  return <Card className="p-4" data-testid="trace-panel"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Radio size={15} className="text-info" aria-hidden="true" /><span className="text-sm font-medium text-main">Runtime details</span></div><StatusIndicator label={semantics.status.replace(/_/g, " ")} tone={semantics.status === "completed" ? "success" : semantics.status === "waiting_confirmation" ? "warning" : semantics.status === "blocked" || semantics.status === "failed_validation" ? "danger" : "neutral"} compact /></div><div className="mt-4 grid grid-cols-2 gap-3"><div><span className="field-label">Trace ID</span><div className="mt-1 flex items-center gap-1 font-mono text-xs text-main"><GitBranch size={11} aria-hidden="true" />{run.trace_id ?? "Not recorded"}</div></div><div><span className="field-label">Latency</span><div className="mt-1 flex items-center gap-1 text-xs text-main"><Clock3 size={11} aria-hidden="true" />{run.duration_ms.toFixed(1)} ms</div></div><div><span className="field-label">Provider</span><div className="mt-1 text-xs text-main">{run.provider}</div></div><div><span className="field-label">Model</span><div className="mt-1 font-mono text-xs text-main">{run.model ?? "Not recorded"}</div></div></div><div className="mt-4 space-y-2 border-t border-border pt-3"><div className="flex items-center justify-between gap-2 text-xs"><span className="flex items-center gap-2 text-muted"><ShieldCheck size={13} aria-hidden="true" />Decision</span><Badge tone={decision.includes("deny") || decision.includes("failed") ? "danger" : "warning"}>{decision.replace(/_/g, " ")}</Badge></div><div className="flex items-center justify-between gap-2 text-xs"><span className="text-muted">Validation stage</span><span className="text-main">{validationStage.replace(/_/g, " ")}</span></div><div className="flex items-center justify-between gap-2 text-xs"><span className="flex items-center gap-2 text-muted"><LockKeyhole size={13} aria-hidden="true" />Authority</span><Badge tone={authority.tone}>{authority.label}</Badge></div><div className="flex items-center justify-between gap-2 text-xs"><span className="flex items-center gap-2 text-muted"><Server size={13} aria-hidden="true" />Execution status</span><span className="text-main">{semantics.executionStatus.replace(/_/g, " ")}</span></div></div><div className="mt-5 border-t border-border pt-4"><div className="mb-3 text-xs font-medium uppercase tracking-[.12em] text-muted">Observed trace</div><TraceTimeline events={run.trace} run={run} embedded /></div></Card>;
}
