import { Activity, ArrowRight, Beaker, LockKeyhole, ShieldCheck } from "lucide-react";
import type { AgentRun } from "../types";
import { Badge, Card, MetricCard, SectionHeader } from "./ui";

type Props = {
  runs: AgentRun[];
  busy: boolean;
  onRunSafetyDemo: () => Promise<void>;
  onNavigate: (view: "playground" | "traces" | "evidence") => void;
};

export function OverviewDashboard({ runs, busy, onRunSafetyDemo, onNavigate }: Props) {
  const runtimeRuns = runs.filter((run) => !run.run_id.startsWith("demo-"));
  return <div className="space-y-6">
    <section className="overview-hero surface product-hero">
      <div className="max-w-3xl"><div className="eyebrow">Agent Platform / Customer operations</div>
        <h1>Every action needs<br className="hidden sm:block" /> a reason. And authority.</h1>
        <p className="mt-5 max-w-xl text-base leading-7 text-muted">Follow a refund request from conversation to decision. See what the agent proposed, what policy allowed, and what actually happened.</p>
        <div className="mt-7 flex flex-wrap gap-3"><button type="button" className="button-primary" onClick={() => { void onRunSafetyDemo(); }} disabled={busy}><Beaker size={16} aria-hidden="true" />{busy ? "Loading recorded demo…" : "Explore the refund workflow"}<ArrowRight size={15} aria-hidden="true" /></button><button type="button" className="operator-action" onClick={() => onNavigate("traces")}><Activity size={15} aria-hidden="true" />Investigate runs</button></div>
        <p className="mt-3 text-xs text-muted">Recorded demo · no runtime request</p>
      </div>
      <div className="product-boundary"><div className="eyebrow">LLM proposes, deterministic systems decide</div><ol>{[["01", "Customer request", "A damaged order. A refund request."], ["02", "Model proposal", "Intent and arguments, without authority."], ["03", "Validation + policy", "Scope, eligibility, and confirmation."], ["04", "Controlled outcome", "A recorded decision with evidence."]].map(([number, title, detail]) => <li key={number}><span>{number}</span><div><strong>{title}</strong><p>{detail}</p></div></li>)}</ol></div>
    </section>
    <section className="grid gap-4 md:grid-cols-3" aria-label="System guarantees">{[
      ["Policy before action", "Customer scope and target validation bound each proposed operation."],
      ["Confirmation before mutation", "Covered writes wait for explicit confirmation before execution."],
      ["One business effect", "Persistence-backed idempotency protects against duplicate requests."],
    ].map(([title, detail]) => <Card as="section" className="p-5" key={title}><ShieldCheck size={20} className="text-success mb-4" aria-hidden="true" /><h2 className="section-title">{title}</h2><p className="mt-2 text-sm leading-6 text-muted">{detail}</p></Card>)}</section>
    <Card as="section" className="p-5 sm:p-6"><SectionHeader eyebrow="Runtime / Operator API" title="Operational runs" description="Current loaded runtime projections. Recorded demo fixtures are excluded." action={<button type="button" className="operator-action" onClick={() => onNavigate("traces")}>View runs<ArrowRight size={14} aria-hidden="true" /></button>} /><div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3"><MetricCard label="Loaded runs" value={runtimeRuns.length ? String(runtimeRuns.length) : "Not available"} detail="Current projection, not all-time telemetry" icon={Activity} /><MetricCard label="Completed" value={runtimeRuns.length ? String(runtimeRuns.filter((run) => run.status === "completed").length) : "Not available"} detail="Recorded runtime status" icon={ShieldCheck} /><MetricCard label="Awaiting confirmation" value={runtimeRuns.length ? String(runtimeRuns.filter((run) => run.status === "waiting_confirmation").length) : "Not available"} detail="Not yet authorized to execute" icon={LockKeyhole} /></div></Card>
    <Card as="section" className="recorded-evidence p-5 sm:p-6"><SectionHeader eyebrow="Recorded evaluation / Reference release" title="Published safety evidence" description="Historical evaluation artifacts. These results are separate from live runtime data." action={<Badge tone="neutral">Recorded evidence</Badge>} /><div className="grid gap-5 sm:grid-cols-3"><div><div className="field-label">D2c semantic validation</div><strong className="mt-2 block text-lg text-main">540/540 · CLOSED</strong></div><div><div className="field-label">D2d operational gate</div><strong className="mt-2 block text-sm text-success">RELEASE_GATE_PASS</strong></div><div><div className="field-label">Unsafe executions in recorded gates</div><strong className="mt-2 block text-lg text-main">0</strong></div></div><button type="button" className="back-link mt-5" onClick={() => onNavigate("evidence")}>Review evidence and scope<ArrowRight size={14} aria-hidden="true" /></button></Card>
  </div>;
}
