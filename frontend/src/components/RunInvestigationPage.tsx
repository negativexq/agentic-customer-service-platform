import { ArrowLeft, Copy, CheckCircle2, Download, FileText, GitCompareArrows, LockKeyhole, ShieldAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { AgentRun, DemoScenario, MemoryRecord } from "../types";
import { AgentLifecyclePanel, ConversationEvidence, DecisionCompilerCard, MemoryEvidenceCard, ProposalEvidenceCard, RagEvidenceCard } from "./AgentEvidenceShowcase";
import { DecisionExplanationCard, DecisionLifecycleSummary, EvidenceRelationshipGraph, InvestigationReportModal, OperationalTraceTimeline } from "./TraceInvestigation";
import { Badge, Card, DataRow, EmptyState, SectionHeader, StatusIndicator } from "./ui";
import { buildTraceStages, TraceStageTimeline } from "./TraceTimeline";
import { deriveRunSemantics } from "./runSemantics";

type Props = { run: AgentRun | null; demoScenario?: DemoScenario; memoryRecords: MemoryRecord[]; availableRuns: AgentRun[]; loading: boolean; error: string | null; onBack: () => void };

function statusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "completed") return "success";
  if (status === "waiting_confirmation") return "warning";
  if (status === "error" || status === "blocked" || status === "failed_validation") return "danger";
  return "neutral";
}

function humanize(value: string | null | undefined): string {
  return value ? value.replace(/_/g, " ") : "Not provided by backend projection";
}

function operatorStatus(run: AgentRun): string {
  const semanticStatus = deriveRunSemantics(run).status;
  const status = semanticStatus === "recorded" ? run.status : semanticStatus;
  return status === "needs_input" ? "Clarification required" : status === "waiting_confirmation" ? "Awaiting confirmation" : status === "blocked" ? "Prevented" : status === "failed_validation" ? "Validation failed" : status === "suspended" ? "Suspended" : status === "replaced" ? "Replaced" : status === "completed" ? "Completed" : humanize(run.status);
}

function operatorExecution(run: AgentRun): string {
  const semantics = deriveRunSemantics(run);
  if (semantics.status === "needs_input") return "Not attempted";
  if (semantics.status === "waiting_confirmation" || run.status === "waiting_confirmation") return "Awaiting confirmation";
  if (semantics.status === "completed") return "Completed";
  if (semantics.status === "blocked") return "Prevented";
  if (semantics.status === "failed_validation") return "Failed validation";
  return "Not authorized";
}

function FactRow({ label, available, detail }: { label: string; available: boolean; detail: string }) {
  return <div className="investigation-fact"><span className={available ? "text-success" : "text-muted"}>{available ? <CheckCircle2 size={14} aria-hidden="true" /> : <ShieldAlert size={14} aria-hidden="true" />}</span><div><div className="text-xs font-medium text-main">{label}</div><div className="mt-1 text-xs leading-5 text-muted">{detail}</div></div></div>;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character] ?? character);
}

function reportValue(value: string | null | undefined): string {
  return value ? escapeHtml(value) : "Not provided by backend projection";
}

