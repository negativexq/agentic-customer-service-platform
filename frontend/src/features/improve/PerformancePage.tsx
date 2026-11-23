import { Skeleton } from "@/components/primitives/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/primitives/table";
import { useState } from "react";
import { PageHeader } from "../../components/layout/PageHeader";
import { change, KpiCard, KpiGrid } from "../../components/layout/KpiCard";
import { Bot, MessagesSquare, Star, UserRound } from "lucide-react";
import { humanize } from "../runs/runPresentation";
import { usePerformance } from "./queries";
import { compareWindow, EmptyNote, percent, rating, Section, WindowPicker, windowHalves } from "./shared";
import type { PerformanceView } from "./types";
import { UI_LOCALE } from "@/lib/utils";
import { DotMatrixChart } from "../../components/charts/DotMatrixChart";

const dayLabel = (day: string) => new Date(`${day}T00:00:00`).toLocaleDateString(UI_LOCALE, { month: "short", day: "numeric" });

type Comparison = { conversations: number; ai_resolved: number; handed_off: number };

const rate = (part: number, whole: number) => (whole > 0 ? part / whole : null);
const pointsBetween = (current: number | null, previous: number | null) => (current === null || previous === null ? null : (current - previous) * 100);

export function PerformanceSummaryStrip({ summary, daily = [], previous, current }: {
  summary: PerformanceView["summary"];
  daily?: PerformanceView["daily"];
  /** Totals of the window before this one, and of this one counted the same way; omitted when there is nothing to compare. */
  previous?: Comparison;
  current?: Comparison;
}) {
  const compare = previous && current;
  const period = `vs previous ${summary.window_days} days`;
  return (
    <KpiGrid label="Performance summary">
      <KpiCard label="Conversations" icon={MessagesSquare} value={summary.conversations.toLocaleString(UI_LOCALE)} period={period}
        delta={compare ? { value: change(current.conversations, previous.conversations) } : undefined}
        detail={compare ? undefined : `${summary.ai_runs} AI runs`}
        spark={daily.map((point) => point.conversations)} sparkTone="var(--foreground)"
        footer={[["AI runs", summary.ai_runs], ["Failed runs", summary.ai_runs ? `${summary.failed_runs} · ${percent(summary.failed_runs / summary.ai_runs)}` : summary.failed_runs]]} />
      <KpiCard label="AI resolution rate" icon={Bot} value={percent(summary.ai_resolution_rate)} period={period}
        delta={compare ? { value: pointsBetween(rate(current.ai_resolved, current.conversations), rate(previous.ai_resolved, previous.conversations)), unit: "pts" } : undefined}
        detail={`${summary.ai_resolved} without a person`}
        spark={daily.map((point) => point.ai_resolved)} />
      <KpiCard label="Handoff rate" icon={UserRound} value={percent(summary.handoff_rate)} period={period}
        delta={compare ? { value: pointsBetween(rate(current.handed_off, current.conversations), rate(previous.handed_off, previous.conversations)), unit: "pts", goodWhenUp: false } : undefined}
        detail={`${summary.handed_off} handed off`}
        spark={daily.map((point) => point.handed_off)} sparkTone="var(--foreground)" />
      <KpiCard label="CSAT" icon={Star} value={rating(summary.csat_average)} unit={summary.csat_average === null ? undefined : "/ 5"}
        detail={summary.csat_responses ? `${summary.csat_responses} ratings` : "No ratings yet"}
        footer={[["Resolved by AI", rating(summary.csat_ai_average)], ["Resolved by a person", rating(summary.csat_human_average)]]} />
    </KpiGrid>
  );
}

const toComparison = (half: Record<string, number>): Comparison => ({ conversations: half.conversations, ai_resolved: half.ai_resolved, handed_off: half.handed_off });

