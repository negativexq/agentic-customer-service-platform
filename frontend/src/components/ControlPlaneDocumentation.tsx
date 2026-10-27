import { ArrowRight, BookOpen, GitBranch, LockKeyhole, ShieldCheck } from "lucide-react";
import { Card, SectionHeader } from "./ui";

const responsibilities = [
  { title: "Context", description: "Provides information only. Memory and RAG enrich understanding but cannot authorize actions.", icon: BookOpen, tone: "info" },
  { title: "Proposal", description: "The model produces semantic suggestions. Output remains untrusted.", icon: GitBranch, tone: "proposal" },
  { title: "Decision", description: "Deterministic layers validate provenance, policy, risk, and admissibility.", icon: ShieldCheck, tone: "decision" },
  { title: "Authority", description: "Only controlled runtime paths can mutate state.", icon: LockKeyhole, tone: "success" },
] as const;

const scenarios = [
  { title: "Valid refund request", flow: "Request → Context → Proposal → Decision → Evidence", expected: "Refund proposal is validated and confirmation requirements are enforced before execution.", tone: "success" },
  { title: "Prompt injection attempt", flow: "Untrusted input → Deterministic checks → Bounded outcome", expected: "Untrusted instructions remain contained. The model cannot bypass policy or gain execution authority.", tone: "danger" },
  { title: "Unauthorized mutation attempt", flow: "Request → Target validation → Authority boundary", expected: "Target validation and authorization boundaries prevent execution.", tone: "warning" },
] as const;

function toneClass(tone: (typeof responsibilities)[number]["tone"] | (typeof scenarios)[number]["tone"]) {
  return `control-plane-doc-${tone}`;
}

export function ControlPlaneDocumentation() {
  return <div className="grid gap-5 lg:grid-cols-2">
    <Card as="section" className="p-5"><SectionHeader eyebrow="Trust boundary" title="Why LLM is not the execution authority" description="The platform separates observable responsibilities without exposing hidden reasoning." /><div className="control-plane-responsibilities">{responsibilities.map(({ title, description, icon: Icon, tone }) => <div className={`control-plane-responsibility ${toneClass(tone)}`} key={title}><Icon size={16} aria-hidden="true" /><div><h3>{title}</h3><p>{description}</p></div></div>)}</div></Card>
    <Card as="section" className="p-5"><SectionHeader eyebrow="Demonstration scenarios" title="Trace the boundary through a request" description="These walkthroughs describe expected observable behavior; they do not fabricate a run result." /><div className="control-plane-scenarios">{scenarios.map(({ title, flow, expected, tone }) => <article className={`control-plane-scenario ${toneClass(tone)}`} key={title}><div className="flex items-start justify-between gap-3"><h3>{title}</h3><ArrowRight size={15} aria-hidden="true" /></div><div className="mt-2 font-mono text-xs leading-5 text-muted">{flow}</div><p className="mt-2 text-xs leading-5 text-muted"><strong>Expected:</strong> {expected}</p></article>)}</div></Card>
  </div>;
}
