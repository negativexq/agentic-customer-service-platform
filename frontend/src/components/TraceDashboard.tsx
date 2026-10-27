import { Activity, CheckCircle2, Clock3, Database, FlaskConical, GitBranch, LockKeyhole } from "lucide-react";
import { useMemo, useState } from "react";
import type { AgentRun, PlaygroundHistoryItem } from "../types";
import { Badge, Card, EmptyState, SectionHeader, StatusIndicator } from "./ui";
import { deriveRunSemantics } from "./runSemantics";

type Props = {
  runs: AgentRun[];
  recordedRuns?: AgentRun[];
  playgroundRuns?: PlaygroundHistoryItem[];
  loading?: boolean;
  error?: string | null;
  onSelect: (run: AgentRun) => void;
  onOpenPlayground?: () => void;
};

function humanize(value: string | null | undefined): string {
  return value ? value.replace(/_/g, " ") : "Not recorded";
}

function clarificationRequired(run: AgentRun): boolean {
  return run.request_type === "unclear" || run.evidence?.compiler?.status === "clarification_required" || run.evidence?.target_validation?.status === "missing_required_information";
}

function policyOutcome(run: AgentRun): string {
  if (clarificationRequired(run)) return "Clarification required";
  return humanize(run.policy?.[run.policy.length - 1]?.outcome ?? run.evidence?.compiler?.status);
}

function executionOutcome(run: AgentRun): string {
  const semantics = deriveRunSemantics(run);
  if (semantics.status === "needs_input") return "Not attempted";
  if (semantics.status === "waiting_confirmation") return "Awaiting confirmation";
  if (semantics.status === "completed") return "Completed";
  if (semantics.status === "failed_validation") return "Failed validation";
  if (semantics.status === "blocked") return "Prevented";
  if (semantics.status === "suspended") return "Suspended";
  if (semantics.status === "replaced") return "Replaced";
  if (run.tools?.length > 0) return humanize(run.tools[run.tools.length - 1].status);
  return "Not authorized";
}

function authorityOutcome(run: AgentRun): string {
  const authority = deriveRunSemantics(run).authority;
  if (authority === "read_access") return "Read access";
  if (authority === "controlled_execution") return "Granted · controlled path";
  if (authority === "confirmation_required") return "Confirmation required";
  return "Not granted";
}

function statusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "completed") return "success";
  if (status === "waiting_confirmation") return "warning";
  if (status === "error") return "danger";
  return "neutral";
}

function statusPresentation(run: AgentRun): { label: string; tone: "success" | "warning" | "danger" | "neutral"; description: string } {
  const semantics = deriveRunSemantics(run);
  if (semantics.status === "needs_input") return { label: "Clarification required", tone: "warning", description: "Required target information is missing" };
  if (semantics.status === "waiting_confirmation") return { label: "Awaiting confirmation", tone: "warning", description: "Human approval required before mutation" };
  if (semantics.status === "blocked") return { label: "Prevented", tone: "danger", description: "Policy or scope controls prevented execution" };
  if (semantics.status === "failed_validation") return { label: "Validation failed", tone: "warning", description: "Business validation prevented an execution attempt" };
  if (semantics.status === "suspended") return { label: "Suspended", tone: "neutral", description: "The workflow is paused while another request is handled" };
  if (semantics.status === "replaced") return { label: "Replaced", tone: "neutral", description: "A newer workflow superseded this request" };
  if (semantics.status === "completed") return { label: "Completed", tone: "success", description: "Evidence snapshot completed" };
  return { label: humanize(run.status), tone: statusTone(run.status), description: "Status recorded in the bounded projection" };
}

function sourceLabel(run: AgentRun): string {
  return run.run_id.startsWith("demo-") ? "Evidence snapshot" : "Operator projection";
}

type FilterValue = "all" | "completed" | "waiting_confirmation" | "blocked" | "require_confirmation" | "deny" | "clarification_required" | "refund-request" | "prompt-injection" | "duplicate-operation" | "missing-information";

function scenarioKey(run: AgentRun): string {
  if (run.run_id.includes("prompt-injection")) return "prompt-injection";
  if (run.run_id.includes("duplicate-operation")) return "duplicate-operation";
  if (run.run_id.includes("missing-information")) return "missing-information";
  return "refund-request";
}

