import { BookOpen, Compass, MessageCircleQuestion, UserRound, Workflow, Zap, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/primitives/badge";
import { Button } from "@/components/primitives/button";
import { Skeleton } from "@/components/primitives/skeleton";
import { PageHeader } from "../../components/layout/PageHeader";
import type { GuidanceSeed } from "../studio/GuidancePage";
import { formatRelative, humanize } from "../runs/runPresentation";
import { coachingSeed } from "./coaching";
import { useGaps } from "./queries";
import { EmptyNote, WindowPicker } from "./shared";
import type { Gap, GapFix, GapKind } from "./types";

const kinds: Record<GapKind, { label: string; icon: LucideIcon }> = {
  content: { label: "Missing content", icon: BookOpen },
  clarification: { label: "Needed clarification", icon: MessageCircleQuestion },
  action: { label: "Couldn't act", icon: Zap },
  handoff: { label: "Handed to a person", icon: UserRound },
};

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

export function OptimizePage(handlers: GapHandlers) {
  const [days, setDays] = useState(30);
  const [kind, setKind] = useState<GapKind | "all">("all");
  const query = useGaps(days);
  const gaps = (query.data ?? []).filter((gap) => kind === "all" || gap.kind === kind);
  const counts = (query.data ?? []).reduce<Record<string, number>>((total, gap) => ({ ...total, [gap.kind]: (total[gap.kind] ?? 0) + gap.count }), {});

  return (
    <div className="min-w-0">
      <PageHeader title="Optimize" description="Where the assistant fell short, grouped and ranked by how often it happened. Each gap links to the place that fixes it."
        actions={<WindowPicker value={days} onChange={setDays} />}>
        <div role="radiogroup" aria-label="Gap type" className="flex flex-wrap gap-1.5">
          {(["all", ...Object.keys(kinds)] as (GapKind | "all")[]).map((value) => (
            <button key={value} type="button" role="radio" aria-checked={kind === value} onClick={() => setKind(value)}
              className={`h-7 rounded-md border px-2.5 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${kind === value ? "border-foreground/20 bg-muted text-foreground" : "border-border text-muted-foreground hover:text-foreground"}`}>
              {value === "all" ? "All gaps" : kinds[value].label}
              {value !== "all" && counts[value] ? <span className="ml-1.5 font-mono tabular-nums">{counts[value]}</span> : null}
            </button>
          ))}
        </div>
      </PageHeader>
      <div className="grid gap-3 p-4 sm:p-6">
        {query.isError && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">Gaps are unavailable. Check that the operator API is running.</p>}
        {query.isPending ? [0, 1, 2].map((index) => <Skeleton key={index} className="h-36 w-full rounded-xl" />)
          : gaps.length === 0 ? <div className="rounded-xl border border-dashed border-border"><EmptyNote>No gaps of this kind in the last {days} days.</EmptyNote></div>
          : gaps.map((gap) => <GapCard key={gap.key} gap={gap} {...handlers} />)}
      </div>
    </div>
  );
}
