import { Activity, Brain, CheckCircle2, CircleAlert, GitBranch, LockKeyhole, ShieldCheck, Wrench, XCircle } from "lucide-react";
import type { AgentRun, MemoryRecord } from "../types";
import { Badge, DataRow, EmptyState, Panel, SectionHeader, StatusIndicator, Tabs } from "./ui";
import { MemoryPanel } from "./MemoryPanel";
import { PolicyPanel } from "./PolicyPanel";
import { ToolPanel } from "./ToolPanel";
import { TraceTimeline } from "./TraceTimeline";
import { GroundingPanel } from "./GroundingPanel";
import { deriveRunSemantics } from "./runSemantics";

function humanize(value: string | null | undefined): string {
  return value ? value.replace(/_/g, " ") : "Not recorded";
}

function GroundingCheck({ label, tone, detail }: { label: string; tone: "success" | "warning" | "neutral"; detail: string }) {
  const Icon = tone === "success" ? CheckCircle2 : CircleAlert;
  return <div className="control-check"><Icon size={14} className={tone === "success" ? "text-success" : tone === "warning" ? "text-warning" : "text-muted"} aria-hidden="true" /><div><div className="text-xs font-medium text-main">{label}</div><div className="mt-0.5 text-xs text-muted">{detail}</div></div></div>;
}

function ActionInspector({ run }: { run: AgentRun }) {
  const tool = run.tools[0];
  const policy = run.policy[run.policy.length - 1];
  const semantics = deriveRunSemantics(run);
  const isPending = semantics.waiting;
  const isExecuted = semantics.executed;
  const executionLabel = isExecuted ? "Completed" : isPending ? "Awaiting confirmation" : semantics.status === "needs_input" ? "Not attempted" : semantics.status === "failed_validation" ? "Failed validation" : semantics.status === "blocked" ? "Prevented" : humanize(semantics.executionStatus);
  const executionTone = isExecuted ? "success" : isPending ? "warning" : run.status === "error" ? "danger" : "neutral";
  const toolName = tool?.name ?? policy?.tool_name ?? "No tool proposal recorded";
  const decision = semantics.decision;
  const decisionReason = run.evidence.reason ?? run.decision_reason ?? "Reason not recorded in the operator projection.";
  const projectedStage = run.evidence.validation_stage;
  const validationStage = projectedStage && projectedStage !== "not_recorded" ? projectedStage : policy ? "policy_evaluation" : "not_recorded";
  return <section data-testid="decision-boundary" className="action-inspector rounded-xl border border-info/25 bg-void/35 p-4" aria-label="Action inspector">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="eyebrow text-info">Signature control</div><h2 className="mt-1 text-base font-semibold text-main">Proposed action</h2></div><Badge tone={isPending ? "warning" : isExecuted ? "success" : "neutral"}>{isPending ? "Awaiting confirmation" : isExecuted ? "Committed" : "Observation only"}</Badge></div>
    <div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="proposal-field"><span className="field-label">Tool proposal</span><strong className="mt-1 block font-mono text-sm text-main">{toolName}</strong><span className="mt-1 block text-xs text-muted">LLM output · untrusted proposal</span></div><div className="proposal-field"><span className="field-label">Action binding</span><strong className="mt-1 block font-mono text-sm text-main">{run.action_id ?? "Not recorded"}</strong><span className="mt-1 block text-xs text-muted">Server-owned identifier</span></div></div>
    <div className="mt-3 proposal-field"><span className="field-label">Arguments / result fields</span>{tool?.result_fields.length ? <div className="mt-2 flex flex-wrap gap-1.5">{tool.result_fields.map((field) => <Badge key={field} tone="neutral">{field}</Badge>)}</div> : <div className="mt-1 text-xs text-muted">Raw arguments are intentionally not exposed in the operator projection.</div>}</div>
    <div className="control-flow mt-4" aria-label="Decision authority flow"><div className="flow-node flow-proposal">LLM proposal</div><span className="flow-arrow">↓</span><div className="flow-node">Deterministic validation</div><span className="flow-arrow">↓</span><div className="flow-node">Policy decision</div><span className="flow-arrow">↓</span><div className="flow-node flow-authority">Execution authority</div></div>
    <div className="mt-4"><div className="field-label">Grounding and admissibility</div><div className="mt-2 grid gap-2 sm:grid-cols-2"><GroundingCheck label="Semantic grounding" tone={run.evidence.grounding.status === "grounded" || run.evidence.grounding.status === "symbolic" ? "success" : "neutral"} detail={run.evidence.grounding.status.replace(/_/g, " ")} /><GroundingCheck label="Target validation" tone={run.evidence.target_validation.status === "validated" || run.evidence.target_validation.status === "admissible" || run.evidence.target_validation.status === "admissible_symbolic_read" ? "success" : "neutral"} detail={run.evidence.target_validation.status.replace(/_/g, " ")} /><GroundingCheck label="Evidence retrieval" tone={run.rag_documents.length ? "success" : "neutral"} detail={run.rag_documents.length ? `${run.rag_documents.length} citation(s) recorded` : "No retrieval evidence recorded"} /><GroundingCheck label="Policy audit" tone={policy ? "success" : "neutral"} detail={policy ? "Decision event recorded" : "No policy event recorded"} /></div></div>
    <div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="decision-block"><div className="field-label">Decision</div><div data-testid="policy-decision" className="mt-1 font-mono text-sm font-semibold text-warning">{humanize(decision)}</div></div><div className="decision-block"><div className="field-label">Validation stage</div><div className="mt-1 font-mono text-sm text-main">{humanize(validationStage)}</div></div><div className="decision-block"><div className="field-label">Reason</div><div className="mt-1 text-xs leading-5 text-muted">{decisionReason}</div></div><div className="decision-block"><div className="field-label">Execution status</div><div data-testid="execution-result" className="mt-1"><StatusIndicator label={executionLabel} tone={executionTone} /></div><div className="mt-2 text-xs leading-5 text-muted">{isPending ? "A deterministic confirmation boundary prevents direct mutation." : "No execution command is exposed by this read-only console."}</div></div></div>
    <div className="mt-4"><div className="field-label">Operator actions</div><div className="mt-2 flex flex-wrap gap-2"><button type="button" className="operator-action" disabled><CheckCircle2 size={13} aria-hidden="true" />Approve</button><button type="button" className="operator-action" disabled><XCircle size={13} aria-hidden="true" />Deny</button><button type="button" className="operator-action" disabled><ShieldCheck size={13} aria-hidden="true" />Escalate</button></div><p className="mt-2 text-xs text-muted">Action commands are intentionally read-only in this operator projection.</p></div>
  </section>;
}