function scenarioLabel(run: AgentRun): string {
  const labels: Record<string, string> = { "prompt-injection": "Prompt injection", "duplicate-operation": "Duplicate operation", "missing-information": "Missing information", "refund-request": "Refund request" };
  return labels[scenarioKey(run)] ?? humanize(run.intent || run.request_type);
}

function scenarioTone(run: AgentRun): "info" | "danger" | "warning" | "neutral" {
  const key = scenarioKey(run);
  if (key === "prompt-injection") return "danger";
  if (key === "duplicate-operation") return "warning";
  if (key === "missing-information") return "neutral";
  return "info";
}

function decisionKey(run: AgentRun): string {
  return run.policy?.[run.policy.length - 1]?.outcome ?? run.evidence?.compiler?.status ?? "not_recorded";
}

function matchesFilter(run: AgentRun, value: FilterValue): boolean {
  const semantics = deriveRunSemantics(run);
  if (value === "all") return true;
  if (value === "completed") return semantics.status === "completed";
  if (value === "waiting_confirmation") return semantics.status === "waiting_confirmation";
  if (value === "blocked") return semantics.status === "blocked" || semantics.status === "failed_validation";
  return decisionKey(run) === value;
}

function FilterSelect({ label, value, onChange, options }: { label: string; value: FilterValue; onChange: (value: FilterValue) => void; options: Array<[FilterValue, string]> }) {
  return <label className="trace-filter"><span className="field-label">{label}</span><select className="field-control mt-1.5" value={value} onChange={(event) => onChange(event.target.value as FilterValue)}>{options.map(([optionValue, optionLabel]) => <option value={optionValue} key={optionValue}>{optionLabel}</option>)}</select></label>;
}

function durationLabel(run: AgentRun): string {
  return run.duration_ms > 0 ? `${run.duration_ms.toFixed(0)} ms` : run.run_id.startsWith("demo-") ? "Not applicable" : "Not recorded";
}

function timestampLabel(run: AgentRun): string {
  return new Date(run.started_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function RunStatus({ run }: { run: AgentRun }) {
  const presentation = statusPresentation(run);
  return <span title={presentation.description} aria-label={`${presentation.label}: ${presentation.description}`}><StatusIndicator label={presentation.label} tone={presentation.tone} compact /></span>;
}

function TraceAvailability({ run }: { run: AgentRun }) {
  return run.trace?.length > 0 ? <Badge tone="success"><span className="inline-flex items-center gap-1.5"><CheckCircle2 size={12} aria-hidden="true" />Available</span></Badge> : <Badge tone="neutral"><span className="inline-flex items-center gap-1.5"><LockKeyhole size={12} aria-hidden="true" />Not recorded</span></Badge>;
}

function RegistryRow({ run, onSelect }: { run: AgentRun; onSelect: (run: AgentRun) => void }) {
  return <tr className="trace-run-row" onClick={() => onSelect(run)} tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onSelect(run); }}>
    <td className="px-4 py-3 font-mono text-xs text-main">{run.run_id}</td>
    <td className="px-4 py-3 text-xs text-main"><span className="inline-flex items-center gap-2"><Badge tone={scenarioTone(run)}>{scenarioLabel(run)}</Badge></span></td>
    <td className="px-4 py-3 font-mono text-xs text-muted">{run.intent || "Not recorded"}</td>
    <td className="px-4 py-3"><RunStatus run={run} /></td>
    <td className="px-4 py-3 text-xs capitalize text-main">{policyOutcome(run)}</td>
    <td className="px-4 py-3 text-xs text-muted">{executionOutcome(run)}</td>
    <td className="px-4 py-3 text-xs text-muted">{authorityOutcome(run)}</td>
    <td className="px-4 py-3 text-xs text-muted">{durationLabel(run)}</td>
    <td className="px-4 py-3 text-xs text-muted">{timestampLabel(run)}</td>
    <td className="px-4 py-3"><TraceAvailability run={run} /></td>
  </tr>;
}

