import { Activity, CheckCircle2, CircleAlert, CircleDot, Clock3, LockKeyhole } from "lucide-react";
import { formatTraceDuration, traceMetadata, type RecordedTraceMetadata } from "../data/traceFixtures";
import type { AgentRun, TraceEvent, TraceStage as TraceStageId } from "../types";
import { Badge, EmptyState, Panel, SectionHeader, StatusIndicator } from "./ui";

export type TraceStageStatus = "completed" | "blocked" | "waiting" | "failed" | "not_recorded";
type TraceLayer = "input" | "context" | "decision" | "authority";
type ContextRole = "customer_state" | "knowledge_retrieval";

export type TraceStage = {
  id: string;
  layer: TraceLayer;
  contextRole?: ContextRole;
  label: string;
  status: TraceStageStatus;
  explanation: string;
  evidence?: string;
  events: TraceEvent[];
  metadata: RecordedTraceMetadata;
};

type StageDefinition = {
  id: string;
  layer: TraceLayer;
  contextRole?: ContextRole;
  stage: TraceStageId;
  label: string;
  absent: string;
  explanation: (run: AgentRun, events: TraceEvent[]) => string;
};

const stageDefinitions: StageDefinition[] = [
  { id: "request", layer: "input", stage: "user_request", label: "User request", absent: "Request metadata is unavailable.", explanation: (_run, events) => events.length ? "Request entered the bounded agent projection." : "Request exists, but a separate request event is not recorded." },
  { id: "intent", layer: "input", stage: "intent_detection", label: "Intent detection", absent: "Intent stage is unavailable.", explanation: (run, events) => events.length || run.intent ? `Intent recorded as ${run.intent || "not recorded"}.` : "Intent detection is not recorded as a separate node." },
  { id: "memory", layer: "context", contextRole: "customer_state", stage: "memory_context", label: "Memory context", absent: "No memory context stage recorded for this run.", explanation: (run, events) => {
    const itemsUsed = run.memory.items_used ?? run.memory.retrieved_count ?? run.memory.item_count;
    return events.length || itemsUsed > 0 ? `${itemsUsed} bounded memory item${itemsUsed === 1 ? "" : "s"} available for context enrichment.` : "Memory was not used in the current projection.";
  } },
  { id: "context", layer: "context", stage: "context_retrieval", label: "Context retrieval", absent: "No context retrieval stage recorded.", explanation: (run, events) => events.length || run.memory.item_count > 0 || run.rag_documents.length > 0 ? "Context sources are available to the bounded proposal projection." : "No context retrieval event was recorded for this run." },
  { id: "rag", layer: "context", contextRole: "knowledge_retrieval", stage: "context_retrieval", label: "RAG evidence", absent: "No retrieval stage recorded.", explanation: (run, events) => run.rag_documents.length ? `${run.rag_documents.length} retrieved document record(s) are available.` : events.length ? "Retrieval ran, but document metadata is unavailable from the projection." : "No retrieval stage was recorded for this run." },
  { id: "workflow", layer: "decision", stage: "routing", label: "Workflow transition", absent: "No workflow transition was recorded.", explanation: (_run, events) => events.length ? `Workflow ${String(events[events.length - 1].metadata?.workflow_state ?? "transition").replace(/_/g, " ")} recorded by the routing layer.` : "No suspend, resume, or replacement transition was recorded for this run." },
  { id: "grounding", layer: "decision", stage: "grounding", label: "Semantic grounding", absent: "No semantic grounding stage recorded.", explanation: (run, events) => events.length || run.evidence.grounding.status !== "not_recorded" ? "Deterministic grounding and compilation stage recorded." : "No semantic grounding event was recorded for this run." },
  { id: "target", layer: "decision", stage: "target_validation", label: "Target validation", absent: "No target validation stage recorded.", explanation: (run, events) => events.length || run.evidence.target_validation.status !== "not_recorded" ? "Tool target passed through the runtime validation node." : "No target validation event was recorded for this run." },
  { id: "policy", layer: "decision", stage: "policy_evaluation", label: "Policy evaluation", absent: "No policy evaluation emitted.", explanation: (run, events) => run.policy.length ? `Decision: ${run.policy[run.policy.length - 1].outcome.replace(/_/g, " ")}.` : events.length ? "Policy node recorded without an audit event." : "No policy evaluation was emitted for this run." },
  { id: "confirmation", layer: "authority", stage: "confirmation", label: "Confirmation gate", absent: "No confirmation gate event recorded.", explanation: (run, events) => run.status === "waiting_confirmation" || run.evidence.confirmation.required ? "Mutation is waiting for explicit confirmation." : events.length ? "Confirmation lifecycle node recorded." : "No confirmation gate was required or recorded." },
  { id: "execution", layer: "authority", stage: "execution_authority", label: "Execution authority", absent: "Execution authority was not reached.", explanation: (run, events) => run.status === "waiting_confirmation" ? "Execution is locked until confirmation." : run.evidence.write_outcome.status === "executed" || events.length ? "Execution authority node recorded." : "No execution authority event was recorded for this run." },
];

