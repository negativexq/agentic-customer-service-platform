import { useState } from "react";
import { Skeleton } from "@/components/primitives/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/primitives/table";
import { PageHeader } from "../../components/layout/PageHeader";
import { change, KpiCard, KpiGrid } from "../../components/layout/KpiCard";
import { RankedBars } from "../../components/charts/RankedBars";
import { AlertTriangle, Activity } from "lucide-react";
import { formatRelative, humanize } from "../runs/runPresentation";
import { useErrorLog } from "./queries";
import { compareWindow, EmptyNote, percent, Section, WindowPicker, windowHalves } from "./shared";
import { UI_LOCALE } from "@/lib/utils";
import { DotMatrixChart } from "../../components/charts/DotMatrixChart";

const palette = ["var(--chart-5)", "var(--chart-4)", "var(--chart-3)", "var(--chart-1)", "var(--chart-2)"];

export function ErrorsPage({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  const [days, setDays] = useState(7);
  const [category, setCategory] = useState<string | null>(null);
  const query = useErrorLog(days);
  const data = query.data;
  const names = (data?.categories ?? []).map((item) => item.category);
  const chart = (data?.daily ?? []).map((point) => ({ ...point, label: new Date(`${point.day}T00:00:00`).toLocaleDateString(UI_LOCALE, { month: "short", day: "numeric" }) }));
  const span = compareWindow(days);
  const wide = useErrorLog(span ?? days);
  const halves = span && wide.data && !wide.isPlaceholderData ? windowHalves(wide.data.daily, days, ["total", ...names]) : null;
  const period = `vs previous ${days} days`;
  const series = (key: string) => (data?.daily ?? []).map((point) => Number((point as Record<string, unknown>)[key] ?? 0));
  const colorOf = (name: string) => palette[Math.max(0, names.indexOf(name)) % palette.length];
  const errors = (data?.errors ?? []).filter((row) => !category || row.category === category);

  return (
    <div className="min-w-0">
      <PageHeader title="Errors" description="Runs that failed or degraded, grouped by cause. Open a run for its full trace." actions={<WindowPicker value={days} onChange={setDays} />} />
      <div className="grid gap-4 p-4 sm:p-6">
        {query.isError && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">The error log is unavailable.</p>}
        {!data ? <Skeleton className="h-24 w-full rounded-xl" /> : (
          <KpiGrid label="Error summary">
            <KpiCard label="Failed or degraded" icon={AlertTriangle} value={data.failed_runs.toLocaleString(UI_LOCALE)} period={period}
              delta={halves ? { value: change(halves[0].total, halves[1].total), goodWhenUp: false } : undefined}
              detail={data.total_runs ? `${percent(data.failed_runs / data.total_runs)} of ${data.total_runs.toLocaleString(UI_LOCALE)} runs` : "No runs yet"}
              spark={series("total")} sparkTone="var(--danger)" />
            {data.categories.slice(0, 3).map((item) => (
              <KpiCard key={item.category} label={humanize(item.category)} icon={Activity} value={item.count.toLocaleString(UI_LOCALE)} period={period}
                delta={halves ? { value: change(halves[0][item.category] ?? 0, halves[1][item.category] ?? 0), goodWhenUp: false } : undefined}
                detail={`Last ${formatRelative(item.last_seen)}`}
                spark={series(item.category)} sparkTone={colorOf(item.category)} />
            ))}
          </KpiGrid>
        )}
        <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <Section title="Errors per day">
          {!data ? <Skeleton className="m-4 h-52" /> : data.failed_runs === 0 ? <EmptyNote>No failed runs in the last {days} days.</EmptyNote> : (
            <DotMatrixChart label="Errors per day" rows={14}
              series={names.map((name, index) => ({ key: name, label: humanize(name), color: palette[index % palette.length] }))}
              data={chart.map((point) => ({ label: point.label, values: Object.fromEntries(names.map((name) => [name, Number((point as Record<string, unknown>)[name] ?? 0)])) }))} />
          )}
        </Section>
        <Section title="Top causes" description="Select one to filter the runs below.">
          {!data ? <Skeleton className="m-4 h-40" /> : data.categories.length === 0 ? <EmptyNote>Nothing failed.</EmptyNote> : (
            <RankedBars label="Top causes" items={data.categories.slice(0, 6).map((item) => ({
              label: humanize(item.category), value: item.count, color: colorOf(item.category),
              detail: data.failed_runs ? percent(item.count / data.failed_runs) : undefined,
              selected: category === item.category,
              onSelect: () => setCategory(category === item.category ? null : item.category),
            }))} />
          )}
        </Section>
        </div>
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