function RegistryCard({ run, onSelect }: { run: AgentRun; onSelect: (run: AgentRun) => void }) {
  return <button type="button" className="trace-run-card w-full text-left" onClick={() => onSelect(run)}>
    <div className="flex flex-wrap items-start justify-between gap-3"><div><span className="font-mono text-xs text-main">{run.run_id}</span><div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted"><Badge tone={scenarioTone(run)}>{scenarioLabel(run)}</Badge><span>{humanize(run.intent || run.request_type)}</span></div></div><RunStatus run={run} /></div>
    <div className="mt-4 grid grid-cols-2 gap-3">
      <div><div className="field-label">Scenario</div><div className="mt-1 truncate text-xs text-main">{humanize(run.intent || run.request_type)}</div></div>
      <div><div className="field-label">Intent</div><div className="mt-1 truncate text-xs text-muted">{humanize(run.intent)}</div></div>
      <div><div className="field-label">Decision</div><div className="mt-1 truncate text-xs capitalize text-main">{policyOutcome(run)}</div></div>
      <div><div className="field-label">Execution</div><div className="mt-1 truncate text-xs text-muted">{executionOutcome(run)}</div></div>
      <div><div className="field-label">Authority</div><div className="mt-1 truncate text-xs text-muted">{authorityOutcome(run)}</div></div>
      <div><div className="field-label">Duration</div><div className="mt-1 truncate text-xs text-muted">{durationLabel(run)}</div></div>
    </div>
    <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border/70 pt-3"><Badge tone="neutral">{sourceLabel(run)}</Badge><span className="text-xs text-muted">{timestampLabel(run)}</span></div>
  </button>;
}