export function Inspector({ run, memoryRecords = [] }: { run: AgentRun | null; memoryRecords?: MemoryRecord[] }) {
  if (!run) return <Panel title="Action inspector" eyebrow="Execution authority"><EmptyState title="No run selected" description="Run an agent request to inspect the proposal, evidence, policy boundary, and trace." icon={Activity} /></Panel>;
  const overview = <div className="space-y-5"><ActionInspector run={run} /><div className="grid gap-5 lg:grid-cols-2"><div><SectionHeader title="Run metadata" description="Bounded identifiers for correlation." /><div className="divide-y divide-border/70"><DataRow label="Status" value={<StatusIndicator label={humanize(run.status)} tone={run.status === "completed" ? "success" : run.status === "waiting_confirmation" ? "warning" : run.status === "error" ? "danger" : "neutral"} compact />} /><DataRow label="Intent" value={run.intent} mono />{run.operation_type === "memory_summary" && <DataRow label="Operation" value="Memory summary · read-only" />}<DataRow label="Request type" value={humanize(run.request_type)} /><DataRow label="Conversation" value={run.conversation_id} mono /><DataRow label="Trace" value={run.trace_id ?? "not exported"} mono /></div></div><div><SectionHeader title="Observed path" description="Structured graph nodes only; no hidden reasoning." /><div className="path-strip">{run.path.length ? run.path.map((step, index) => <span className="path-step" key={`${step}-${index}`}>{step}{index < run.path.length - 1 && <span className="path-arrow" aria-hidden="true">›</span>}</span>) : <span className="text-xs text-muted">No execution path recorded.</span>}</div></div></div></div>;
  const tabs = [{ id: "overview", label: "Overview", icon: LockKeyhole, content: overview }, { id: "grounding", label: "Grounding", icon: ShieldCheck, content: <GroundingPanel run={run} /> }, { id: "policy", label: "Policy", icon: ShieldCheck, content: <PolicyPanel events={run.policy} embedded /> }, { id: "trace", label: "Trace", icon: GitBranch, content: <TraceTimeline events={run.trace} run={run} embedded /> }, { id: "memory", label: "Memory", icon: Brain, content: <MemoryPanel usage={run.memory} records={memoryRecords} embedded /> }, { id: "tools", label: "Tools", icon: Wrench, content: <ToolPanel tools={run.tools} embedded /> }];
  return <Panel title="Action inspector" eyebrow="Execution authority" description="The model proposes. Deterministic software decides what may execute."><Tabs tabs={tabs} /></Panel>;
}

export function Metric({ label, value }: { label: string; value: string }) { return <div className="flex items-center justify-between text-xs"><span className="text-muted">{label}</span><span className="font-mono text-main">{value}</span></div>; }
export function Empty({ text }: { text: string }) { return <EmptyState title="Nothing to inspect" description={text} />; }