function statusForStage(stage: StageDefinition, run: AgentRun, events: TraceEvent[]): TraceStageStatus {
  if (events.some((event) => event.status === "error" || event.status === "failed")) return "failed";
  if (stage.id === "confirmation" && run.status === "waiting_confirmation") return "waiting";
  if (stage.id === "execution" && run.status === "waiting_confirmation") return "blocked";
  if (events.length > 0) return "completed";
  if (stage.id === "request" || stage.id === "intent") return run.intent ? "completed" : "not_recorded";
  if (stage.id === "memory") return run.memory.item_count > 0 ? "completed" : "not_recorded";
  if (stage.id === "context" || stage.id === "rag") return run.memory.item_count > 0 || run.rag_documents.length > 0 ? "completed" : "not_recorded";
  if (stage.id === "grounding") return run.evidence.grounding.status !== "not_recorded" ? "completed" : "not_recorded";
  if (stage.id === "target") return run.evidence.target_validation.status !== "not_recorded" ? "completed" : "not_recorded";
  if (stage.id === "policy") return run.policy.length > 0 ? "completed" : "not_recorded";
  if (stage.id === "confirmation") return run.evidence.confirmation.required ? "waiting" : "not_recorded";
  if (stage.id === "execution") return run.evidence.write_outcome.status === "executed" ? "completed" : "blocked";
  return "not_recorded";
}

function matchingEvents(definition: StageDefinition, events: TraceEvent[]): TraceEvent[] {
  if (definition.id === "workflow") {
    return events.filter((event) => event.event_key?.startsWith("workflow."));
  }
  return events.filter((event) => event.stage === definition.stage);
}

export function buildTraceStages(run: AgentRun): TraceStage[] {
  const definitions = run.security_signal === "instruction_override_attempt"
    ? stageDefinitions.filter((definition) => definition.id !== "intent")
    : stageDefinitions;
  return definitions.map((definition) => {
    const matched = matchingEvents(definition, run.trace);
    const status = statusForStage(definition, run, matched);
    const evidence = definition.id === "policy" && run.policy.length ? run.policy[run.policy.length - 1].reason_codes.join(" · ") : definition.id === "rag" && run.rag_documents.length ? run.rag_documents.map((document) => document.citation_id).join(" · ") : undefined;
    return { id: definition.id, layer: definition.layer, contextRole: definition.contextRole, label: definition.label, status, explanation: status !== "not_recorded" ? definition.explanation(run, matched) : definition.absent, evidence: evidence || undefined, events: matched, metadata: traceMetadata(definition.id, run, matched) };
  });
}

function stageTone(status: TraceStageStatus): "success" | "warning" | "danger" | "neutral" {
  if (status === "completed") return "success";
  if (status === "waiting" || status === "blocked") return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}

function StageIcon({ status }: { status: TraceStageStatus }) {
  if (status === "completed") return <CheckCircle2 size={16} aria-hidden="true" />;
  if (status === "waiting" || status === "blocked" || status === "failed") return <CircleAlert size={16} aria-hidden="true" />;
  return <CircleDot size={16} aria-hidden="true" />;
}

function stageMeta(stage: TraceStage): string {
  if (stage.metadata.source === "not_recorded") return "Not recorded";
  return `${formatTraceDuration(stage.metadata)} · ${stage.metadata.evidenceCount} evidence`;
}

const eventLabels: Record<string, string> = {
  load_context: "Request context loaded",
  understand_request: "Intent detection",
  retrieve_memory: "Memory retrieval",
  retrieve_context: "Context retrieval",
  retrieve_documents: "Knowledge retrieval",
  compile_decision: "Decision compilation",
  validate_tool: "Target validation",
  evaluate_policy: "Policy evaluation",
  create_pending_action: "Confirmation gate",
  handle_workflow_interruption: "Workflow transition",
  restore_suspended_workflow: "Workflow resumed",
  execute_tool: "Execution authority",
  respond: "Response returned",
};

