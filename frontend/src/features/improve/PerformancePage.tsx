import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/primitives/chart";
import { Skeleton } from "@/components/primitives/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/primitives/table";
import { useState } from "react";
import { PageHeader } from "../../components/layout/PageHeader";
import { Stat, StatStrip } from "../../components/layout/StatStrip";
import { humanize } from "../runs/runPresentation";
import { usePerformance } from "./queries";
import { EmptyNote, percent, rating, Section, WindowPicker } from "./shared";
import type { PerformanceView } from "./types";

const trendConfig = {
  ai_resolved: { label: "Resolved by AI", color: "var(--chart-1)" },
  handed_off: { label: "Handed to a person", color: "var(--chart-3)" },
} satisfies ChartConfig;

const dayLabel = (day: string) => new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function PerformanceSummaryStrip({ summary }: { summary: PerformanceView["summary"] }) {
  return (
    <StatStrip label="Performance summary">
      <Stat label="Conversations" value={summary.conversations} detail={`${summary.ai_runs} AI runs`} />
      <Stat label="AI resolution rate" value={percent(summary.ai_resolution_rate)} detail={`${summary.ai_resolved} resolved without a person`} />
      <Stat label="Handoff rate" value={percent(summary.handoff_rate)} detail={`${summary.handed_off} handed off`} />
      <Stat label="CSAT" value={rating(summary.csat_average)} detail={summary.csat_responses ? `${summary.csat_responses} ratings out of 5` : "No ratings yet"} />
      <Stat label="CSAT by who resolved" value={<span className="text-base">{rating(summary.csat_ai_average)} / {rating(summary.csat_human_average)}</span>} detail="AI / person" />
      <Stat label="Failed runs" value={summary.failed_runs} detail={summary.ai_runs ? percent(summary.failed_runs / summary.ai_runs) + " of runs" : undefined} />
    </StatStrip>
  );
}

export function PerformancePage({ onOpenProcedure }: { onOpenProcedure: (intent: string) => void }) {
  const [days, setDays] = useState(30);
  const query = usePerformance(days);
  const data = query.data;
  const trend = (data?.daily ?? []).map((point) => ({ ...point, label: dayLabel(point.day) }));

  return (
    <div className="min-w-0">
      <PageHeader title="Performance" description="How often the assistant resolves conversations on its own, when people step in, and how customers rate the outcome."
        actions={<WindowPicker value={days} onChange={setDays} />} />
      <div className="grid gap-4 p-4 sm:p-6">
        {query.isError && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">Performance data is unavailable. Check that the operator API is running.</p>}
        {!data ? <Skeleton className="h-24 w-full rounded-xl" /> : <PerformanceSummaryStrip summary={data.summary} />}

        <Section title="Resolutions per day" description="A conversation counts as handed off once it reached a person, even if the AI answered first.">
          {!data ? <Skeleton className="m-4 h-56" /> : data.summary.conversations === 0 ? <EmptyNote>No conversations in this window.</EmptyNote> : (
            <ChartContainer config={trendConfig} className="aspect-auto h-60 w-full p-2">
              <BarChart data={trend} margin={{ left: 4, right: 12, top: 12 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis tickLine={false} axisLine={false} width={32} allowDecimals={false} />
                <ChartTooltip isAnimationActive={false} cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent />} />
                <Bar dataKey="ai_resolved" stackId="outcome" fill="var(--color-ai_resolved)" isAnimationActive={false} />
                <Bar dataKey="handed_off" stackId="outcome" fill="var(--color-handed_off)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ChartContainer>
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
                      <button type="button" onClick={() => onOpenProcedure(topic.intent)} className="rounded text-left text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
                        {humanize(topic.intent)}
                      </button>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{topic.conversations}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{topic.runs}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{percent(topic.handoff_rate)}</TableCell>
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
