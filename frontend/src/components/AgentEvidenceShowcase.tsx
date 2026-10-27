import { ArrowDown, Brain, CheckCircle2, CircleAlert, CircleDot, FileText, LockKeyhole, MessageSquare, ShieldCheck, Sparkles } from "lucide-react";
import type { AgentProposal, AgentRun, DemoConversationMessage, DemoMemoryEvidence, DemoScenario, MemoryRecord, MemoryUsage } from "../types";
import { Badge, Card, EmptyState, SectionHeader, StatusIndicator } from "./ui";

type Tone = "success" | "warning" | "danger" | "info" | "neutral";

function humanize(value: string | null | undefined): string {
  return value ? value.replace(/_/g, " ") : "Not recorded";
}

function displayStatus(value: string | null | undefined): string {
  if (value === "waiting_confirmation" || value === "waiting" || value === "pending_confirmation") return "Awaiting confirmation";
  if (value === "blocked") return "Blocked before execution";
  if (value === "not_attempted") return "Not attempted";
  return humanize(value);
}

function memoryCount(usage: MemoryUsage): number {
  return usage.items_used ?? usage.retrieved_count ?? usage.item_count;
}

function memoryRetrieved(usage: MemoryUsage): boolean {
  return usage.retrieved === true || memoryCount(usage) > 0;
}

function toneFor(status: string): Tone {
  if (status === "completed" || status === "used" || status === "available" || status === "passed") return "success";
  if (status === "waiting" || status === "blocked" || status === "pending" || status === "required") return "warning";
  if (status === "failed" || status === "rejected" || status === "denied") return "danger";
  return "neutral";
}

function StateIcon({ status }: { status: string }) {
  if (status === "completed" || status === "used" || status === "available" || status === "passed") return <CheckCircle2 size={15} aria-hidden="true" />;
  if (status === "waiting" || status === "blocked" || status === "pending" || status === "required" || status === "failed") return <CircleAlert size={15} aria-hidden="true" />;
  return <CircleDot size={15} aria-hidden="true" />;
}

type ConversationProps = {
  run: AgentRun;
  request?: string;
  response?: string;
  messages?: DemoConversationMessage[];
};

