import { Activity, BookOpen, Brain, ChevronDown, ChevronRight, ExternalLink, PenLine, Wrench } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Button } from "@/components/primitives/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { Skeleton } from "@/components/primitives/skeleton";
import type { AgentRun } from "../../types";
import { formatDuration, humanize, parseTimestamp, runLatencyMs, runStatus } from "../runs/runPresentation";
import { StatusLabel } from "../runs/StatusLabel";
import { RunContextPanel } from "./RunContextPanel";

type Props = {
  open: boolean;
  conversationId: string | null;
  customerName: string;
  runs: AgentRun[] | undefined;
  loading: boolean;
  onClose: () => void;
  onOpenRun: (runId: string) => void;
  onOpenMonitoring: (conversationId: string) => void;
};

/** Debug view for a whole conversation: every turn the agent ran, and what knowledge and memory it used. */
export function ConversationInspectorSheet({ open, conversationId, customerName, runs, loading, onClose, onOpenRun, onOpenMonitoring }: Props) {
  return (
    <Sheet open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <SheetContent side="right" className="w-full gap-0 p-0 data-[side=right]:sm:max-w-3xl" data-testid="conversation-inspector">
        <SheetHeader className="border-b border-border px-5 py-4 pr-12">
          <SheetTitle>Conversation debug · {customerName}</SheetTitle>
          <SheetDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono text-xs">{conversationId}</span>
            {conversationId && (
              <Button variant="link" size="xs" className="h-auto p-0" onClick={() => onOpenMonitoring(conversationId)}>
                Open in Monitoring<ExternalLink aria-hidden="true" />
              </Button>
            )}
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading && !runs ? (
            <div className="grid gap-3 p-5"><Skeleton className="h-16" /><Skeleton className="h-48" /></div>
          ) : (
            <ConversationDebugView runs={runs ?? []} onOpenRun={onOpenRun} />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="grid gap-0.5 px-4 py-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-base font-medium tabular-nums text-foreground">{value}</span>
      {detail && <span className="truncate text-[11px] text-muted-foreground">{detail}</span>}
    </div>
  );
}

function Block({ icon, title, count, children }: { icon: ReactNode; title: string; count?: number; children: ReactNode }) {
  return (
    <section className="grid gap-2">
      <h3 className="flex items-center gap-2 text-sm font-medium text-foreground">
        <span className="text-muted-foreground" aria-hidden="true">{icon}</span>
        {title}
        {count !== undefined && <span className="text-xs font-normal tabular-nums text-muted-foreground">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

function Chip({ children }: { children: ReactNode }) {
  return <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{children}</span>;
}

function tokensOf(run: AgentRun): { input: number; output: number } {
  return { input: run.provider_metadata?.input_tokens ?? 0, output: run.provider_metadata?.output_tokens ?? 0 };
}

export function ConversationDebugView({ runs: unsorted, onOpenRun }: { runs: AgentRun[]; onOpenRun: (runId: string) => void }) {
  const runs = useMemo(() => [...unsorted].sort((a, b) => a.started_at.localeCompare(b.started_at)), [unsorted]);
  const [expanded, setExpanded] = useState<string | null>(null);

  const summary = useMemo(() => {
    const input = runs.reduce((sum, run) => sum + tokensOf(run).input, 0);
    const output = runs.reduce((sum, run) => sum + tokensOf(run).output, 0);
    const latency = runs.reduce((sum, run) => sum + runLatencyMs(run), 0);
    const knowledge = new Map<string, { title: string; section: string; turns: number[]; best: number }>();
    const writes: { turn: number; key: string; type: string; runId: string }[] = [];
    const reads = new Map<string, { type: string; turns: number[] }>();
    runs.forEach((run, index) => {
      const turn = index + 1;
      for (const doc of run.rag_documents) {
        const key = doc.chunk_id ?? doc.citation_id;
        const entry = knowledge.get(key) ?? { title: doc.title, section: doc.section, turns: [], best: 0 };
        if (!entry.turns.includes(turn)) entry.turns.push(turn);
        entry.best = Math.max(entry.best, doc.score);
        knowledge.set(key, entry);
      }
      for (const item of run.memory.read_items ?? []) {
        const entry = reads.get(item.key) ?? { type: item.memory_type, turns: [] };
        entry.turns.push(turn);
        reads.set(item.key, entry);
      }
      const written = run.memory.written_item;
      if (written) writes.push({ turn, key: written.key, type: written.memory_type, runId: run.run_id });
    });
    return {
      input, output,
      avgLatency: runs.length ? latency / runs.length : null,
      failed: runs.filter((run) => runStatus(run).tone === "danger").length,
      escalated: runs.filter((run) => run.intent === "human_escalation").length,
      knowledge: [...knowledge].sort((a, b) => b[1].turns.length - a[1].turns.length || b[1].best - a[1].best),
      reads: [...reads],
      writes,
    };
  }, [runs]);

  if (runs.length === 0) {
    return <p className="m-5 rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">The AI assistant has not run in this conversation yet.</p>;
  }

  return (
    <div className="grid gap-6 p-5">
      <div className="grid grid-cols-2 divide-x divide-y divide-border overflow-hidden rounded-lg border border-border sm:grid-cols-4 sm:divide-y-0">
        <Stat label="AI turns" value={String(runs.length)} detail={`${summary.escalated} escalated · ${summary.failed} failed`} />
        <Stat label="Avg latency" value={formatDuration(summary.avgLatency)} />
        <Stat label="Tokens" value={(summary.input + summary.output).toLocaleString()} detail={`${summary.input.toLocaleString()} in · ${summary.output.toLocaleString()} out`} />
        <Stat label="Context" value={`${summary.knowledge.length} chunks`} detail={`${summary.reads.length} memories read · ${summary.writes.length} written`} />
      </div>

      <Block icon={<Activity className="size-4" />} title="Turns" count={runs.length}>
        <ol className="grid gap-1.5" aria-label="Conversation turns">
          {runs.map((run, index) => {
            const status = runStatus(run);
            const open = expanded === run.run_id;
            const tokens = tokensOf(run);
            return (
              <li key={run.run_id} className="rounded-lg border border-border bg-background">
                <button type="button" onClick={() => setExpanded(open ? null : run.run_id)} aria-expanded={open}
                  className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-accent/40">
                  {open ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
                  <span className="w-6 shrink-0 text-xs tabular-nums text-muted-foreground">#{index + 1}</span>
                  <span className="grid min-w-0 flex-1 gap-1">
                    <span className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium text-foreground">{humanize(run.intent)}</span>
                      <StatusLabel {...status} />
                    </span>
                    <span className="flex flex-wrap items-center gap-1.5">
                      {run.rag_documents.length > 0 && <Chip><BookOpen className="size-3" aria-hidden="true" />{run.rag_documents.length} knowledge</Chip>}
                      {(run.memory.read_items?.length ?? 0) > 0 && <Chip><Brain className="size-3" aria-hidden="true" />{run.memory.read_items!.length} memory read</Chip>}
                      {run.memory.written_item && <Chip><PenLine className="size-3" aria-hidden="true" />memory written</Chip>}
                      {run.tools.length > 0 && <Chip><Wrench className="size-3" aria-hidden="true" />{run.tools.map((tool) => humanize(tool.name)).join(", ")}</Chip>}
                    </span>
                  </span>
                  <span className="hidden shrink-0 text-right text-xs tabular-nums text-muted-foreground sm:grid">
                    <span>{parseTimestamp(run.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                    <span>{formatDuration(runLatencyMs(run))} · {tokens.input + tokens.output} tok</span>
                  </span>
                </button>
                {open && (
                  <div className="grid gap-4 border-t border-border px-4 py-4">
                    <RunContextPanel run={run} />
                    <Button variant="outline" size="sm" className="justify-self-start" onClick={() => onOpenRun(run.run_id)} aria-label={`Inspect run ${run.run_id}`}>
                      Trace, policy and tools<ExternalLink aria-hidden="true" />
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </Block>

      <Block icon={<BookOpen className="size-4" />} title="Knowledge used across the conversation" count={summary.knowledge.length}>
        {summary.knowledge.length === 0 ? <p className="text-xs text-muted-foreground">No knowledge base chunks were retrieved.</p> : (
          <ul className="grid divide-y divide-border rounded-lg border border-border">
            {summary.knowledge.map(([key, doc]) => (
              <li key={key} className="flex items-baseline justify-between gap-3 px-3 py-2 text-sm">
                <span className="min-w-0 truncate text-foreground">{doc.title}<span className="text-muted-foreground"> · {doc.section}</span></span>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">turn {doc.turns.join(", ")} · best {doc.best.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        )}
      </Block>

      <Block icon={<Brain className="size-4" />} title="Memory activity" count={summary.reads.length + summary.writes.length}>
        {summary.reads.length === 0 && summary.writes.length === 0 ? <p className="text-xs text-muted-foreground">No customer memory was read or stored.</p> : (
          <ul className="grid divide-y divide-border rounded-lg border border-border">
            {summary.reads.map(([key, entry]) => (
              <li key={`read-${key}`} className="flex items-baseline justify-between gap-3 px-3 py-2 text-sm">
                <span className="min-w-0 truncate"><span className="text-muted-foreground">Read </span><span className="font-mono text-xs text-foreground">{key}</span><span className="text-muted-foreground"> · {humanize(entry.type)}</span></span>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">turn {entry.turns.join(", ")}</span>
              </li>
            ))}
            {summary.writes.map((entry) => (
              <li key={`write-${entry.runId}`} className="flex items-baseline justify-between gap-3 px-3 py-2 text-sm">
                <span className="min-w-0 truncate"><span className="text-muted-foreground">Stored </span><span className="font-mono text-xs text-foreground">{entry.key}</span><span className="text-muted-foreground"> · {humanize(entry.type)}</span></span>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">turn {entry.turn}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-[11px] text-muted-foreground">Open a turn to see the stored text. Deleted memories are not shown.</p>
      </Block>
    </div>
  );
}
