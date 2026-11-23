import { ArrowRight, Beaker, CircleCheck, Clock, MessagesSquare } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/primitives/button";
import { Skeleton } from "@/components/primitives/skeleton";
import { UI_LOCALE } from "@/lib/utils";
import { DotMatrixChart } from "../../components/charts/DotMatrixChart";
import { RankedBars } from "../../components/charts/RankedBars";
import { PageHeader } from "../../components/layout/PageHeader";
import type { ConsoleView } from "../../app/routes";
import type { AgentRun, OperatorConversation } from "../../types";
import { PerformanceSummaryStrip, usePerformanceComparison } from "../improve/PerformancePage";
import { useErrorLog, useQuality } from "../improve/queries";
import { EmptyNote, percent, Section, WindowPicker } from "../improve/shared";
import { DecisionPath } from "../runs/DecisionPath";
import { formatRelative, humanize, isRecorded, runStatus } from "../runs/runPresentation";
import { StatusLabel } from "../runs/StatusLabel";
import { slaLabel, slaShort, slaTone } from "../workspace/sla";

type OverviewPageProps = {
  runs: AgentRun[];
  inbox: OperatorConversation[];
  inboxLoading: boolean;
  onOpenRun: (runId: string) => void;
  onOpenConversation: (pk: number) => void;
  onOpenConversations: () => void;
  onNavigate: (view: ConsoleView) => void;
  onExploreDemo: () => void;
};

const dayLabel = (day: string) => new Date(`${day}T00:00:00`).toLocaleDateString(UI_LOCALE, { month: "short", day: "numeric" });
const causeColors = ["var(--chart-5)", "var(--chart-4)", "var(--chart-3)", "var(--chart-1)", "var(--chart-2)"];

function SectionLink({ label, onClick }: { label: string; onClick: () => void }) {
  return <Button variant="ghost" size="xs" onClick={onClick}>{label}<ArrowRight aria-hidden="true" /></Button>;
}