/** The chosen window plus, when the API can serve twice its length, the same totals for the window before it. */
export function usePerformanceComparison(days: number) {
  const query = usePerformance(days);
  const span = compareWindow(days);
  const wide = usePerformance(span ?? days);
  const halves = span && wide.data && !wide.isPlaceholderData ? windowHalves(wide.data.daily, days, ["conversations", "ai_resolved", "handed_off"]) : null;
  return { query, data: query.data, current: halves ? toComparison(halves[0]) : undefined, previous: halves ? toComparison(halves[1]) : undefined };
}

export function PerformancePage({ onOpenProcedure }: { onOpenProcedure: (intent: string) => void }) {
  const [days, setDays] = useState(30);
  const { query, data, current, previous } = usePerformanceComparison(days);
  const trend = (data?.daily ?? []).map((point) => ({ ...point, label: dayLabel(point.day) }));

  return (
    <div className="min-w-0">
      <PageHeader title="Performance" description="How often the assistant resolves conversations on its own, when people step in, and how customers rate the outcome."
        actions={<WindowPicker value={days} onChange={setDays} />} />
      <div className="grid gap-4 p-4 sm:p-6">
        {query.isError && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">Performance data is unavailable. Check that the operator API is running.</p>}
        {!data ? <Skeleton className="h-24 w-full rounded-xl" /> : <PerformanceSummaryStrip summary={data.summary} daily={data.daily} current={current} previous={previous} />}

        <Section title="Resolutions per day" description="A conversation counts as handed off once it reached a person, even if the AI answered first.">
          {!data ? <Skeleton className="m-4 h-56" /> : data.summary.conversations === 0 ? <EmptyNote>No conversations in this window.</EmptyNote> : (
            <DotMatrixChart label="Resolutions per day" rows={18}
              series={[{ key: "ai_resolved", label: "Resolved by AI", color: "var(--chart-1)" }, { key: "handed_off", label: "Handed to a person", color: "var(--foreground)" }]}
              data={trend.map((point) => ({ label: point.label, values: { ai_resolved: point.ai_resolved, handed_off: point.handed_off } }))} />
          )}
        </Section>

        <Section title="Topics" description="Grouped by the intent the assistant recognised. Open a topic to see its procedure.">
          {!data ? <Skeleton className="m-4 h-40" /> : data.topics.length === 0 ? <EmptyNote>No AI runs in this window.</EmptyNote> : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Topic</TableHead>
                  <TableHead className="text-right">Conversations</TableHead>
                  <TableHead className="text-right">Runs</TableHead>
                  <TableHead className="text-right">Handoff rate</TableHead>
                  <TableHead className="text-right">Clarifications</TableHead>
                  <TableHead className="text-right">Failures</TableHead>
                  <TableHead className="pr-4 text-right">CSAT</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.topics.map((topic) => (
                  <TableRow key={topic.intent}>
                    <TableCell className="pl-4">
                      <button type="button" onClick={() => onOpenProcedure(topic.intent)} className="-my-1 inline-flex min-h-7 items-center rounded px-1 -mx-1 text-left text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
                        {humanize(topic.intent)}
                      </button>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{topic.conversations}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{topic.runs}</TableCell>
                    <TableCell className="text-right">
                      <span className="inline-flex items-center justify-end gap-2">
                        <span className="track-hatched hidden h-1.5 w-16 overflow-hidden rounded-full sm:block" aria-hidden="true">
                          <span className="block h-full rounded-full bg-foreground/70" style={{ width: `${Math.round((topic.handoff_rate ?? 0) * 100)}%` }} />
                        </span>
                        <span className="w-9 font-mono tabular-nums">{percent(topic.handoff_rate)}</span>
                      </span>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{topic.clarifications}</TableCell>
                    <TableCell className={`text-right font-mono tabular-nums ${topic.failures ? "text-danger" : ""}`}>{topic.failures}</TableCell>
                    <TableCell className="pr-4 text-right font-mono tabular-nums">{rating(topic.csat_average)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      </div>
    </div>
  );
}
