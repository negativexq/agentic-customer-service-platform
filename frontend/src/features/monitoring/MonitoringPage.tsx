import { Activity } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/primitives/chart";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/primitives/empty";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/primitives/select";
import { Skeleton } from "@/components/primitives/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/primitives/table";
import { PageHeader } from "../../components/layout/PageHeader";
import { Stat, StatStrip } from "../../components/layout/StatStrip";
import type { AgentRun, Health } from "../../types";
import { formatDuration, formatRelative, humanize, isRecorded } from "../runs/runPresentation";
import { deriveTelemetry, percentile, type TimelineOutcome, type TimelinePoint } from "./telemetry";

const latencyConfig = { latency: { label: "Latency (ms)", color: "var(--chart-1)" } } satisfies ChartConfig;
const tokenConfig = {
  tokensIn: { label: "Input tokens", color: "var(--chart-3)" },
  tokensOut: { label: "Output tokens", color: "var(--chart-2)" },
} satisfies ChartConfig;

const componentTone: Record<string, string> = {
  healthy: "bg-success", configured: "bg-success", unavailable: "bg-danger", incompatible: "bg-danger",
  not_configured: "bg-muted-foreground/50", not_probed: "bg-muted-foreground/50",
};

function Section({ title, description, actions, children, className = "" }: { title: string; description?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-border bg-card ${className}`}>
      <div className="flex min-h-12 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border px-4 py-2">
        <div className="flex min-w-0 items-baseline gap-3">
          <h2 className="shrink-0 text-sm font-medium text-foreground">{title}</h2>
          {description && <p className="truncate text-xs text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-1.5">{actions}</div>}
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

type RangeFilter = "20" | "50" | "all";
type OutcomeFilter = "all" | TimelineOutcome;
type TokenSeries = "both" | "in" | "out";
type ChartFilters = { range: RangeFilter; model: string; outcome: OutcomeFilter };

const rangeLabels: Record<RangeFilter, string> = { "20": "Last 20 runs", "50": "Last 50 runs", all: "All runs" };
const outcomeLabels: Record<OutcomeFilter, string> = { all: "All outcomes", succeeded: "Succeeded", failed: "Failed", escalated: "Escalated" };
const seriesLabels: Record<TokenSeries, string> = { both: "In + out", in: "Input", out: "Output" };

function applyChartFilters(points: TimelinePoint[], filters: ChartFilters): TimelinePoint[] {
  const matching = points.filter((point) => (filters.model === "all" || point.model === filters.model) && (filters.outcome === "all" || point.outcome === filters.outcome));
  return filters.range === "all" ? matching : matching.slice(-Number(filters.range));
}

function FilterSelect<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void }) {
  return (
    <Select value={value} onValueChange={(next) => next && onChange(next as T)}>
      <SelectTrigger size="sm" aria-label={label} className="h-7 gap-1 px-2 text-xs"><SelectValue>{(current: string) => options.find((option) => option.value === current)?.label ?? current}</SelectValue></SelectTrigger>
      <SelectContent>
        {options.map((option) => <SelectItem key={option.value} value={option.value} className="text-xs">{option.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

function ChartFilterControls({ filters, models, onChange, idPrefix }: { filters: ChartFilters; models: string[]; onChange: (next: ChartFilters) => void; idPrefix: string }) {
  return (
    <>
      <FilterSelect label={`${idPrefix} range`} value={filters.range} onChange={(range) => onChange({ ...filters, range })}
        options={(Object.keys(rangeLabels) as RangeFilter[]).map((value) => ({ value, label: rangeLabels[value] }))} />
      <FilterSelect label={`${idPrefix} model`} value={filters.model} onChange={(model) => onChange({ ...filters, model })}
        options={[{ value: "all", label: "All models" }, ...models.map((model) => ({ value: model, label: model }))]} />
      <FilterSelect label={`${idPrefix} outcome`} value={filters.outcome} onChange={(outcome) => onChange({ ...filters, outcome })}
        options={(Object.keys(outcomeLabels) as OutcomeFilter[]).map((value) => ({ value, label: outcomeLabels[value] }))} />
    </>
  );
}

const defaultChartFilters: ChartFilters = { range: "50", model: "all", outcome: "all" };

type MonitoringPageProps = {
  runs: AgentRun[];
  health: Health | null | undefined;
  loading: boolean;
  healthUnavailable: boolean;
  onOpenRun: (runId: string) => void;
  initialConversation?: string;
};

export function MonitoringPage({ runs: allRuns, health, loading, healthUnavailable, onOpenRun, initialConversation = "all" }: MonitoringPageProps) {
  const [conversation, setConversation] = useState(initialConversation);
  const conversationOptions = useMemo(() => {
    const counts = new Map<string, { count: number; last: string; customer: number }>();
    for (const run of allRuns) {
      if (isRecorded(run)) continue;
      const entry = counts.get(run.conversation_id) ?? { count: 0, last: run.started_at, customer: run.customer_id };
      entry.count += 1;
      if (run.started_at > entry.last) entry.last = run.started_at;
      counts.set(run.conversation_id, entry);
    }
    return [...counts].sort((a, b) => b[1].last.localeCompare(a[1].last)).map(([id, entry]) => ({ id, ...entry }));
  }, [allRuns]);
  const runs = useMemo(() => (conversation === "all" ? allRuns : allRuns.filter((run) => run.conversation_id === conversation)), [allRuns, conversation]);
  const telemetry = useMemo(() => deriveTelemetry(runs), [runs]);
  const generations = useMemo(
    () => runs.filter((run) => !isRecorded(run) && run.provider_metadata?.latency_ms != null).sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 25),
    [runs],
  );
  const tokens = telemetry.inputTokens + telemetry.outputTokens;
  const [latencyFilters, setLatencyFilters] = useState<ChartFilters>(defaultChartFilters);
  const [tokenFilters, setTokenFilters] = useState<ChartFilters>(defaultChartFilters);
  const [tokenSeries, setTokenSeries] = useState<TokenSeries>("both");
  const models = useMemo(() => [...new Set(telemetry.timeline.map((point) => point.model))].sort(), [telemetry.timeline]);
  const latencyPoints = useMemo(() => applyChartFilters(telemetry.timeline, latencyFilters), [telemetry.timeline, latencyFilters]);
  const latencyP95 = useMemo(() => percentile(latencyPoints.map((point) => point.latency), 95), [latencyPoints]);
  const tokenPoints = useMemo(() => applyChartFilters(telemetry.timeline, tokenFilters), [telemetry.timeline, tokenFilters]);

  return (
    <>
      <PageHeader
        title="Monitoring"
        description="LLM generations, latency, token usage, and system health across recent live runs. Recorded demo runs are excluded."
        actions={<span className="text-xs text-muted-foreground">Last {telemetry.runCount} live runs · refreshes every 30 s</span>}
      >
        <div className="flex flex-wrap items-center gap-2">
          <Select value={conversation} onValueChange={(value) => setConversation(value ?? "all")}>
            <SelectTrigger size="sm" className="w-80" aria-label="Conversation"><SelectValue>{(value: string) => (value === "all" ? "All conversations" : <span className="font-mono text-xs">{value}</span>)}</SelectValue></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All conversations</SelectItem>
              {conversationOptions.map((option) => (
                <SelectItem key={option.id} value={option.id}>
                  <span className="font-mono text-xs">{option.id}</span>
                  <span className="text-xs text-muted-foreground">#{option.customer} · {option.count} runs</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {conversation !== "all" && <button type="button" className="inline-flex min-h-7 items-center rounded px-1.5 text-xs text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none" onClick={() => setConversation("all")}>Clear</button>}
        </div>
      </PageHeader>
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
              <Section title="Latency per run" description={latencyP95 === null ? undefined : `p95 ${formatDuration(latencyP95)}`} className="xl:col-span-2"
                actions={<ChartFilterControls idPrefix="Latency" filters={latencyFilters} models={models} onChange={setLatencyFilters} />}>
                <ChartContainer config={latencyConfig} className="aspect-auto h-56 w-full p-2">
                  <AreaChart data={latencyPoints} margin={{ left: 4, right: 12, top: 12 }}>
                    <defs>
                      <linearGradient id="latencyFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--color-latency)" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="var(--color-latency)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid vertical={false} strokeDasharray="3 3" />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={32} />
                    <YAxis tickLine={false} axisLine={false} width={44} tickFormatter={(value: number) => (value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}`)} />
                    <ChartTooltip isAnimationActive={false} cursor={{ stroke: "var(--border)" }} content={<ChartTooltipContent indicator="line" />} />
                    {latencyP95 !== null && <ReferenceLine y={latencyP95} stroke="var(--warning)" strokeDasharray="4 4" />}
                    <Area dataKey="latency" type="monotone" stroke="var(--color-latency)" strokeWidth={1.5} fill="url(#latencyFill)" isAnimationActive={false} activeDot={{ r: 3 }} />
                  </AreaChart>
                </ChartContainer>
              </Section>
              <Section title="Outcomes">
                <BarList items={telemetry.outcomes.map((item) => ({ label: item.label, value: item.count }))} format={(value) => String(value)} />
              </Section>
            </div>

            <div className="grid min-w-0 gap-4 xl:grid-cols-3">
              <Section title="Token usage per run" className="xl:col-span-2"
                actions={<>
                  <FilterSelect label="Token series" value={tokenSeries} onChange={setTokenSeries}
                    options={(Object.keys(seriesLabels) as TokenSeries[]).map((value) => ({ value, label: seriesLabels[value] }))} />
                  <ChartFilterControls idPrefix="Tokens" filters={tokenFilters} models={models} onChange={setTokenFilters} />
                </>}>
                <ChartContainer config={tokenConfig} className="aspect-auto h-56 w-full p-2">
                  <BarChart data={tokenPoints} margin={{ left: 4, right: 12, top: 12 }}>
                    <CartesianGrid vertical={false} strokeDasharray="3 3" />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={32} />
                    <YAxis tickLine={false} axisLine={false} width={44} />
                    <ChartTooltip isAnimationActive={false} cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent />} />
                    {tokenSeries !== "out" && <Bar dataKey="tokensIn" stackId="tokens" fill="var(--color-tokensIn)" radius={tokenSeries === "in" ? [2, 2, 0, 0] : [0, 0, 2, 2]} isAnimationActive={false} />}
                    {tokenSeries !== "in" && <Bar dataKey="tokensOut" stackId="tokens" fill="var(--color-tokensOut)" radius={[2, 2, 0, 0]} isAnimationActive={false} />}
                  </BarChart>
                </ChartContainer>
              </Section>
              <Section title="Where time goes" description="Average per pipeline stage">
                {telemetry.stages.length === 0 ? <p className="p-4 text-sm text-muted-foreground">No stage timings recorded.</p> : (
                  <BarList items={telemetry.stages.slice(0, 7).map((item) => ({ label: humanize(item.stage), value: item.avgMs, detail: `${item.samples}×` }))} format={(value) => formatDuration(value)} />
                )}
              </Section>
            </div>

            <Section title="Generations" description="Most recent LLM calls">
                <div className="overflow-x-auto">
                  <Table className="min-w-[760px]">
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        {["Time", "Conversation", "Intent", "Model", "Latency", "Input", "Output"].map((label) => <TableHead key={label} className="h-9 px-4 text-xs font-medium text-muted-foreground">{label}</TableHead>)}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {generations.map((run) => (
                        <TableRow key={run.run_id} className="cursor-pointer" onClick={() => onOpenRun(run.run_id)}>
                          <TableCell className="px-4 text-xs text-muted-foreground">{formatRelative(run.started_at)}</TableCell>
                          <TableCell className="max-w-[180px] px-4">
                            <button
                              type="button"
                              className="-my-1 block min-h-7 max-w-full truncate rounded font-mono text-xs leading-7 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none pointer-coarse:min-h-9 pointer-coarse:leading-9"
                              title={`Show only ${run.conversation_id}`}
                              onClick={(event) => { event.stopPropagation(); setConversation(run.conversation_id); }}
                            >
                              {run.conversation_id}
                            </button>
                          </TableCell>
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
            <div className="grid min-w-0 gap-4 lg:grid-cols-2">
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
          </>
        )}
      </div>
    </>
  );
}
