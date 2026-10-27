import { Activity, BookOpen, BrainCircuit, Clock3, Database, ShieldCheck, Wrench } from "lucide-react";
import type { AgentRun, TraceEvent } from "../../types";
import { Badge, Card, EmptyState, StatusIndicator } from "../../components/ui";

function traceLabel(event: TraceEvent): string {
  if (event.event_key === "workflow.suspended") return "Workflow paused";
  if (event.event_key === "workflow.superseded") return "Workflow replaced";
  if (event.event_key === "workflow.resumed") return "Workflow resumed";
  if (event.stage === "intent_detection") return "Intent detected";
  if (event.stage === "context_retrieval") return "Context retrieved";
  if (event.stage === "grounding") return "Grounding validated";
  if (event.stage === "target_validation") return "Target validated";
  if (event.stage === "policy_evaluation") return "Policy evaluated";
  if (event.stage === "confirmation") return "Confirmation boundary";
  if (event.stage === "execution_authority") return "Execution authority";
  if (event.stage === "memory_context") return "Memory event";
  return event.event_key?.replace(/[._]/g, " ") || event.name.replace(/_/g, " ");
}

function statusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "completed" || status === "success" || status === "executed") return "success";
  if (status === "waiting" || status === "pending" || status === "blocked") return "warning";
  if (status === "failed" || status === "error") return "danger";
  return "neutral";
}

function lastPolicyEvent(run: AgentRun) {
  return run.policy.length > 0 ? run.policy[run.policy.length - 1] : undefined;
}

function observableMetadata(metadata: Record<string, unknown> | null | undefined): string {
  if (!metadata) return "";
  return Object.entries(metadata)
    .filter(([key]) => !/(token|prompt|reason|argument|secret|api[_ ]?key|customer[_ ]?id|order[_ ]?id|message|content|raw)/i.test(key))
    .map(([key, value]) => `${key.replace(/_/g, " ")}: ${String(value).replace(/_/g, " ")}`)
    .join(" · ");
}

function EventRow({ icon: Icon, label, detail, status, meta }: { icon: typeof Activity; label: string; detail: string; status: string; meta?: string }) {
  return <div className="flex gap-3 border-l border-border pl-4" data-testid="agent-activity-event"><span className="-ml-[25px] mt-0.5 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-surface text-muted"><Icon size={11} aria-hidden="true" /></span><div className="min-w-0 flex-1 pb-4"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-medium capitalize text-main">{label}</span><StatusIndicator label={status.replace(/_/g, " ")} tone={statusTone(status)} compact /></div><p className="mt-1 text-xs leading-5 text-muted">{detail}</p>{meta && <span className="mt-1 inline-flex items-center gap-1 font-mono text-xs text-muted"><Clock3 size={10} aria-hidden="true" />{meta}</span>}</div></div>;
}

export function AgentTimeline({ run, busy }: { run: AgentRun | null; busy: boolean }) {
  if (!run && !busy) return <Card className="p-4" data-testid="agent-timeline"><div className="mb-3 flex items-center gap-2"><Activity size={15} className="text-info" aria-hidden="true" /><span className="text-sm font-medium text-main">Agent timeline</span></div><EmptyState title="No activity yet" description="Send a customer request to observe intent, context, policy, tools, memory, and tracing in one view." icon={Activity} /></Card>;
  if (!run) return <Card className="p-4" data-testid="agent-timeline"><div className="mb-3 flex items-center gap-2"><Activity size={15} className="text-info" aria-hidden="true" /><span className="text-sm font-medium text-main">Agent timeline</span></div><EventRow icon={Activity} label="Agent processing" detail="The backend is evaluating the request. Event metadata will appear when the run projection is available." status="running" /></Card>;

  const events = run.trace.filter((event) => event.stage !== "internal");
  const lastPolicy = lastPolicyEvent(run);
  const memoryCount = run.memory.items_used ?? run.memory.retrieved_count ?? run.memory.item_count;
  const securityBlocked = Boolean(run.security_signal);
  const securityDetail = run.security_signal === "memory_security_override_attempt"
    ? "Memory security override was rejected before memory lookup or persistence."
    : run.security_signal === "cross_customer_access_attempt"
      ? "Cross-customer access was rejected before business routing."
      : "Instruction override attempt was contained before workflow processing.";
  return <Card className="p-4" data-testid="agent-timeline"><div className="mb-4 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Activity size={15} className="text-info" aria-hidden="true" /><span className="text-sm font-medium text-main">Agent timeline</span></div><Badge tone="neutral">{events.length} event{events.length === 1 ? "" : "s"}</Badge></div><div className="space-y-1">{securityBlocked ? <EventRow icon={ShieldCheck} label="Security boundary" detail={securityDetail} status="blocked" /> : <EventRow icon={BrainCircuit} label="Intent detected" detail={run.intent || "Intent not recorded"} status={run.intent ? "completed" : "not_recorded"} />}{run.memory.item_count > 0 || (run.memory.retrieved_count ?? 0) > 0 ? <EventRow icon={Database} label="Memory event" detail={`${memoryCount} bounded item(s) available for context enrichment.`} status="completed" /> : null}{run.rag_documents.length > 0 ? <EventRow icon={BookOpen} label="RAG retrieval" detail={`${run.rag_documents.length} knowledge source(s) attached to the operator projection.`} status="completed" /> : null}{events.map((event, index) => <EventRow key={`${event.name}-${event.timestamp}-${index}`} icon={event.stage === "policy_evaluation" ? ShieldCheck : event.stage === "execution_authority" ? Wrench : Activity} label={traceLabel(event)} detail={observableMetadata(event.metadata) || "Observable lifecycle event recorded."} status={event.status} meta={`${new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })} · ${event.duration_ms.toFixed(1)} ms`} />)}{lastPolicy && <EventRow icon={ShieldCheck} label="Policy decision" detail={`${lastPolicy.outcome.replace(/_/g, " ")}${lastPolicy.reason_codes.length ? ` · ${lastPolicy.reason_codes.join(", ")}` : ""}`} status={lastPolicy.outcome} />}</div></Card>;
}
