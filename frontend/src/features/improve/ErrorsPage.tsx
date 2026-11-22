import { useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/primitives/chart";
import { Skeleton } from "@/components/primitives/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/primitives/table";
import { PageHeader } from "../../components/layout/PageHeader";
import { Stat, StatStrip } from "../../components/layout/StatStrip";
import { formatRelative, humanize } from "../runs/runPresentation";
import { useErrorLog } from "./queries";
import { EmptyNote, percent, Section, WindowPicker } from "./shared";

const palette = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

export function ErrorsPage({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  const [days, setDays] = useState(7);
  const [category, setCategory] = useState<string | null>(null);
  const query = useErrorLog(days);
  const data = query.data;
  const names = (data?.categories ?? []).map((item) => item.category);
  const config = Object.fromEntries(names.map((name, index) => [name, { label: humanize(name), color: palette[index % palette.length] }])) satisfies ChartConfig;
  const chart = (data?.daily ?? []).map((point) => ({ ...point, label: new Date(`${point.day}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) }));
  const errors = (data?.errors ?? []).filter((row) => !category || row.category === category);

  return (
    <div className="min-w-0">
      <PageHeader title="Errors" description="Runs that failed or degraded, grouped by cause. Open a run for its full trace." actions={<WindowPicker value={days} onChange={setDays} />} />
      <div className="grid gap-4 p-4 sm:p-6">
        {query.isError && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">The error log is unavailable.</p>}
        {!data ? <Skeleton className="h-24 w-full rounded-xl" /> : (
          <StatStrip label="Error summary">
            <Stat label="Runs" value={data.total_runs} />
            <Stat label="Failed or degraded" value={data.failed_runs} detail={data.total_runs ? `${percent(data.failed_runs / data.total_runs)} of runs` : undefined} />
            {data.categories.slice(0, 4).map((item) => <Stat key={item.category} label={humanize(item.category)} value={item.count} detail={`Last ${formatRelative(item.last_seen)}`} />)}
          </StatStrip>
        )}
        <Section title="Errors per day">
          {!data ? <Skeleton className="m-4 h-52" /> : data.failed_runs === 0 ? <EmptyNote>No failed runs in the last {days} days.</EmptyNote> : (
            <ChartContainer config={config} className="aspect-auto h-52 w-full p-2">
              <BarChart data={chart} margin={{ left: 4, right: 12, top: 12 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis tickLine={false} axisLine={false} width={28} allowDecimals={false} />
                <ChartTooltip isAnimationActive={false} cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent />} />
                {names.map((name, index) => <Bar key={name} dataKey={name} stackId="errors" fill={`var(--color-${name})`} radius={index === names.length - 1 ? [2, 2, 0, 0] : 0} isAnimationActive={false} />)}
              </BarChart>
            </ChartContainer>
          )}
        </Section>
        <Section title="Failed runs" description={category ? humanize(category) : undefined}
          actions={names.length > 0 && (
            <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Error category">
              {[null, ...names].map((value) => (
                <button key={value ?? "all"} type="button" role="radio" aria-checked={category === value} onClick={() => setCategory(value)}
                  className={`h-6 rounded-md border px-2 text-xs focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${category === value ? "border-foreground/20 bg-muted text-foreground" : "border-border text-muted-foreground"}`}>
                  {value ? humanize(value) : "All"}
                </button>
              ))}
            </div>
          )}>
          {!data ? <Skeleton className="m-4 h-40" /> : errors.length === 0 ? <EmptyNote>Nothing failed.</EmptyNote> : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">When</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Intent</TableHead>
                  <TableHead>What broke</TableHead>
                  <TableHead>Recovery</TableHead>
                  <TableHead className="pr-4 text-right">Run</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {errors.map((row) => (
                  <TableRow key={row.run_id}>
                    <TableCell className="pl-4 text-muted-foreground">{formatRelative(row.at)}</TableCell>
                    <TableCell>{humanize(row.category)}</TableCell>
                    <TableCell>{humanize(row.intent)}</TableCell>
                    <TableCell className="max-w-64 truncate text-muted-foreground">{[...row.failed_tools, ...row.degraded_components].join(", ") || humanize(row.status)}</TableCell>
                    <TableCell className="text-muted-foreground">{row.recovery_action ? humanize(row.recovery_action) : "—"}</TableCell>
                    <TableCell className="pr-4 text-right">
                      <button type="button" onClick={() => onOpenRun(row.run_id)} className="-my-1 inline-flex min-h-7 items-center rounded px-1 font-mono text-xs text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">{row.run_id.slice(0, 12)}</button>
                    </TableCell>
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