export function TraceDashboard({ runs, recordedRuns = [], playgroundRuns = [], loading = false, error = null, onSelect, onOpenPlayground }: Props) {
  const queryFilters = useMemo(() => new URLSearchParams(typeof window === "undefined" ? "" : window.location.search), []);
  const [statusFilter, setStatusFilter] = useState<FilterValue>(() => (queryFilters.get("status") as FilterValue) || "all");
  const [decisionFilter, setDecisionFilter] = useState<FilterValue>(() => (queryFilters.get("decision") as FilterValue) || "all");
  const [scenarioFilter, setScenarioFilter] = useState<FilterValue>(() => (queryFilters.get("scenario") as FilterValue) || "all");
  const fixtureRows = recordedRuns.length > 0 ? recordedRuns : runs.filter((run) => run.run_id.startsWith("demo-"));
  const observedRows = runs.filter((run) => !run.run_id.startsWith("demo-") && !recordedRuns.some((recorded) => recorded.run_id === run.run_id));
  const filtersActive = statusFilter !== "all" || decisionFilter !== "all" || scenarioFilter !== "all";
  const fixtureOnly = queryFilters.get("fixtures") === "1";
  const filteredFixtures = useMemo(() => fixtureRows.filter((run) => matchesFilter(run, statusFilter) && matchesFilter(run, decisionFilter) && (scenarioFilter === "all" || scenarioKey(run) === scenarioFilter)), [fixtureRows, statusFilter, decisionFilter, scenarioFilter]);
  const registry = filtersActive || fixtureOnly ? filteredFixtures : [...fixtureRows, ...observedRows];

  return <div className="space-y-5">
    <section className="release-hero surface"><div className="flex flex-wrap items-start justify-between gap-5"><div><div className="eyebrow">Operational observability</div><h1 className="mt-2 text-2xl font-semibold tracking-tight text-main">Runs & traces</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted">Start with a recorded run, then open its bounded investigation detail. This registry is an evidence snapshot, not live telemetry.</p></div><Badge tone="info"><span className="inline-flex items-center gap-1.5"><Activity size={13} aria-hidden="true" />{registry.length} available runs</span></Badge></div></section>

    <Card as="section" className="p-5"><SectionHeader eyebrow="Recorded evidence" title="Run registry" description="Deterministic scenarios and observed projections share one investigation workflow." action={<Badge tone="neutral">Evidence snapshot</Badge>} />
      {loading ? <EmptyState title="Loading evidence snapshot..." description="Collecting bounded run projections for the operator registry." icon={Clock3} /> : error ? <EmptyState title="Investigation evidence unavailable" description={error} icon={Database} action={onOpenPlayground && <button type="button" className="operator-action" onClick={onOpenPlayground}><FlaskConical size={13} aria-hidden="true" />Open Agent Playground</button>} /> : registry.length === 0 ? <EmptyState title="No recorded investigations available" description="Choose a scenario or submit a controlled request to create an investigation projection." icon={GitBranch} action={onOpenPlayground && <button type="button" className="operator-action" onClick={onOpenPlayground}><FlaskConical size={13} aria-hidden="true" />Submit a scenario</button>} /> : <>
        <div className="trace-filter-bar mt-4"><FilterSelect label="Status" value={statusFilter} onChange={setStatusFilter} options={[["all", "All statuses"], ["completed", "Completed"], ["waiting_confirmation", "Awaiting confirmation"], ["blocked", "Prevented"]]} /><FilterSelect label="Decision" value={decisionFilter} onChange={setDecisionFilter} options={[["all", "All decisions"], ["require_confirmation", "Require confirmation"], ["deny", "Prevented"], ["clarification_required", "Clarification required"]]} /><FilterSelect label="Scenario" value={scenarioFilter} onChange={setScenarioFilter} options={[["all", "All scenarios"], ["refund-request", "Refund request"], ["prompt-injection", "Prompt injection"], ["duplicate-operation", "Duplicate operation"], ["missing-information", "Missing information"]]} /><span className="trace-filter-note">Filters apply to deterministic evidence snapshots.</span></div><div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted"><Badge tone="neutral">Evidence snapshot</Badge><span>Deterministic scenario evidence</span><span className="text-border">·</span><Badge tone="info">Operator projection</Badge><span>Returned by the operator API</span></div>
        <div className="trace-registry-table mt-4 hidden overflow-x-auto rounded-lg border border-border md:block"><table className="min-w-[1280px] w-full text-left"><thead className="bg-void/45 text-xs uppercase tracking-[.12em] text-muted"><tr><th className="px-4 py-3 font-medium">Run ID</th><th className="px-4 py-3 font-medium">Scenario</th><th className="px-4 py-3 font-medium">Intent</th><th className="px-4 py-3 font-medium">Status</th><th className="px-4 py-3 font-medium">Decision</th><th className="px-4 py-3 font-medium">Execution</th><th className="px-4 py-3 font-medium">Authority</th><th className="px-4 py-3 font-medium">Duration</th><th className="px-4 py-3 font-medium">Timestamp</th><th className="px-4 py-3 font-medium">Trace</th></tr></thead><tbody className="divide-y divide-border/70">{registry.map((run) => <RegistryRow key={run.run_id} run={run} onSelect={onSelect} />)}</tbody></table></div>
        <div className="mt-4 space-y-3 md:hidden">{registry.map((run) => <RegistryCard key={run.run_id} run={run} onSelect={onSelect} />)}</div>
      </>}
    </Card>

    <Card as="section" className="p-5"><SectionHeader eyebrow="Playground history" title="Recent Playground Runs" description="Runs submitted in this browser session. Selecting one opens the same read-only investigation surface." />{playgroundRuns.length === 0 ? <EmptyState title="No playground runs" description="Choose a business or safety scenario to create a real run projection." icon={FlaskConical} action={onOpenPlayground && <button type="button" className="operator-action" onClick={onOpenPlayground}><FlaskConical size={13} aria-hidden="true" />Open Agent Playground</button>} /> : <div className="space-y-2">{playgroundRuns.map((item) => <button type="button" className="trace-run-card w-full text-left" key={item.run.run_id} onClick={() => onSelect(item.run)}><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><span className="font-mono text-xs text-main">{item.run.run_id.slice(0, 16)}</span><Badge tone="info">{item.scenario}</Badge></div><RunStatus run={item.run} /></div><div className="mt-3 grid gap-3 sm:grid-cols-4"><div><div className="field-label">Scenario</div><div className="mt-1 truncate text-xs text-main">{item.scenario}</div></div><div><div className="field-label">Decision</div><div className="mt-1 truncate text-xs capitalize text-main">{policyOutcome(item.run)}</div></div><div><div className="field-label">Execution</div><div className="mt-1 truncate text-xs capitalize text-muted">{executionOutcome(item.run)}</div></div><div><div className="field-label">Trace</div><div className="mt-1"><TraceAvailability run={item.run} /></div></div></div></button>)}</div>}</Card>
  </div>;
}
