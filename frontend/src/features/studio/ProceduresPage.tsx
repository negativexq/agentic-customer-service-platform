import { BookOpen, CheckCircle2, ClipboardList, Hand, MessageSquare, Search, ShieldCheck, Sparkles, UserRound, Wrench, type LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";
import { EntityChip, entityColor } from "../../components/EntityChip";
import { useMemo, useState } from "react";
import { Input } from "@/components/primitives/input";
import { Skeleton } from "@/components/primitives/skeleton";
import { PageHeader } from "../../components/layout/PageHeader";
import { humanize } from "../runs/runPresentation";
import { ProcedureSimulations } from "../improve/ProcedureSimulations";
import { useKnowledge, useProcedures } from "./queries";
import type { ProcedureStepKind, ProcedureView } from "./types";

const stepIcons: Record<ProcedureStepKind, LucideIcon> = {
  understand: Sparkles, collect: ClipboardList, lookup: Search, knowledge: BookOpen, policy: ShieldCheck,
  confirm: CheckCircle2, act: Wrench, respond: MessageSquare, handoff: UserRound,
};

const requestTypeLabels: Record<string, string> = {
  read_action: "Look-up", write_action: "Change", knowledge_only: "Policy question", escalation: "Hand-off", memory_action: "Memory", informational: "About the assistant",
};

function rate(part: number, total: number): string {
  return total === 0 ? "—" : `${Math.round((part / total) * 100)}%`;
}

export function ProcedureDetail({ procedure, articleTitles = {}, onOpenAction, onOpenGuidance, onOpenKnowledge }: { procedure: ProcedureView; articleTitles?: Record<string, string>; onOpenAction: (name: string) => void; onOpenGuidance: () => void; onOpenKnowledge: (id: string) => void }) {
  const titleOf = (id: string) => articleTitles[id] ?? humanize(id);
  return (
    <article className="grid content-start gap-6" aria-label={`${procedure.name} procedure`}>
      <header className="grid gap-1">
        <h2 className="text-base font-semibold text-foreground">{procedure.name}</h2>
        <p className="text-sm text-muted-foreground">{procedure.summary}</p>
      </header>

      <dl className="grid grid-cols-2 divide-x divide-y divide-border overflow-hidden rounded-lg border border-border sm:grid-cols-4 sm:divide-y-0">
        {([
          ["Requests · 30 d", String(procedure.usage_runs)],
          ["Asked a follow-up", rate(procedure.clarification_runs, procedure.usage_runs)],
          ["Handed to a person", rate(procedure.handoff_runs, procedure.usage_runs)],
          ["Failed", rate(procedure.failed_runs, procedure.usage_runs)],
        ] as const).map(([label, value]) => (
          <div key={label} className="grid gap-0.5 px-4 py-3"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="text-base font-medium tabular-nums text-foreground">{value}</dd></div>
        ))}
      </dl>

      <section className="grid gap-2">
        <h3 className="text-sm font-medium text-foreground">Steps</h3>
        <ol className="relative grid gap-0">
          {procedure.steps.map((step, index) => {
            const Icon = step.kind === "handoff" ? Hand : stepIcons[step.kind];
            const last = index === procedure.steps.length - 1;
            return (
              <li key={`${step.kind}-${index}`} className="relative grid grid-cols-[2rem_minmax(0,1fr)] gap-3 pb-4">
                {!last && <span className="absolute top-8 bottom-0 left-4 w-px bg-border" aria-hidden="true" />}
                {step.action ? (
                  // A step that calls an action wears the action colour, so tool steps stand out in the path.
                  <span style={{ "--entity": entityColor("action") } as CSSProperties} className="flex size-8 items-center justify-center rounded-full border border-[color-mix(in_oklab,var(--entity)_35%,var(--border))] bg-[color-mix(in_oklab,var(--entity)_10%,var(--card))] text-(--entity)"><Icon className="size-3.5" aria-hidden="true" /></span>
                ) : (
                  <span className="flex size-8 items-center justify-center rounded-full border border-border bg-card text-muted-foreground"><Icon className="size-3.5" aria-hidden="true" /></span>
                )}
                <div className="grid gap-0.5 pt-1">
                  <span className="text-sm font-medium text-foreground">{step.title}</span>
                  <span className="text-sm text-muted-foreground">{step.detail}</span>
                  {step.operator_note && <span className="text-xs text-warning">{step.operator_note}</span>}
                  {step.action && (
                    <span className="mt-1.5"><EntityChip kind="action" label={humanize(step.action)} code={step.action} onOpen={() => onOpenAction(step.action!)} /></span>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <div className="grid gap-4 sm:grid-cols-2">
        <section className="grid content-start gap-2">
          <h3 className="text-sm font-medium text-foreground">Knowledge</h3>
          {procedure.knowledge.length === 0 ? <p className="text-sm text-muted-foreground">Does not search the knowledge base.</p> : (
            <div className="flex flex-wrap gap-1.5">
              {procedure.knowledge.map((id) => <EntityChip key={id} kind="knowledge" label={titleOf(id)} onOpen={() => onOpenKnowledge(id)} />)}
            </div>
          )}
        </section>
        <section className="grid content-start gap-2">
          <h3 className="text-sm font-medium text-foreground">Guidance</h3>
          <p className="text-sm text-muted-foreground">
            {procedure.guidance_count === 0 ? "No rules target this request type." : `${procedure.guidance_count} rule${procedure.guidance_count === 1 ? "" : "s"} shape how it is interpreted.`}
          </p>
          <div><EntityChip kind="guidance" label={procedure.guidance_count === 0 ? "Add a rule" : "Edit guidance"} onOpen={onOpenGuidance} /></div>
        </section>
      </div>
    </article>
  );
}

export function ProceduresPage({ initialIntent, onOpenAction, onOpenGuidance, onOpenKnowledge }: { initialIntent: string | null; onOpenAction: (name: string) => void; onOpenGuidance: () => void; onOpenKnowledge: (id: string) => void }) {
  const procedures = useProcedures();
  const articles = useKnowledge();
  const articleTitles = useMemo(() => Object.fromEntries((articles.data ?? []).map((doc) => [doc.document_id, doc.title])), [articles.data]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(initialIntent);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (procedures.data ?? []).filter((item) => !needle || `${item.name} ${item.summary}`.toLowerCase().includes(needle));
  }, [procedures.data, query]);
  const current = procedures.data?.find((item) => item.intent === selected) ?? filtered[0] ?? null;

  return (
    <>
      <PageHeader title="Procedures" description="The step-by-step workflow the AI assistant follows for each kind of request: what it collects, which checks run, which actions it can take and when it hands off." />
      <div className="grid min-h-0 flex-1 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <aside className="grid content-start gap-2 border-b border-border py-3 pr-3 pl-4 sm:pl-6 lg:border-r lg:border-b-0">
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter procedures" aria-label="Filter procedures" className="h-8" />
          {procedures.isPending ? <div className="grid gap-1.5"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : (
            <nav aria-label="Procedures" className="grid gap-0.5">
              {filtered.map((item) => {
                const active = current?.intent === item.intent;
                return (
                  <button key={item.intent} type="button" onClick={() => setSelected(item.intent)} aria-current={active ? "true" : undefined}
                    className={`grid gap-0.5 rounded-lg px-3 py-2 text-left transition-colors ${active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"}`}>
                    <span className="flex items-center justify-between gap-2 text-sm font-medium">
                      <span className="truncate">{item.name}</span>
                      <span className="shrink-0 text-xs font-normal tabular-nums text-muted-foreground">{item.usage_runs || ""}</span>
                    </span>
                    <span className="text-xs text-muted-foreground">{requestTypeLabels[item.request_type] ?? humanize(item.request_type)}{item.actions.length > 0 ? ` · ${item.actions.length} action${item.actions.length === 1 ? "" : "s"}` : ""}</span>
                  </button>
                );
              })}
              {filtered.length === 0 && <p className="px-3 py-4 text-sm text-muted-foreground">No procedure matches “{query}”.</p>}
            </nav>
          )}
        </aside>
        <div className="min-w-0 p-4 sm:p-6">
          {current ? (
            <div className="grid gap-6">
              <ProcedureDetail key={current.intent} procedure={current} articleTitles={articleTitles} onOpenAction={onOpenAction} onOpenGuidance={onOpenGuidance} onOpenKnowledge={onOpenKnowledge} />
              <ProcedureSimulations key={`sim-${current.intent}`} intent={current.intent} />
            </div>
          )
            : procedures.isError ? <p className="text-sm text-muted-foreground">Procedures could not be loaded.</p> : null}
        </div>
      </div>
    </>
  );
}