/** Customers waiting for a person, most urgent first: the one list an operator acts on from here. */
function WaitingQueue({ items, loading, onOpen }: { items: OperatorConversation[]; loading: boolean; onOpen: (pk: number) => void }) {
  if (loading) return <div className="grid gap-2 p-4">{[0, 1, 2].map((key) => <Skeleton key={key} className="h-11" />)}</div>;
  if (items.length === 0) {
    return (
      <div className="flex items-center gap-3 px-4 py-8 text-sm text-muted-foreground">
        <CircleCheck className="size-4 text-success" aria-hidden="true" />Nobody is waiting for a person.
      </div>
    );
  }
  return (
    <ul className="divide-y divide-border">
      {items.map((item) => (
        <li key={item.id}>
          <button type="button" onClick={() => onOpen(item.id)}
            className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5 px-4 py-2.5 text-left transition-colors hover:bg-accent/60 focus-visible:bg-accent/60 focus-visible:outline-none">
            <span className="truncate text-sm text-foreground">{item.customer_name}</span>
            {item.sla ? (
              <span className={`inline-flex items-center gap-1 text-xs tabular-nums ${slaTone[item.sla.state]}`} title={slaLabel(item.sla)}>
                <Clock className="size-3" aria-hidden="true" />{item.sla.state === "breached" ? `Overdue ${slaShort(item.sla)}` : slaShort(item.sla)}
              </span>
            ) : <span className="text-xs text-muted-foreground">{formatRelative(item.updated_at)}</span>}
            <span className="col-span-2 truncate text-xs text-muted-foreground">
              {item.escalation?.reason ? humanize(item.escalation.reason) : item.last_message?.content ?? "Waiting for a reply"}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function RunRow({ run, onOpen }: { run: AgentRun; onOpen: (runId: string) => void }) {
  const status = runStatus(run);
  return (
    <li>
      <a
        href={`/runs/${encodeURIComponent(run.run_id)}`}
        onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return; event.preventDefault(); onOpen(run.run_id); }}
        className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-2.5 transition-colors hover:bg-accent/60 focus-visible:bg-accent/60 focus-visible:outline-none sm:grid-cols-[minmax(0,1fr)_auto_auto_4.5rem]"
      >
        <span className="grid min-w-0 gap-0.5">
          <span className="truncate text-sm text-foreground">{humanize(run.intent)}</span>
          <span className="truncate font-mono text-xs text-muted-foreground">{run.conversation_id}</span>
        </span>
        <span className="hidden sm:inline-flex"><DecisionPath run={run} /></span>
        <StatusLabel {...status} />
        <span className="hidden text-right text-xs text-muted-foreground sm:block">{formatRelative(run.started_at)}</span>
      </a>
    </li>
  );
}

export function OverviewPage({ runs, inbox, inboxLoading, onOpenRun, onOpenConversation, onOpenConversations, onNavigate, onExploreDemo }: OverviewPageProps) {
  const [days, setDays] = useState(7);
  const performance = usePerformanceComparison(days);
  const errors = useErrorLog(days);
  const quality = useQuality(days);

  const live = useMemo(() => runs.filter((run) => !isRecorded(run)).sort((a, b) => b.started_at.localeCompare(a.started_at)), [runs]);
  const waiting = useMemo(() => {
    const urgency = { breached: 0, at_risk: 1, ok: 2 } as const;
    return inbox
      .filter((item) => item.owner === "queued")
      .sort((a, b) => (urgency[a.sla?.state ?? "ok"] - urgency[b.sla?.state ?? "ok"]) || (a.sla?.due_at ?? a.updated_at).localeCompare(b.sla?.due_at ?? b.updated_at));
  }, [inbox]);
  const breached = waiting.filter((item) => item.sla?.state === "breached").length;

  const data = performance.data;
  const topics = [...(data?.topics ?? [])].sort((a, b) => b.conversations - a.conversations).slice(0, 5);
  const causes = (errors.data?.categories ?? []).slice(0, 5);
  const monitors = [...(quality.data?.monitors ?? [])].filter((monitor) => monitor.open_flags > 0).sort((a, b) => b.open_flags - a.open_flags).slice(0, 5);

  return (
    <>
      <PageHeader
        title="Overview"
        description="How the assistant is doing, and who is waiting for a person."
        actions={
          <>
            <WindowPicker value={days} onChange={setDays} />
            <Button variant="outline" size="sm" onClick={onExploreDemo}><Beaker aria-hidden="true" />Refund workflow</Button>
            <Button size="sm" onClick={onOpenConversations}><MessagesSquare aria-hidden="true" />Open conversations</Button>
          </>
        }
      />
      <div className="grid min-w-0 gap-4 p-4 sm:p-6">
        {performance.query.isError && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">Performance data is unavailable. Check that the operator API is running.</p>}
        {!data
          ? <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((key) => <Skeleton key={key} className="h-36 rounded-xl" />)}</div>
          : <PerformanceSummaryStrip summary={data.summary} daily={data.daily} current={performance.current} previous={performance.previous} />}

        <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <Section title="Resolutions per day" description="Resolved by the assistant, or handed to a person."
            actions={<SectionLink label="Performance" onClick={() => onNavigate("performance")} />}>
            {!data ? <Skeleton className="m-4 h-48" /> : data.summary.conversations === 0 ? <EmptyNote>No conversations in this window.</EmptyNote> : (
              <DotMatrixChart label="Resolutions per day" rows={15}
                series={[{ key: "ai_resolved", label: "Resolved by AI", color: "var(--chart-1)" }, { key: "handed_off", label: "Handed to a person", color: "var(--foreground)" }]}
                data={data.daily.map((point) => ({ label: dayLabel(point.day), values: { ai_resolved: point.ai_resolved, handed_off: point.handed_off } }))} />
            )}
          </Section>

          <Section title="Waiting for a person"
            description={waiting.length === 0 ? undefined : breached ? `${waiting.length} waiting · ${breached} past the response target` : `${waiting.length} waiting`}
            actions={waiting.length > 0 && <SectionLink label="Inbox" onClick={onOpenConversations} />}
            className="flex flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto lg:max-h-[19rem]">
              <WaitingQueue items={waiting.slice(0, 6)} loading={inboxLoading && inbox.length === 0} onOpen={onOpenConversation} />
            </div>
          </Section>
        </div>

        <div className="grid min-w-0 gap-4 md:grid-cols-3">
          <Section title="Top topics" description="Conversations · handoff rate" actions={<SectionLink label="Topics" onClick={() => onNavigate("performance")} />}>
            {!data ? <Skeleton className="m-4 h-32" /> : topics.length === 0 ? <EmptyNote>No AI runs in this window.</EmptyNote> : (
              <RankedBars label="Top topics" items={topics.map((topic) => ({ label: humanize(topic.intent), value: topic.conversations, detail: `${percent(topic.handoff_rate)} handed off` }))} />
            )}
          </Section>
          <Section title="Error causes" description={errors.data ? `${errors.data.failed_runs} of ${errors.data.total_runs.toLocaleString(UI_LOCALE)} runs failed or degraded` : undefined}
            actions={<SectionLink label="Errors" onClick={() => onNavigate("errors")} />}>
            {!errors.data ? <Skeleton className="m-4 h-32" /> : causes.length === 0 ? <EmptyNote>Nothing failed in this window.</EmptyNote> : (
              <RankedBars label="Error causes" items={causes.map((item, index) => ({
                label: humanize(item.category), value: item.count, color: causeColors[index % causeColors.length],
                detail: errors.data.failed_runs ? percent(item.count / errors.data.failed_runs) : undefined,
              }))} />
            )}
          </Section>
          <Section title="Quality flags" description="Open flags waiting for review" actions={<SectionLink label="Quality" onClick={() => onNavigate("quality")} />}>
            {!quality.data ? <Skeleton className="m-4 h-32" /> : monitors.length === 0 ? <EmptyNote>No open flags.</EmptyNote> : (
              <RankedBars label="Quality flags" items={monitors.map((monitor) => ({
                label: monitor.name, value: monitor.open_flags,
                color: monitor.severity === "high" ? "var(--danger)" : monitor.severity === "medium" ? "var(--warning)" : "var(--muted-foreground)",
                detail: `${monitor.severity} severity`,
              }))} />
            )}
          </Section>
        </div>

        <Section title="Recent decisions" description="Live runs only; recorded demo runs are left out."
          actions={<SectionLink label="All runs" onClick={() => onNavigate("traces")} />}>
          {live.length === 0 ? (
            <EmptyNote>No live runs yet. Send a message from Conversations or Playground.</EmptyNote>
          ) : (
            <ul className="divide-y divide-border">{live.slice(0, 6).map((run) => <RunRow key={run.run_id} run={run} onOpen={onOpenRun} />)}</ul>
          )}
        </Section>
      </div>
    </>
  );
}
