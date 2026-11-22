import { Activity } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/primitives/chart";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/primitives/empty";
import { Skeleton } from "@/components/primitives/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/primitives/table";
import { PageHeader } from "../../components/layout/PageHeader";
import { Stat, StatStrip } from "../../components/layout/StatStrip";
import type { AgentRun, Health } from "../../types";
import { formatDuration, formatRelative, humanize, isRecorded } from "../runs/runPresentation";
import { deriveTelemetry } from "./telemetry";

const latencyConfig = { latency: { label: "Latency (ms)", color: "var(--chart-1)" } } satisfies ChartConfig;
const tokenConfig = {
  tokensIn: { label: "Input tokens", color: "var(--chart-3)" },
  tokensOut: { label: "Output tokens", color: "var(--chart-2)" },
} satisfies ChartConfig;

const componentTone: Record<string, string> = {
  healthy: "bg-success", configured: "bg-success", unavailable: "bg-danger", incompatible: "bg-danger",
  not_configured: "bg-muted-foreground/50", not_probed: "bg-muted-foreground/50",
};

function Section({ title, description, children, className = "" }: { title: string; description?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-border bg-card ${className}`}>
      <div className="flex items-baseline justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-medium text-foreground">{title}</h2>
        {description && <p className="truncate text-xs text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function BarList({ items, format }: { items: { label: string; value: number; detail?: string }[]; format: (value: number) => string }) {
  const max = Math.max(...items.map((item) => item.value), 1);
  return (
    <ul className="grid gap-2.5 p-4">
      {items.map((item) => (
        <li key={item.label} className="grid gap-1">
          <div className="flex items-baseline justify-between gap-3 text-xs">
            <span className="truncate text-foreground">{item.label}</span>
            <span className="shrink-0 font-mono tabular-nums text-muted-foreground">{format(item.value)}{item.detail ? ` · ${item.detail}` : ""}</span>
          </div>
          <div className="h-1.5 rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary/70" style={{ width: `${Math.max(2, (item.value / max) * 100)}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

type MonitoringPageProps = {
  runs: AgentRun[];
  health: Health | null | undefined;
  loading: boolean;
  healthUnavailable: boolean;
  onOpenRun: (runId: string) => void;
};

export function MonitoringPage({ runs, health, loading, healthUnavailable, onOpenRun }: MonitoringPageProps) {
  const telemetry = useMemo(() => deriveTelemetry(runs), [runs]);
  const generations = useMemo(
    () => runs.filter((run) => !isRecorded(run) && run.provider_metadata?.latency_ms != null).sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 25),
    [runs],
  );
  const tokens = telemetry.inputTokens + telemetry.outputTokens;

  return (
    <>
      <PageHeader
        title="Monitoring"
        description="LLM generations, latency, token usage, and system health across recent live runs. Recorded demo runs are excluded."
        actions={<span className="text-xs text-muted-foreground">Last {telemetry.runCount} live runs · refreshes every 30 s</span>}
      />
      <div className="grid min-w-0 gap-4 p-4 sm:p-6">
        {loading && runs.length === 0 ? (
          <div className="grid gap-4"><Skeleton className="h-20" /><Skeleton className="h-64" /></div>
        ) : telemetry.runCount === 0 ? (
          <Empty className="rounded-xl border border-dashed border-border py-16">
            <EmptyHeader>
              <EmptyMedia variant="icon"><Activity aria-hidden="true" /></EmptyMedia>
              <EmptyTitle>No live runs to measure yet</EmptyTitle>
              <EmptyDescription>Send a message from Conversations or Playground. Latency, tokens, and outcomes appear here as runs are recorded.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <>
            <StatStrip label="Live run telemetry">
              <Stat label="Runs" value={String(telemetry.runCount)} detail={`${telemetry.generationCount} LLM generations`} />
              <Stat label="Success rate" value={telemetry.successRate === null ? "—" : `${(telemetry.successRate * 100).toFixed(1)}%`} detail={`${telemetry.errorCount} failed`} />
              <Stat label="Latency p50" value={formatDuration(telemetry.latencyP50)} />
              <Stat label="Latency p95" value={formatDuration(telemetry.latencyP95)} />
              <Stat label="Tokens" value={tokens.toLocaleString()} detail={`${telemetry.inputTokens.toLocaleString()} in · ${telemetry.outputTokens.toLocaleString()} out`} />
              <Stat label="Escalations" value={String(telemetry.escalationCount)} detail={telemetry.costUsd === null ? "Cost not reported" : `$${telemetry.costUsd.toFixed(4)} cost`} />
            </StatStrip>

            <div className="grid min-w-0 gap-4 xl:grid-cols-3">
              <Section title="Latency per run" description="Dashed line marks p95" className="xl:col-span-2">
                <ChartContainer config={latencyConfig} className="aspect-auto h-56 w-full p-2">
                  <AreaChart data={telemetry.timeline} margin={{ left: 4, right: 12, top: 12 }}>
                    <defs>
                      <linearGradient id="latencyFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--color-latency)" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="var(--color-latency)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid vertical={false} strokeDasharray="3 3" />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={32} />
                    <YAxis tickLine={false} axisLine={false} width={44} tickFormatter={(value: number) => (value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}`)} />
                    <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
                    {telemetry.latencyP95 !== null && <ReferenceLine y={telemetry.latencyP95} stroke="var(--warning)" strokeDasharray="4 4" />}
                    <Area dataKey="latency" type="monotone" stroke="var(--color-latency)" strokeWidth={1.5} fill="url(#latencyFill)" />
                  </AreaChart>
                </ChartContainer>
              </Section>
              <Section title="Outcomes">
                <BarList items={telemetry.outcomes.map((item) => ({ label: item.label, value: item.count }))} format={(value) => String(value)} />
              </Section>
            </div>

            <div className="grid min-w-0 gap-4 xl:grid-cols-3">
              <Section title="Token usage per run" className="xl:col-span-2">
                <ChartContainer config={tokenConfig} className="aspect-auto h-56 w-full p-2">
                  <BarChart data={telemetry.timeline} margin={{ left: 4, right: 12, top: 12 }}>
                    <CartesianGrid vertical={false} strokeDasharray="3 3" />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={32} />
                    <YAxis tickLine={false} axisLine={false} width={44} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar dataKey="tokensIn" stackId="tokens" fill="var(--color-tokensIn)" radius={[0, 0, 2, 2]} />
                    <Bar dataKey="tokensOut" stackId="tokens" fill="var(--color-tokensOut)" radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ChartContainer>
              </Section>
              <Section title="Where time goes" description="Average per pipeline stage">
                {telemetry.stages.length === 0 ? <p className="p-4 text-sm text-muted-foreground">No stage timings recorded.</p> : (
                  <BarList items={telemetry.stages.slice(0, 7).map((item) => ({ label: humanize(item.stage), value: item.avgMs, detail: `${item.samples}×` }))} format={(value) => formatDuration(value)} />
                )}
              </Section>
            </div>

            <div className="grid min-w-0 gap-4 xl:grid-cols-3">
              <Section title="Generations" description="Most recent LLM calls" className="xl:col-span-2">
                <div className="overflow-x-auto">
                  <Table className="min-w-[620px]">
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        {["Time", "Intent", "Model", "Latency", "Input", "Output"].map((label) => <TableHead key={label} className="h-9 px-4 text-xs font-medium text-muted-foreground">{label}</TableHead>)}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {generations.map((run) => (
                        <TableRow key={run.run_id} className="cursor-pointer" onClick={() => onOpenRun(run.run_id)}>
                          <TableCell className="px-4 text-xs text-muted-foreground">{formatRelative(run.started_at)}</TableCell>
                          <TableCell className="px-4 text-sm">{humanize(run.intent)}</TableCell>
                          <TableCell className="px-4 font-mono text-xs text-muted-foreground">{run.provider_metadata?.model ?? run.model ?? "—"}</TableCell>
                          <TableCell className="px-4 font-mono text-xs tabular-nums">{formatDuration(run.provider_metadata?.latency_ms)}</TableCell>
                          <TableCell className="px-4 font-mono text-xs tabular-nums text-muted-foreground">{run.provider_metadata?.input_tokens ?? "—"}</TableCell>
                          <TableCell className="px-4 font-mono text-xs tabular-nums text-muted-foreground">{run.provider_metadata?.output_tokens ?? "—"}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </Section>
              <div className="grid min-w-0 content-start gap-4">
                <Section title="Models">
                  <ul className="divide-y divide-border">
                    {telemetry.models.length === 0 && <li className="p-4 text-sm text-muted-foreground">No model calls recorded.</li>}
                    {telemetry.models.map((model) => (
                      <li key={model.model} className="grid gap-1 px-4 py-3">
                        <span className="truncate font-mono text-xs text-foreground">{model.model}</span>
                        <span className="text-xs text-muted-foreground">{model.calls} calls · {formatDuration(model.avgLatency)} avg · {model.tokens.toLocaleString()} tokens{model.errors ? ` · ${model.errors} errors` : ""}</span>
                      </li>
                    ))}
                  </ul>
                </Section>
                <Section title="System components" description={healthUnavailable ? "API unreachable" : health?.status === "ready" ? "Ready" : "Degraded"}>
                  <ul className="divide-y divide-border">
                    {(health?.components ?? []).map((component) => (
                      <li key={component.name} className="flex items-start gap-2.5 px-4 py-2.5">
                        <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${componentTone[component.status] ?? "bg-muted-foreground/50"}`} aria-hidden="true" />
                        <span className="grid min-w-0 gap-0.5">
                          <span className="text-sm text-foreground">{humanize(component.name)}</span>
                          <span className="truncate text-xs text-muted-foreground" title={component.detail}>{humanize(component.status)} · {component.detail}</span>
                        </span>
                      </li>
                    ))}
                    {!health && <li className="p-4 text-sm text-muted-foreground">{healthUnavailable ? "The health endpoint did not respond." : "Checking components…"}</li>}
                  </ul>
                </Section>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
