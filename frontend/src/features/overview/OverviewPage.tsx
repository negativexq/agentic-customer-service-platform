import { ArrowRight, Beaker, CircleCheck, MessagesSquare } from "lucide-react";
import { useMemo } from "react";
import { Button } from "@/components/primitives/button";
import { PageHeader } from "../../components/layout/PageHeader";
import { Stat, StatStrip } from "../../components/layout/StatStrip";
import type { AgentRun } from "../../types";
import { deriveTelemetry } from "../monitoring/telemetry";
import { DecisionPath } from "../runs/DecisionPath";
import { formatDuration, formatRelative, humanize, isRecorded, runStatus } from "../runs/runPresentation";
import { StatusLabel } from "../runs/StatusLabel";

type OverviewPageProps = {
  runs: AgentRun[];
  onOpenRun: (runId: string) => void;
  onOpenConversations: () => void;
  onOpenRuns: () => void;
  onExploreDemo: () => void;
};

const attentionLabels = new Set(["Awaiting confirmation", "Needs input", "Failed", "Failed validation"]);

function RunRow({ run, onOpen }: { run: AgentRun; onOpen: (runId: string) => void }) {
  const status = runStatus(run);
  return (
    <li>
      <a
        href={`/runs/${encodeURIComponent(run.run_id)}`}
        onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return; event.preventDefault(); onOpen(run.run_id); }}
        className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-2.5 transition-colors hover:bg-accent/60 focus-visible:bg-accent/60 focus-visible:outline-none sm:grid-cols-[minmax(0,1fr)_auto_auto]"
      >
        <span className="grid min-w-0 gap-0.5">
          <span className="truncate text-sm text-foreground">{humanize(run.intent)}</span>
          <span className="truncate font-mono text-xs text-muted-foreground">{run.conversation_id}</span>
        </span>
        <span className="hidden sm:inline-flex"><DecisionPath run={run} /></span>
        <span className="grid justify-items-end gap-0.5">
          <StatusLabel {...status} />
          <span className="text-xs text-muted-foreground">{formatRelative(run.started_at)}</span>
        </span>
      </a>
    </li>
  );
}

export function OverviewPage({ runs, onOpenRun, onOpenConversations, onOpenRuns, onExploreDemo }: OverviewPageProps) {
  const live = useMemo(() => runs.filter((run) => !isRecorded(run)).sort((a, b) => b.started_at.localeCompare(a.started_at)), [runs]);
  const telemetry = useMemo(() => deriveTelemetry(live), [live]);
  // One row per conversation, and only while its latest turn still waits on someone.
  const open = useMemo(() => {
    const latest = new Map<string, AgentRun>();
    for (const run of live) if (!latest.has(run.conversation_id)) latest.set(run.conversation_id, run);
    return [...latest.values()].filter((run) => attentionLabels.has(runStatus(run).label) || run.intent === "human_escalation");
  }, [live]);
  const attention = open.slice(0, 6);
  const count = (label: string) => live.filter((run) => runStatus(run).label === label).length;
  const value = (n: number) => (live.length ? String(n) : "—");

  return (
    <>
      <PageHeader
        title="Overview"
        description="What the agent decided recently, and which conversations need a person."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={onExploreDemo}><Beaker aria-hidden="true" />Explore the refund workflow</Button>
            <Button size="sm" onClick={onOpenConversations}><MessagesSquare aria-hidden="true" />Open conversations</Button>
          </>
        }
      />
      <div className="grid min-w-0 gap-4 p-4 sm:p-6">
        <StatStrip label="Live run summary">
          <Stat label="Live runs" value={value(live.length)} detail="Most recent 100" />
          <Stat label="Awaiting confirmation" value={value(count("Awaiting confirmation"))} detail="Held before any write" />
          <Stat label="Needs input" value={value(count("Needs input"))} detail="Asked the customer" />
          <Stat label="Prevented" value={value(count("Prevented"))} detail="Stopped by policy" />
          <Stat label="Escalations" value={value(telemetry.escalationCount)} detail="Routed to a person" />
          <Stat label="Latency p95" value={formatDuration(telemetry.latencyP95)} detail={`p50 ${formatDuration(telemetry.latencyP50)}`} />
        </StatStrip>

        <div className="grid min-w-0 gap-4 lg:grid-cols-5">
          <section className="min-w-0 rounded-xl border border-border bg-card lg:col-span-2">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-sm font-medium">Needs attention</h2>
              <span className="text-xs text-muted-foreground">{open.length} open</span>
            </div>
            {attention.length === 0 ? (
              <div className="flex items-center gap-3 px-4 py-8 text-sm text-muted-foreground">
                <CircleCheck className="size-4 text-success" aria-hidden="true" />
                {live.length ? "Nothing is waiting on a person." : "No live runs yet. Start a conversation to see decisions here."}
              </div>
            ) : (
              <ul className="divide-y divide-border">{attention.map((run) => <RunRow key={run.run_id} run={run} onOpen={onOpenRun} />)}</ul>
            )}
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card lg:col-span-3">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-sm font-medium">Recent decisions</h2>
              <Button variant="ghost" size="xs" onClick={onOpenRuns}>All runs<ArrowRight aria-hidden="true" /></Button>
            </div>
            {live.length === 0 ? (
              <p className="px-4 py-8 text-sm text-muted-foreground">Recorded demo runs are excluded here. Send a message from Conversations or Playground.</p>
            ) : (
              <ul className="divide-y divide-border">{live.slice(0, 8).map((run) => <RunRow key={run.run_id} run={run} onOpen={onOpenRun} />)}</ul>
            )}
          </section>
        </div>

        <section className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-4" aria-label="How a decision is made">
          {[
            ["Proposal", "The model proposes an intent and arguments. It has no authority."],
            ["Policy", "Customer scope, target, and eligibility are validated deterministically."],
            ["Authority", "Writes wait for explicit confirmation; reads get read access only."],
            ["Outcome", "One business effect, recorded with its evidence."],
          ].map(([title, detail]) => (
            <div key={title} className="bg-card px-4 py-3">
              <h3 className="text-sm font-medium text-foreground">{title}</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>
            </div>
          ))}
        </section>
      </div>
    </>
  );
}