function eventLabel(name: string): string {
  return eventLabels[name] ?? name.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function eventKeyLabel(event: TraceEvent): string {
  if (!event.event_key) return eventLabel(event.name);
  return event.event_key.replace(/[._]/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function observableEventMetadata(metadata: Record<string, unknown> | null | undefined): Array<[string, unknown]> {
  if (!metadata) return [];
  return Object.entries(metadata).filter(([key]) => !/(token|prompt|reason|argument|secret|api[_ ]?key|customer[_ ]?id|order[_ ]?id|message|content|raw)/i.test(key));
}

const layerLabels: Record<TraceLayer, string> = {
  input: "Input",
  context: "Context",
  decision: "Decision",
  authority: "Authority",
};

const contextRoleLabels: Record<ContextRole, { label: string; tone: "info" | "success" }> = {
  customer_state: { label: "Customer state", tone: "info" },
  knowledge_retrieval: { label: "Knowledge retrieval", tone: "success" },
};

export function TraceStageTimeline({ run, compact = false }: { run: AgentRun; compact?: boolean }) {
  const stages = buildTraceStages(run);
  const layers = (Object.keys(layerLabels) as TraceLayer[]).map((layer) => ({
    layer,
    stages: stages.filter((stage) => stage.layer === layer),
  }));
  return <div data-testid="trace-timeline" className={`trace-stage-layers ${compact ? "trace-stage-list-compact" : ""}`}>{layers.map(({ layer, stages: layerStages }) => <section className="trace-layer" key={layer}><div className="trace-layer-heading"><span className="eyebrow">{layerLabels[layer]}</span><span className="trace-layer-rule" aria-hidden="true" /></div><div className="trace-stage-list">{layerStages.map((stage, index) => <details className={`trace-stage trace-stage-${stage.status}`} key={stage.id} open={index === 0 || stage.status === "waiting" || stage.status === "failed"}><summary className="trace-stage-summary"><span className="trace-stage-marker"><StageIcon status={stage.status} /></span><span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-2"><span className="truncate text-sm font-medium text-main">{stage.label}</span>{stage.contextRole && <Badge tone={contextRoleLabels[stage.contextRole].tone}>{contextRoleLabels[stage.contextRole].label}</Badge>}</span><span className="mt-1 block text-xs text-muted">{stage.explanation}</span></span><span className="trace-stage-meta"><StatusIndicator label={stage.status.replace(/_/g, " ")} tone={stageTone(stage.status)} compact /><span className="hidden font-mono text-xs text-muted sm:inline">{stageMeta(stage)}</span></span></summary><div className="trace-stage-detail"><div className="trace-stage-explanation"><span className="field-label">System explanation</span><p className="mt-1 text-xs leading-5 text-muted">{stage.explanation}</p></div><div className="trace-stage-operational-meta"><div><span className="field-label">Duration</span><strong>{formatTraceDuration(stage.metadata)}</strong></div><div><span className="field-label">Evidence</span><strong>{stage.metadata.source === "not_recorded" ? "Not recorded" : stage.metadata.evidenceCount}</strong></div><div><span className="field-label">Owner</span><strong>{stage.metadata.owner}</strong></div><div><span className="field-label">Metadata source</span><strong>{stage.metadata.source === "recorded_fixture" ? "Recorded fixture" : stage.metadata.source === "observed" ? "Observed event" : "Not recorded"}</strong></div></div>{stage.evidence && <div className="trace-stage-evidence"><span className="field-label">Observed evidence</span><p className="mt-1 font-mono text-xs text-info">{stage.evidence}</p></div>}{stage.events.length > 0 && <div className="mt-3 space-y-1.5">{stage.events.map((event, eventIndex) => <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-void/30 px-2.5 py-2 text-xs" key={`${event.name}-${event.timestamp}-${eventIndex}`}><div><span className="block text-xs font-medium text-main">{event.event_key ? eventKeyLabel(event) : eventLabel(event.name)}</span><span className="mt-1 block font-mono text-xs text-muted">{event.name}</span>{observableEventMetadata(event.metadata).length > 0 && <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-info">{observableEventMetadata(event.metadata).map(([key, value]) => <span key={key}>{key.replace(/_/g, " ")}: {String(value).replace(/_/g, " ")}</span>)}</div>}</div><span className="inline-flex items-center gap-1 text-muted"><Clock3 size={11} aria-hidden="true" />{new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })} · {event.duration_ms.toFixed(1)} ms</span></div>)}</div>}</div></details>)}</div></section>)}</div>;
}

export function TraceTimeline({ events, run, embedded = false }: { events: TraceEvent[]; run?: AgentRun | null; embedded?: boolean }) {
  const content = run ? <><div className="trace-authority-strip"><span>Input</span><span>↓</span><span className="trace-proposal-label">LLM proposal</span><span>↓</span><span>Deterministic checks</span><span>↓</span><span className="trace-authority-label"><LockKeyhole size={12} aria-hidden="true" />Final decision</span></div><TraceStageTimeline run={run} /></> : events.length === 0 ? <EmptyState title="Trace metadata unavailable" description="Run an agent request to generate bounded execution events." icon={Activity} /> : <div className="space-y-2">{events.map((event, index) => <div className="trace-event-row" key={`${event.name}-${event.timestamp}-${index}`}><span className="font-mono text-xs text-main">{event.name}</span><span className="text-xs text-muted">{event.status} · {event.duration_ms.toFixed(1)} ms</span></div>)}</div>;
  return embedded ? <div>{content}</div> : <Panel title="Agent trace timeline" eyebrow="OpenTelemetry projection"><SectionHeader title="Why this decision happened" description="Observed lifecycle stages only; no prompts, chain-of-thought, or fabricated timing." />{content}</Panel>;
}