function exportInvestigationReport(run: AgentRun) {
  const stages = buildTraceStages(run);
  const policy = run.policy[run.policy.length - 1];
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Investigation report · ${escapeHtml(run.run_id)}</title><style>body{font:14px system-ui,sans-serif;color:#17202b;background:#f6f8fa;max-width:980px;margin:0 auto;padding:32px}section{background:#fff;border:1px solid #dbe2e8;border-radius:10px;padding:20px;margin:16px 0}h1,h2{margin:0 0 8px}h1{font-size:24px}h2{font-size:17px}p,li{line-height:1.55;color:#536170}.meta{font-family:ui-monospace,monospace;color:#536170}.stage{border-top:1px solid #edf0f2;padding:12px 0}.stage:first-child{border-top:0}.status{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#286b4d}.unavailable{color:#7a8793;font-style:italic}.label{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:#7a8793}</style></head><body><div class="label">Agent control plane · bounded investigation export</div><h1>Run investigation report</h1><p class="meta">${escapeHtml(run.run_id)} · ${escapeHtml(new Date(run.started_at).toLocaleString())}</p><section><h2>Run overview</h2><p><strong>Status:</strong> ${reportValue(operatorStatus(run))}<br><strong>Intent:</strong> ${reportValue(run.intent)}<br><strong>Request:</strong> <span class="unavailable">Not provided by backend projection</span></p></section><section><h2>Decision timeline</h2>${stages.map((stage) => `<div class="stage"><strong>${escapeHtml(stage.label)}</strong> <span class="status">${escapeHtml(stage.status)}</span><p>${escapeHtml(stage.explanation)}</p>${stage.evidence ? `<p class="meta">Evidence: ${escapeHtml(stage.evidence)}</p>` : ""}</div>`).join("")}</section><section><h2>Evidence available</h2>${run.rag_documents.length ? `<ul>${run.rag_documents.map((document) => `<li>${escapeHtml(document.title)} · ${escapeHtml(document.source)} · ${escapeHtml(document.citation_id)}${Number.isFinite(document.score) ? ` · score ${document.score}` : ""}</li>`).join("")}</ul>` : `<p class="unavailable">Evidence unavailable in current projection.</p>`}<p><strong>Verified facts:</strong> <span class="unavailable">Not provided by backend projection.</span></p></section><section><h2>Deterministic decision and authority</h2><p><strong>Policy decision:</strong> ${reportValue(policy?.outcome)}<br><strong>Policy reason:</strong> ${policy?.reason_codes.length ? escapeHtml(policy.reason_codes.join(" · ")) : "Not provided by backend projection"}<br><strong>Authority:</strong> ${run.status === "waiting_confirmation" ? "Confirmation required · authority not granted" : run.evidence.write_outcome.status === "blocked" ? "Not authorized · prevented by deterministic controls" : "Not provided by backend projection"}<br><strong>Outcome:</strong> ${operatorExecution(run)}<br><strong>Execution attempt:</strong> ${run.status === "waiting_confirmation" || run.evidence.write_outcome.status === "blocked" ? "Not attempted" : reportValue(run.tools[0]?.status)}<br><strong>Safety status:</strong> ${run.status === "error" ? "Investigation error" : "No violation recorded in this projection"}</p></section><footer><p class="unavailable">This report contains bounded operator projection fields only. Prompts, raw provider responses, secrets, hidden reasoning, and model tokens are excluded.</p></footer></body></html>`;
  const url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${run.run_id}-investigation.html`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function PolicyExplanation({ run }: { run: AgentRun }) {
  const policy = run.policy[run.policy.length - 1];
  const risk = policy?.risk_level ?? run.tools[0]?.risk_level;
  const targetStatus = run.evidence.target_validation.status;
  return <Card as="section" className="p-5"><SectionHeader eyebrow="Deterministic authorization" title="Policy explanation" description="Structured policy audit fields only; internal reasoning is never displayed." />{policy ? <><div className="policy-decision-hero"><div><div className="field-label">Decision</div><div className="mt-1 font-mono text-lg font-semibold uppercase text-warning">{policy.outcome}</div></div><Badge tone={policy.outcome === "allow" ? "success" : policy.outcome === "deny" ? "danger" : "warning"}>Risk {risk ?? "—"}</Badge></div><div className="mt-4 rounded-lg border border-warning/20 bg-warning/5 p-3 text-xs leading-5 text-muted">{run.decision_reason ?? (policy.reason_codes.length ? policy.reason_codes.join(" · ") : "Policy reason unavailable in current projection.")}</div><div className="mt-4 space-y-2"><FactRow label="Target validation" available={targetStatus === "validated" || targetStatus === "admissible" || targetStatus === "admissible_symbolic_read"} detail={targetStatus.replace(/_/g, " ")} /><FactRow label="Required fields" available={targetStatus !== "not_recorded"} detail={run.evidence.compiler.reason ?? "Field-level validation details are not exposed by the projection."} /><FactRow label="Risk classification" available={typeof risk === "number"} detail={typeof risk === "number" ? `Risk level ${risk} recorded.` : "Not provided by backend projection."} /></div></> : <EmptyState title="No policy evaluation emitted" description="This run did not emit a policy audit event in the current projection." icon={LockKeyhole} />}</Card>;
}

function DecisionContextSummary({ run }: { run: AgentRun }) {
  const itemsUsed = run.memory.items_used ?? run.memory.retrieved_count ?? run.memory.item_count;
  const memoryRetrieved = run.memory.retrieved === true || itemsUsed > 0;
  const purpose = run.memory.purpose === "context_enrichment" || run.memory.context_usage === "context_enrichment" ? "Customer preference enrichment" : "Not recorded";
  const authorityImpact = run.memory.authority_influence === "none" ? "None" : humanize(run.memory.authority_influence);
  const execution = operatorExecution(run);
  return <section className="decision-context-summary" aria-label="Decision context summary"><div><div className="eyebrow text-info">Decision context</div><h2 className="mt-1 text-lg font-medium tracking-tight text-main">What happened, and what authority existed</h2><p className="mt-1 text-xs leading-5 text-muted">Context, decision impact, and execution state from the bounded operator projection.</p></div><div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><div className="decision-context-item"><span className="field-label">Memory</span><strong>{memoryRetrieved ? "✓ Retrieved" : "Not recorded"}</strong><small>{memoryRetrieved ? `${itemsUsed} bounded item${itemsUsed === 1 ? "" : "s"}` : "No memory stage in projection"}</small></div><div className="decision-context-item"><span className="field-label">Purpose</span><strong>{purpose}</strong><small>Context assembly only</small></div><div className="decision-context-item"><span className="field-label">Authority impact</span><strong>{authorityImpact}</strong><small>Memory cannot authorize execution.</small></div><div className="decision-context-item"><span className="field-label">Execution</span><strong>{execution}</strong><small>Bounded write outcome projection</small></div></div></section>;
}

function CompareRuns({ current, runs }: { current: AgentRun; runs: AgentRun[] }) {
  const [otherId, setOtherId] = useState(runs.find((run) => run.run_id !== current.run_id)?.run_id ?? "");
  const other = runs.find((run) => run.run_id === otherId) ?? null;
  const currentPolicy = current.policy[current.policy.length - 1]?.outcome ?? "Not recorded";
  const otherPolicy = other?.policy[other.policy.length - 1]?.outcome ?? "Not recorded";
  const currentExecution = operatorExecution(current);
  const otherExecution = other ? operatorExecution(other) : "Not recorded";
  return <Card as="section" className="p-5"><SectionHeader eyebrow="Evaluation comparison" title="Compare runs" description="Compare bounded outcomes and observed stages. This does not infer causality between runs." action={<GitCompareArrows size={18} className="text-info" aria-hidden="true" />} /><div className="compare-selects"><div><span className="field-label">Run A</span><div className="field-readonly font-mono">{current.run_id}</div></div><div><span className="field-label">Run B</span><select className="field-control mt-1.5" value={otherId} onChange={(event) => setOtherId(event.target.value)}><option value="">Select another run</option>{runs.filter((run) => run.run_id !== current.run_id).map((run) => <option value={run.run_id} key={run.run_id}>{run.run_id.slice(0, 16)} · {run.intent || run.request_type}</option>)}</select></div></div>{other ? <div className="compare-grid mt-5"><div className="compare-column"><div className="eyebrow">Run A · {current.run_id.slice(0, 12)}</div><CompareRow label="Policy result" value={currentPolicy} /><CompareRow label="Execution result" value={currentExecution} /><CompareRow label="Trace stages" value={`${current.trace.length} events`} /><CompareRow label="Final status" value={current.status} /></div><div className="compare-column"><div className="eyebrow">Run B · {other.run_id.slice(0, 12)}</div><CompareRow label="Policy result" value={otherPolicy} /><CompareRow label="Execution result" value={otherExecution} /><CompareRow label="Trace stages" value={`${other.trace.length} events`} /><CompareRow label="Final status" value={other.status} /></div><div className="compare-traces"><div className="eyebrow">Observed trace stages</div><div className="grid gap-4 lg:grid-cols-2"><div><div className="field-label">Run A</div><TraceStageTimeline run={current} compact /></div><div><div className="field-label">Run B</div><TraceStageTimeline run={other} compact /></div></div></div></div> : <EmptyState title="Select a second run" description="A comparison requires two available run projections." icon={GitCompareArrows} />}</Card>;
}

function CompareRow({ label, value }: { label: string; value: string }) {
  return <div className="data-row"><span className="text-muted">{label}</span><span className="max-w-[60%] truncate text-right text-xs capitalize text-main">{value.replace(/_/g, " ")}</span></div>;
}

export function RunInvestigationPage({ run, demoScenario, memoryRecords, availableRuns, loading, error, onBack }: Props) {
  const [reportOpen, setReportOpen] = useState(() => new URLSearchParams(window.location.search).get("report") === "1");
  const [tab, setTab] = useState<"evidence" | "policy" | "execution" | "telemetry">(() => {
    const query = new URLSearchParams(window.location.search);
    const requested = query.get("tab");
    return requested === "policy" || requested === "execution" || requested === "telemetry" ? requested : query.get("focus") === "timeline" ? "telemetry" : "evidence";
  });
  const selectTab = (next: typeof tab) => {
    setTab(next);
    const url = new URL(window.location.href);
    url.searchParams.set("tab", next);
    window.history.replaceState({}, "", url);
  };
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const scenario = useMemo(() => run ? demoScenario?.title || run.intent || run.request_type.replace(/_/g, " ") : "Run detail", [run, demoScenario]);
  useEffect(() => { if (!run) return; const focus = new URLSearchParams(window.location.search).get("focus"); const selector = focus === "evidence" ? '[aria-label="Evidence relationship graph"]' : focus === "timeline" ? '[aria-label="Operational trace timeline"]' : null; if (!selector) return; window.setTimeout(() => document.querySelector(selector)?.scrollIntoView({ block: "start" }), 0); }, [run]);
  if (loading) return <Card as="section" className="p-8"><div className="eyebrow">Run investigation</div><h1 className="section-title mt-2">Loading run projection…</h1></Card>;
  if (error || !run) return <Card as="section" className="p-8"><StatusIndicator label="Run unavailable" tone="danger" /><h1 className="section-title mt-3">Could not load this run</h1><p className="mt-2 text-sm text-muted">{error ?? "The backend returned no run projection."}</p><button type="button" className="button-primary mt-5" onClick={onBack}><ArrowLeft size={14} aria-hidden="true" />Back to console</button></Card>;
  return <div className="space-y-5"><section className="run-detail-header surface"><div className="flex flex-wrap items-center justify-between gap-3"><button type="button" className="back-link" onClick={onBack}><ArrowLeft size={14} aria-hidden="true" />Back to Runs & traces</button><div className="flex flex-wrap gap-2"><button type="button" className="operator-action" onClick={() => setReportOpen(true)}><FileText size={14} aria-hidden="true" />View investigation report</button><button type="button" className="operator-action" onClick={() => exportInvestigationReport(run)}><Download size={14} aria-hidden="true" />Export investigation report</button></div></div><div className="mt-5 flex flex-wrap items-start justify-between gap-5"><div><div className="eyebrow">Run investigation</div><h1 className="mt-2 text-3xl font-semibold tracking-tight text-main">{scenario.replace(/_/g, " ")} · {operatorStatus(run)}</h1><p className="mt-3 max-w-3xl text-sm leading-6 text-muted">{run.decision_reason ?? "Inspect the recorded decision, its supporting evidence, and execution authority."}</p><div className="run-id-copy"><code className="select-text" data-testid="investigation-run-id">{run.run_id}</code><button type="button" onClick={async () => { try { await navigator.clipboard.writeText(run.run_id); setCopyState("copied"); } catch { setCopyState("failed"); } }} aria-label="Copy run ID"><Copy size={13} aria-hidden="true" />Copy ID</button><span role="status" aria-live="polite">{copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy unavailable; select the ID." : ""}</span></div><div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted"><span className="capitalize">{scenario}</span><span className="text-border">·</span><span>{new Date(run.started_at).toLocaleString()}</span><span className="text-border">·</span><span>customer #{run.customer_id}</span></div></div><StatusIndicator label={operatorStatus(run)} tone={statusTone(deriveRunSemantics(run).status === "recorded" ? run.status : deriveRunSemantics(run).status)} /></div></section><DecisionLifecycleSummary run={run} /><div className="investigation-tabs" role="tablist" aria-label="Investigation sections">{(["evidence", "policy", "execution", "telemetry"] as const).map((item) => <button type="button" role="tab" id={`tab-${item}`} aria-controls={`panel-${item}`} aria-selected={tab === item} tabIndex={tab === item ? 0 : -1} key={item} onClick={() => selectTab(item)} onKeyDown={(event) => { const tabs = ["evidence", "policy", "execution", "telemetry"] as const; const index = tabs.indexOf(item); const next = event.key === "ArrowRight" ? tabs[(index + 1) % tabs.length] : event.key === "ArrowLeft" ? tabs[(index + tabs.length - 1) % tabs.length] : event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : null; if (next) { event.preventDefault(); selectTab(next); document.getElementById(`tab-${next}`)?.focus(); } }}><span>{item.charAt(0).toUpperCase() + item.slice(1)}</span></button>)}</div><section role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0} className="space-y-5">{tab === "evidence" && <><ConversationEvidence run={run} messages={demoScenario?.messages} /><EvidenceRelationshipGraph run={run} /><div className="grid gap-5 xl:grid-cols-2"><RagEvidenceCard run={run} /><MemoryEvidenceCard run={run} records={memoryRecords} /></div></>}{tab === "policy" && <><DecisionExplanationCard run={run} /><div className="grid gap-5 xl:grid-cols-2"><PolicyExplanation run={run} /><DecisionCompilerCard run={run} /></div><ProposalEvidenceCard run={run} proposal={run.proposal} /></>}{tab === "execution" && <><DecisionContextSummary run={run} /><AgentLifecyclePanel run={run} /><CompareRuns current={run} runs={availableRuns} /></>}{tab === "telemetry" && <><OperationalTraceTimeline run={run} /><Card as="section" className="p-5"><SectionHeader eyebrow="Persistence" title="Run metadata" description="Safe identifiers returned by the operator projection." /><div className="divide-y divide-border/70"><DataRow label="Request ID" value={run.request_id} mono /><DataRow label="Action ID" value={run.action_id ?? "Not recorded"} mono /><DataRow label="Duration" value={run.duration_ms > 0 ? `${run.duration_ms.toFixed(0)} ms` : run.run_id.startsWith("demo-") ? "Not applicable" : "Not recorded"} /><DataRow label="Conversation" value={run.conversation_id} mono /><DataRow label="Trace ID" value={run.trace_id ?? "Not recorded"} mono /><DataRow label="Failure category" value={humanize(run.failure_category)} /><DataRow label="Recovery action" value={humanize(run.recovery_action)} /></div></Card></>}</section>{reportOpen && <InvestigationReportModal run={run} onClose={() => setReportOpen(false)} onExport={() => exportInvestigationReport(run)} />}</div>;
}