export function ConversationEvidence({ run, request, response, messages = [] }: ConversationProps) {
  const hasConversationContent = Boolean(request?.trim() || response?.trim());
  const rows = messages.length > 0
    ? messages.map((message) => ({ ...message, label: message.role === "customer" ? "Customer" : "Agent", body: message.content }))
    : hasConversationContent
    ? [
        { role: "customer" as const, label: "Customer", body: request?.trim() || "Message not recorded" },
        { role: "agent" as const, label: "Agent", body: response?.trim() || "Response not recorded" },
      ]
    : [
        { role: "customer" as const, label: "Customer", body: "Message content is not provided by the run projection." },
        { role: "agent" as const, label: "Agent", body: `Bounded response state: ${displayStatus(run.status)}.` },
      ];

  return (
    <section className="surface p-5" aria-label="Conversation evidence" data-testid="conversation-evidence">
      <SectionHeader eyebrow="Conversation" title="Customer and agent exchange" description="Visible conversation content is limited to recorded demo text or the current UI request; internal prompts and hidden reasoning are never shown." />
      <div className="conversation-evidence mt-4">
        {rows.map((row, index) => (
          <div data-testid={row.role === "customer" ? "conversation-request" : "agent-response"} className={`conversation-evidence-row conversation-evidence-${row.role}`} key={`${row.role}-${index}`}>
            <div className="conversation-evidence-icon">{row.role === "customer" ? <MessageSquare size={15} aria-hidden="true" /> : <Sparkles size={15} aria-hidden="true" />}</div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold text-main">{row.label}</span><Badge tone={row.role === "customer" ? "neutral" : "info"}>{row.role === "customer" ? "request" : "bounded response"}</Badge>{"evidence_tags" in row && row.evidence_tags?.map((tag) => <Badge tone="neutral" key={tag}>{tag}</Badge>)}</div>
              <p className="mt-2 text-sm leading-6 text-main">{row.body}</p>
              {"state" in row && row.state && <small className="mt-2 block text-xs text-muted">State: {displayStatus(row.state)}</small>}
              {"timestamp" in row && row.timestamp && <small className="mt-1 block font-mono text-xs text-muted">{new Date(row.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small>}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-muted"><span>State</span><Badge tone={toneFor(run.status)}>{displayStatus(run.status)}</Badge><span>·</span><span>Intent {humanize(run.intent)}</span></div>
    </section>
  );
}

type LifecycleStage = {
  id: string;
  label: string;
  status: string;
  explanation: string;
  evidence: string;
  detail: string;
};

function stageEvent(run: AgentRun, stage: string) {
  return run.trace.filter((event) => event.stage === stage);
}

function buildLifecycle(run: AgentRun): LifecycleStage[] {
  const request = stageEvent(run, "user_request");
  const context = stageEvent(run, "context_retrieval");
  const memory = stageEvent(run, "memory_context");
  const policy = run.policy[run.policy.length - 1];
  const confirmation = stageEvent(run, "confirmation");
  const authority = stageEvent(run, "execution_authority");
  const memoryUsed = memoryRetrieved(run.memory);
  const ragAvailable = run.rag_documents.length > 0;
  const proposal = run.proposal;
  const writeStatus = run.evidence.write_outcome.status;

  return [
    { id: "request", label: "Request", status: request.length ? "completed" : "not_recorded", explanation: request.length ? "Request entered the bounded run projection." : "Request event is not recorded in the current projection.", evidence: request.length ? "Request event available" : "Not recorded", detail: "Only bounded request metadata is shown here." },
    { id: "context", label: "Context", status: context.length || memoryUsed || ragAvailable ? "completed" : "not_recorded", explanation: context.length ? "Context retrieval was observed." : "No context retrieval event was recorded.", evidence: context.length ? "Retrieval stage available" : "Not recorded", detail: "Context sources inform the proposal but cannot authorize execution." },
    { id: "memory", label: "Memory", status: memoryUsed || memory.length ? "completed" : "not_recorded", explanation: memoryUsed ? `${memoryCount(run.memory)} bounded item${memoryCount(run.memory) === 1 ? "" : "s"} available for context enrichment.` : "No memory item was recorded for this run.", evidence: memoryUsed ? "Memory metadata available" : "Not recorded", detail: "Memory enriches context only; authority impact is none." },
    { id: "rag", label: "RAG evidence", status: ragAvailable ? "completed" : context.length ? "blocked" : "not_recorded", explanation: ragAvailable ? `${run.rag_documents.length} document metadata record${run.rag_documents.length === 1 ? "" : "s"} available.` : context.length ? "Retrieval ran, but document metadata is unavailable." : "No retrieval stage was recorded.", evidence: ragAvailable ? `${run.rag_documents.length} citation record${run.rag_documents.length === 1 ? "" : "s"}` : "Not recorded", detail: "Evidence supports grounding; it does not grant execution authority." },
    { id: "proposal", label: "Proposal", status: proposal ? proposal.validation === "rejected" ? "blocked" : "completed" : "not_recorded", explanation: proposal ? `Model proposal validation: ${humanize(proposal.validation)}.` : "No model proposal is available in this projection.", evidence: proposal?.suggested_action ?? "Not recorded", detail: "Model output is an untrusted semantic suggestion." },
    { id: "decision", label: "Decision", status: run.evidence.compiler.status !== "not_recorded" || policy ? "completed" : proposal?.validation === "rejected" ? "blocked" : "not_recorded", explanation: run.evidence.compiler.status !== "not_recorded" ? `Compiler status: ${humanize(run.evidence.compiler.status)}.` : policy ? "Policy evidence indicates a deterministic decision path." : "No compiler decision is recorded.", evidence: run.evidence.compiler.selected_tool ?? "Not recorded", detail: run.evidence.compiler.reason ?? "Decision produced by deterministic controls." },
    { id: "policy", label: "Policy", status: policy ? policy.outcome === "deny" ? "blocked" : "completed" : "not_recorded", explanation: policy ? `Policy outcome: ${humanize(policy.outcome)}.` : "No policy evaluation was emitted.", evidence: policy?.reason_codes.join(" · ") || "Not recorded", detail: policy ? "Policy audit metadata is available; hidden reasoning is excluded." : "Policy evidence is unavailable from the current projection." },
    { id: "confirmation", label: "Confirmation", status: run.status === "waiting_confirmation" || run.evidence.confirmation.required ? "waiting" : confirmation.length ? "completed" : "not_recorded", explanation: run.status === "waiting_confirmation" ? "Explicit confirmation is required before the covered mutation." : run.evidence.confirmation.required ? "Confirmation requirement was recorded." : "No confirmation lifecycle is recorded.", evidence: run.evidence.confirmation.status || "Not recorded", detail: "Confirmation is a deterministic authority boundary." },
    { id: "authority", label: "Execution boundary", status: writeStatus === "executed" || authority.length ? "completed" : run.status === "waiting_confirmation" || writeStatus === "pending_confirmation" ? "blocked" : "not_recorded", explanation: writeStatus === "executed" ? "Controlled runtime execution was recorded." : run.status === "waiting_confirmation" || writeStatus === "pending_confirmation" ? "Execution remains blocked until confirmation." : "No execution authority event was recorded.", evidence: writeStatus || "Not recorded", detail: "Only controlled runtime paths can commit business effects." },
  ];
}

export function AgentLifecyclePanel({ run }: { run: AgentRun }) {
  const stages = buildLifecycle(run);
  return <section className="surface p-5" aria-label="Agent lifecycle" data-testid="lifecycle-panel"><SectionHeader eyebrow="Agent lifecycle" title="From request to authority" description="Each stage is derived from the existing run projection. Missing stages remain explicit." /><div className="agent-lifecycle mt-4">{stages.map((stage, index) => <div className="agent-lifecycle-item" key={stage.id}><div className="agent-lifecycle-marker"><StateIcon status={stage.status} /></div><div className="min-w-0 flex-1"><details open={stage.status === "waiting" || stage.status === "blocked" || index === 0}><summary className="agent-lifecycle-summary"><span><strong>{stage.label}</strong><small>{stage.explanation}</small></span><StatusIndicator label={displayStatus(stage.status)} tone={toneFor(stage.status)} compact /></summary><div className="agent-lifecycle-detail"><div><span className="field-label">Evidence</span><p className="mt-1 text-xs text-info">{stage.evidence}</p></div><div className="mt-2"><span className="field-label">Boundary</span><p className="mt-1 text-xs leading-5 text-muted">{stage.detail}</p></div></div></details></div>{index < stages.length - 1 && <ArrowDown className="agent-lifecycle-arrow" size={14} aria-hidden="true" />}</div>)}</div></section>;
}

export function MemoryEvidenceCard({ run, records = [], demoItems = [] }: { run: AgentRun; records?: MemoryRecord[]; demoItems?: DemoMemoryEvidence[] }) {
  const count = memoryCount(run.memory);
  const retrieved = memoryRetrieved(run.memory);
  const category = demoItems[0]?.category || records[0]?.memory_type || run.memory.types[0] || "Not recorded";
  const purpose = run.memory.purpose === "context_enrichment" || run.memory.context_usage === "context_enrichment" ? "Customer preference enrichment" : "Not recorded";
  return <Card as="section" className="p-5" aria-label="Memory evidence"><SectionHeader eyebrow="Context evidence" title="Memory context" description="Bounded customer-state metadata only; raw memory content stays hidden." action={<Badge tone={retrieved ? "info" : "neutral"}>{retrieved ? "Recorded" : "Not recorded"}</Badge>} /><div className="memory-evidence-grid mt-4"><div><span className="field-label">Retrieved items</span><strong>{retrieved ? count : "Not recorded"}</strong></div><div><span className="field-label">Category</span><strong>{humanize(category)}</strong></div><div><span className="field-label">Purpose</span><strong>{purpose}</strong></div><div><span className="field-label">Authority</span><strong>None</strong></div></div>{demoItems.length > 0 && <div className="mt-4 space-y-2">{demoItems.map((item, index) => <div className="memory-evidence-detail" key={`${item.category}-${index}`}><strong>{item.summary}</strong><small>{item.source} · {item.authority} · {item.purpose}</small></div>)}</div>}{records.length > 0 && <div className="mt-4 flex flex-wrap gap-2">{records.map((record) => <Badge tone="neutral" key={record.id}>{record.normalized_key}</Badge>)}</div>}<div className="notice notice-info mt-4"><Brain size={15} aria-hidden="true" /><span>Memory enriches context but cannot authorize actions, override policy, or bypass confirmation.</span></div></Card>;
}

export function RagEvidenceCard({ run }: { run: AgentRun }) {
  const hasRetrieval = run.rag_documents.length > 0;
  return <section className="surface p-5" aria-label="RAG evidence" data-testid="evidence-panel"><SectionHeader eyebrow="Context evidence" title="RAG grounding" description="Document metadata and citations are shown when the backend projection provides them." action={<Badge tone={hasRetrieval ? "success" : "neutral"}>{hasRetrieval ? "Grounded" : "Not recorded"}</Badge>} />{hasRetrieval ? <div data-testid="grounding-status" className="mt-4 space-y-2">{run.rag_documents.map((document) => <div className="rag-evidence-row" key={document.citation_id}><FileText size={15} className="shrink-0 text-info" aria-hidden="true" /><div className="min-w-0 flex-1"><strong>{document.title}</strong><small>{document.source} · {document.section}</small><small className="font-mono text-info">Chunk {document.chunk_id ?? document.citation_id} · score {document.score.toFixed(2)}</small>{document.document_version && <small>Version {document.document_version} · grounding {document.grounding_status ?? "Not recorded"}</small>}</div></div>)}</div> : <EmptyState title="No retrieval evidence recorded" description="Evidence is unavailable from the current run projection." icon={FileText} />}<div className="notice notice-info mt-4"><ShieldCheck size={15} aria-hidden="true" /><span>Evidence supports grounding. It does not grant execution authority.</span></div></section>;
}

export function ProposalEvidenceCard({ run, proposal, confidence }: { run: AgentRun; proposal?: AgentProposal | null; confidence?: number | null }) {
  const metadata = run.provider_metadata;
  return <Card as="section" className="p-5" aria-label="Model proposal"><SectionHeader eyebrow="Untrusted model output" title="LLM proposal" description="Semantic suggestion only; deterministic controls decide admissibility." action={<Badge tone="warning">Not execution authority</Badge>} />{proposal ? <div className="proposal-evidence-grid mt-4"><div><span className="field-label">Intent</span><strong>{humanize(proposal.intent)}</strong></div><div><span className="field-label">Suggested action</span><strong>{proposal.suggested_action || "Not recorded"}</strong></div><div><span className="field-label">Validation</span><strong>{humanize(proposal.validation)}</strong></div><div><span className="field-label">Provider</span><strong>{run.provider || "Not recorded"}</strong></div><div><span className="field-label">Model</span><strong>{run.model || metadata?.model || "Not recorded"}</strong></div><div><span className="field-label">Latency</span><strong>{metadata?.latency_ms !== null && metadata?.latency_ms !== undefined ? `${metadata.latency_ms} ms` : "Not recorded"}</strong></div><div><span className="field-label">Confidence</span><strong>{confidence !== null && confidence !== undefined ? confidence.toFixed(2) : "Not recorded"}</strong></div><div><span className="field-label">Evidence references</span><strong>{proposal.evidence_references.length || "Not recorded"}</strong></div></div> : <EmptyState title="No model proposal recorded" description="The current run projection does not contain a bounded proposal." icon={Sparkles} />}<div className="notice notice-warning mt-4"><Sparkles size={15} aria-hidden="true" /><span>Model output is untrusted until validated by deterministic controls.</span></div></Card>;
}

export function DecisionCompilerCard({ run }: { run: AgentRun }) {
  const policy = run.policy[run.policy.length - 1];
  const targetStatus = run.evidence.target_validation.status;
  const checks = [
    ["Target validation", targetStatus !== "not_recorded", humanize(targetStatus)],
    ["Required fields", run.evidence.compiler.status !== "not_recorded", run.evidence.compiler.reason || "Field-level details are not recorded."],
    ["Policy compatibility", Boolean(policy), policy ? humanize(policy.outcome) : "Not recorded"],
    ["Risk classification", typeof policy?.risk_level === "number", typeof policy?.risk_level === "number" ? `Risk ${policy.risk_level}` : "Not recorded"],
  ] as const;
  return <Card as="section" className="p-5" aria-label="Decision compiler"><SectionHeader eyebrow="Deterministic controls" title="Decision validation" description="Compiler, target, and policy projections are system-owned." /><div className="decision-compiler-result mt-4"><div><span className="field-label">Result</span><strong>{policy?.outcome ? humanize(policy.outcome).toUpperCase() : "Not recorded"}</strong></div><Badge tone={policy ? toneFor(policy.outcome) : "neutral"}>{run.status === "waiting_confirmation" ? "Confirmation boundary" : "Deterministic result"}</Badge></div><div className="mt-4 grid gap-2 sm:grid-cols-2">{checks.map(([label, available, detail]) => <div className="decision-check" key={label}><span className={available ? "text-success" : "text-muted"}>{available ? <CheckCircle2 size={14} aria-hidden="true" /> : <CircleDot size={14} aria-hidden="true" />}</span><div><strong>{label}</strong><small>{detail}</small></div></div>)}</div><div className="notice notice-info mt-4"><LockKeyhole size={15} aria-hidden="true" /><span>Decision produced by deterministic controls; no model output can bypass this boundary.</span></div></Card>;
}

export function ScenarioSummaryHeader({ run }: { run: AgentRun }) {
  const memory = memoryRetrieved(run.memory);
  const rag = run.rag_documents.length > 0 || run.trace.some((event) => event.stage === "context_retrieval");
  const policy = run.policy[run.policy.length - 1]?.outcome;
  const execution = run.status === "waiting_confirmation" || run.evidence.write_outcome.status === "pending_confirmation" ? "Blocked before execution" : run.evidence.write_outcome.status === "executed" ? "Completed" : "Not attempted";
  return <section className="scenario-summary-header" aria-label="Scenario summary"><div><div className="eyebrow">Scenario</div><h2>{humanize(run.intent)} with policy validation</h2><p>Human-readable summary of the current bounded run projection.</p></div><div className="scenario-summary-grid"><div><span className="field-label">Intent</span><strong>{humanize(run.intent)}</strong></div><div><span className="field-label">Memory</span><strong>{memory ? "Used" : "Not recorded"}</strong></div><div><span className="field-label">RAG</span><strong>{rag ? "Available" : "Not recorded"}</strong></div><div><span className="field-label">Decision</span><strong>{policy ? humanize(policy) : "Not recorded"}</strong></div><div><span className="field-label">Execution</span><strong>{execution}</strong></div></div></section>;
}

export function DemoEvidencePreview({ scenario }: { scenario: DemoScenario }) {
  return <div className="space-y-5"><div className="notice notice-info"><ShieldCheck size={15} aria-hidden="true" /><span>Recorded demo evidence · no runtime execution or business mutation is performed.</span></div><section className="surface p-5"><div className="eyebrow">Production-style scenario</div><h2 className="mt-1 text-lg font-semibold text-main">{scenario.title}</h2><p className="mt-2 text-sm leading-6 text-muted">{scenario.purpose} {scenario.expected}</p></section><ScenarioSummaryHeader run={scenario.run} /><ConversationEvidence run={scenario.run} messages={scenario.messages} /><div className="grid gap-5 xl:grid-cols-2"><MemoryEvidenceCard run={scenario.run} demoItems={scenario.memory_evidence} /><RagEvidenceCard run={scenario.run} /></div><div className="grid gap-5 xl:grid-cols-2"><ProposalEvidenceCard run={scenario.run} proposal={scenario.run.proposal} confidence={scenario.proposal_confidence} /><DecisionCompilerCard run={scenario.run} /></div><AgentLifecyclePanel run={scenario.run} /></div>;
}
