import { BookOpen, Compass, MessageCircleQuestion, UserRound, Workflow, Zap, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/primitives/badge";
import { Button } from "@/components/primitives/button";
import { Skeleton } from "@/components/primitives/skeleton";
import { PageHeader } from "../../components/layout/PageHeader";
import type { GuidanceSeed } from "../studio/GuidancePage";
import { formatRelative, humanize } from "../runs/runPresentation";
import { coachingSeed } from "./coaching";
import { ConversationFlow } from "./ConversationFlow";
import { useGaps, usePerformance } from "./queries";
import { EmptyNote, percent, rating, Section, WindowPicker } from "./shared";
import type { Gap, GapFix, GapKind } from "./types";

const kinds: Record<GapKind, { label: string; rail: string; pitch: string; icon: LucideIcon }> = {
  content: { label: "Missing content", rail: "Content gaps", pitch: "Add knowledge so the assistant can answer these questions itself.", icon: BookOpen },
  clarification: { label: "Needed clarification", rail: "Clarification gaps", pitch: "Tell the assistant what to ask for, or what it may assume.", icon: MessageCircleQuestion },
  action: { label: "Couldn't act", rail: "Action gaps", pitch: "Requests the assistant understood but had no way to carry out.", icon: Zap },
  handoff: { label: "Handed to a person", rail: "Handoff reasons", pitch: "Why conversations reached your team instead of being resolved.", icon: UserRound },
};
const kindOrder: GapKind[] = ["content", "clarification", "action", "handoff"];

const fixes: Record<GapFix, { label: string; icon: LucideIcon }> = {
  knowledge: { label: "Add knowledge", icon: BookOpen },
  guidance: { label: "Write guidance", icon: Compass },
  actions: { label: "Review actions", icon: Zap },
  procedures: { label: "Review procedure", icon: Workflow },
};

export type GapHandlers = {
  onFix: (gap: Gap) => void;
  onCoach: (seed: GuidanceSeed) => void;
  onOpenConversation: (pk: number) => void;
  onOpenRun: (runId: string) => void;
};

export function GapCard({ gap, onFix, onCoach, onOpenConversation, onOpenRun }: { gap: Gap } & GapHandlers) {
  const kind = kinds[gap.kind];
  const fix = fixes[gap.fix];
  const Icon = kind.icon;
  const FixIcon = fix.icon;
  return (
    <article className="min-w-0 rounded-xl border border-border bg-card">
      <header className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4">
        <div className="flex min-w-0 gap-3">
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"><Icon className="size-3.5" aria-hidden /></span>
          <div className="min-w-0">
            <h3 className="text-sm font-medium text-foreground">{gap.title}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">{gap.description}</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge variant="outline">{kind.label}</Badge>
          <span className="font-mono text-sm tabular-nums text-foreground" aria-label={`${gap.count} occurrences`}>{gap.count}×</span>
        </div>
      </header>
      {gap.examples.length > 0 && (
        <ul className="mx-4 mt-3 grid divide-y divide-border rounded-lg border border-border" aria-label="Examples">
          {gap.examples.map((example, index) => (
            <li key={`${example.run_id ?? index}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <p className="min-w-0 flex-1 truncate text-sm text-foreground" title={example.question}>“{example.question}”</p>
              <div className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                <span>{formatRelative(example.at)}</span>
                {example.conversation_pk !== null && <Button size="xs" variant="ghost" onClick={() => onOpenConversation(example.conversation_pk!)}>Conversation</Button>}
                {example.run_id && <Button size="xs" variant="ghost" onClick={() => onOpenRun(example.run_id!)}>Run</Button>}
              </div>
            </li>
          ))}
        </ul>
      )}
      <footer className="flex flex-wrap items-center gap-2 px-4 py-3">
        <Button size="sm" onClick={() => onFix(gap)}><FixIcon data-icon="inline-start" aria-hidden />{fix.label}</Button>
        {gap.fix !== "guidance" && (
          <Button size="sm" variant="outline" onClick={() => onCoach(coachingSeed({
            title: gap.intent ? `${humanize(gap.intent)}: ${gap.title}` : gap.title,
            context: gap.examples[0]?.question ? `a customer asks “${gap.examples[0].question}”` : gap.description,
            intent: gap.intent,
            runId: gap.examples[0]?.run_id,
          }))}><Compass data-icon="inline-start" aria-hidden />Coach the assistant</Button>
        )}
      </footer>
    </article>
  );
}

export function GapRail({ gaps, active, onSelect }: { gaps: Gap[]; active: GapKind; onSelect: (kind: GapKind) => void }) {
  return (
    <div role="tablist" aria-label="Gap type" aria-orientation="vertical" className="grid content-start gap-2">
      {kindOrder.map((kind) => {
        const items = gaps.filter((gap) => gap.kind === kind);
        const total = items.reduce((sum, gap) => sum + gap.count, 0);
        const selected = active === kind;
        const Icon = kinds[kind].icon;
        return (
          <button key={kind} type="button" role="tab" aria-selected={selected} onClick={() => onSelect(kind)}
            className={`grid gap-3 rounded-xl border p-4 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${selected ? "border-primary/40 bg-primary/10" : "border-border bg-card hover:bg-muted/40"}`}>
            <span className="flex items-center gap-2 text-xs text-muted-foreground"><Icon className="size-3.5" aria-hidden />{kinds[kind].rail}</span>
            {selected && <span className="text-sm font-medium text-foreground">{kinds[kind].pitch}</span>}
            <span className="flex items-end justify-between gap-3">
              <span className="grid">
                <span className="font-mono text-2xl font-medium tabular-nums text-foreground">{total}</span>
                <span className="text-xs text-muted-foreground">{kind === "handoff" ? "conversations handed off" : "times it happened"}</span>
              </span>
              <span className="text-right text-xs text-muted-foreground"><span className="font-mono tabular-nums text-foreground">{items.length}</span> to review</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function OptimizePage(handlers: GapHandlers) {
  const [days, setDays] = useState(30);
  const [chosen, setChosen] = useState<GapKind | null>(null);
  const query = useGaps(days);
  const performance = usePerformance(days);
  const all = query.data ?? [];
  const kind = chosen ?? kindOrder.find((value) => all.some((gap) => gap.kind === value)) ?? "content";
  const gaps = all.filter((gap) => gap.kind === kind);
  const summary = performance.data?.summary;

  return (
    <div className="min-w-0">
      <PageHeader title="Optimize" description="Where the assistant fell short, ranked by how often it happened. Each gap links to the place that fixes it."
        actions={<>
          {summary && (
            <dl className="flex items-center gap-1.5 text-xs">
              <div className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1"><dt className="text-muted-foreground">Resolution rate</dt><dd className="font-mono tabular-nums text-success">{percent(summary.ai_resolution_rate)}</dd></div>
              <div className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1"><dt className="text-muted-foreground">CSAT</dt><dd className="font-mono tabular-nums text-foreground">{rating(summary.csat_average)}</dd></div>
            </dl>
          )}
          <WindowPicker value={days} onChange={setDays} />
        </>} />
      <div className="grid gap-4 p-4 sm:p-6">
        {query.isError && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">Gaps are unavailable. Check that the operator API is running.</p>}
        <Section title="Where conversations end" description="By the kind of request the assistant recognised first.">
          {!performance.data ? <Skeleton className="m-4 h-56" />
            : performance.data.flows.length === 0 ? <EmptyNote>No conversations in the last {days} days.</EmptyNote>
            : <div className="px-2"><ConversationFlow flows={performance.data.flows} /></div>}
        </Section>
        <div className="grid gap-4 lg:grid-cols-[19rem_minmax(0,1fr)]">
          {query.isPending ? <Skeleton className="h-80 w-full rounded-xl" /> : <GapRail gaps={all} active={kind} onSelect={setChosen} />}
          <div role="tabpanel" aria-label={kinds[kind].rail} className="grid content-start gap-3">
            {query.isPending ? [0, 1].map((index) => <Skeleton key={index} className="h-36 w-full rounded-xl" />)
              : gaps.length === 0 ? <div className="rounded-xl border border-dashed border-border"><EmptyNote>No {kinds[kind].rail.toLowerCase()} in the last {days} days.</EmptyNote></div>
              : gaps.map((gap) => <GapCard key={gap.key} gap={gap} {...handlers} />)}
          </div>
        </div>
      </div>
    </div>
  );
}
