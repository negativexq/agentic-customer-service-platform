import { Compass, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, XAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/primitives/chart";
import { Badge } from "@/components/primitives/badge";
import { Button } from "@/components/primitives/button";
import { Input } from "@/components/primitives/input";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { Skeleton } from "@/components/primitives/skeleton";
import { Textarea } from "@/components/primitives/textarea";
import { ApiError } from "../../api/client";
import { PageHeader } from "../../components/layout/PageHeader";
import type { GuidanceSeed } from "../studio/GuidancePage";
import { formatRelative } from "../runs/runPresentation";
import { coachingSeed } from "./coaching";
import { improveApi, useQuality, useQualityMutation } from "./queries";
import { EmptyNote, percent, Section, WindowPicker } from "./shared";
import type { Flag, Monitor, MonitorInput, MonitorPreview, MonitorSender, ReviewStatus } from "./types";

const severityTone: Record<string, string> = { high: "bg-danger", medium: "bg-warning", low: "bg-muted-foreground/60" };
const statusLabel: Record<ReviewStatus, string> = { open: "Needs review", reviewed: "Reviewed", dismissed: "Dismissed", coached: "Coached" };
const senders: { value: MonitorSender; label: string }[] = [
  { value: "customer", label: "Customer" }, { value: "ai", label: "AI" }, { value: "operator", label: "Operator" }, { value: "any", label: "Anyone" },
];

export const customMonitorId = (key: string): number | null => (key.startsWith("custom:") ? Number(key.slice(7)) || null : null);

function MonitorRow({ monitor, active, onSelect, onEdit }: { monitor: Monitor; active: boolean; onSelect: () => void; onEdit?: () => void }) {
  return (
    <li className="flex items-center gap-1 pr-2">
      <button type="button" onClick={onSelect} aria-pressed={active}
        className={`flex min-w-0 flex-1 items-start gap-2.5 rounded-md px-3 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${active ? "bg-muted" : "hover:bg-muted/60"}`}>
        <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${monitor.enabled ? severityTone[monitor.severity] : "bg-border"}`} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className={`truncate text-sm ${monitor.enabled ? "text-foreground" : "text-muted-foreground line-through"}`}>{monitor.name}</span>
            <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground" aria-label={`${monitor.open_flags} open`}>{monitor.open_flags}</span>
          </span>
          <span className="line-clamp-2 text-xs text-muted-foreground">{monitor.description || monitor.phrases.join(", ")}</span>
        </span>
      </button>
      {onEdit && <Button size="xs" variant="ghost" onClick={onEdit} aria-label={`Edit ${monitor.name}`}>Edit</Button>}
    </li>
  );
}

export function FlagRow({ flag, busy, onReview, onCoach, onOpenConversation, onOpenRun }: {
  flag: Flag; busy: boolean; onReview: (status: ReviewStatus) => void; onCoach: () => void; onOpenConversation: () => void; onOpenRun?: () => void;
}) {
  return (
    <li className="grid gap-2 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-sm">
          <span className={`size-1.5 shrink-0 rounded-full ${severityTone[flag.severity] ?? "bg-border"}`} aria-hidden />
          <span className="font-medium text-foreground">{flag.monitor_name}</span>
          <span className="truncate text-muted-foreground">{flag.customer_name} · {flag.conversation_id}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {flag.status !== "open" && <Badge variant="secondary">{statusLabel[flag.status]}</Badge>}
          <span>{formatRelative(flag.at)}</span>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">{flag.detail}</p>
      {flag.excerpt && <blockquote className="border-l border-border pl-3 text-sm text-foreground">{flag.excerpt}</blockquote>}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="xs" variant="outline" onClick={onOpenConversation}>Open conversation</Button>
        {onOpenRun && <Button size="xs" variant="ghost" onClick={onOpenRun}>Inspect run</Button>}
        <span className="mx-1 h-4 w-px bg-border" aria-hidden />
        {flag.status === "open" ? (
          <>
            <Button size="xs" variant="ghost" disabled={busy} onClick={() => onReview("reviewed")}>Mark reviewed</Button>
            <Button size="xs" variant="ghost" disabled={busy} onClick={() => onReview("dismissed")}>Dismiss</Button>
            <Button size="xs" variant="ghost" disabled={busy} onClick={onCoach}><Compass data-icon="inline-start" aria-hidden />Coach</Button>
          </>
        ) : <Button size="xs" variant="ghost" disabled={busy} onClick={() => onReview("open")}>Reopen</Button>}
      </div>
    </li>
  );
}

const flaggedConfig = { flagged: { label: "Flagged", color: "var(--chart-1)" } } satisfies ChartConfig;

export function flaggedPerDay(flags: Flag[], days: number, now = new Date()) {
  const buckets = new Map<string, number>();
  for (let offset = days; offset >= 0; offset -= 1) {
    const day = new Date(now.getTime() - offset * 86_400_000);
    buckets.set(day.toISOString().slice(0, 10), 0);
  }
  flags.forEach((flag) => { const key = flag.at.slice(0, 10); if (buckets.has(key)) buckets.set(key, buckets.get(key)! + 1); });
  return [...buckets].map(([day, flagged]) => ({ day, flagged, label: new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) }));
}

export function MonitorOverview({ flags, conversations, days }: { flags: Flag[]; conversations: number; days: number }) {
  const flaggedConversations = new Set(flags.map((flag) => flag.conversation_pk)).size;
  const open = flags.filter((flag) => flag.status === "open").length;
  return (
    <div className="grid gap-0 border-b border-border sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
      <dl className="grid grid-cols-3 gap-3 px-4 py-3 sm:grid-cols-1 sm:border-r sm:border-border">
        <div><dt className="text-xs text-muted-foreground">Flagged rate</dt><dd className="font-mono text-xl tabular-nums text-foreground">{conversations ? percent(flaggedConversations / conversations) : "—"}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Conversations flagged</dt><dd className="font-mono text-sm tabular-nums text-foreground">{flaggedConversations} <span className="text-muted-foreground">of {conversations}</span></dd></div>
        <div><dt className="text-xs text-muted-foreground">Needs review</dt><dd className="font-mono text-sm tabular-nums text-foreground">{open}</dd></div>
      </dl>
      <ChartContainer config={flaggedConfig} className="aspect-auto h-36 w-full p-2" aria-label={`Flags per day over ${days} days`}>
        <BarChart data={flaggedPerDay(flags, days)} margin={{ left: 8, right: 8, top: 8 }}>
          <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={32} />
          <ChartTooltip isAnimationActive={false} cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent />} />
          <Bar dataKey="flagged" fill="var(--color-flagged)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
    </div>
  );
}

export function PreviewResult({ preview }: { preview: MonitorPreview }) {
  return (
    <div className="grid gap-2 rounded-lg border border-border bg-muted/30 p-3" aria-live="polite">
      <p className="text-sm text-foreground">
        Would flag <span className="font-mono tabular-nums">{preview.matched}</span> of <span className="font-mono tabular-nums">{preview.conversations}</span> conversations
        {preview.conversations ? ` (${percent(preview.matched / preview.conversations)})` : ""} from the last {preview.window_days} days.
      </p>
      {preview.examples.length > 0 && (
        <ul className="grid gap-1.5">
          {preview.examples.map((example) => (
            <li key={example.conversation_pk} className="grid gap-0.5 text-xs">
              <span className="text-muted-foreground">{example.customer_name} · {formatRelative(example.at)}</span>
              {example.excerpt && <span className="line-clamp-2 border-l border-border pl-2 text-foreground">{example.excerpt}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const blankMonitor: MonitorInput = { name: "", description: "", sender_type: "customer", phrases: [], severity: "medium", enabled: true };

function MonitorSheet({ editing, days, onClose }: { editing: { id: number | null; input: MonitorInput } | null; days: number; onClose: () => void }) {
  const [form, setForm] = useState<MonitorInput>(editing?.input ?? blankMonitor);
  const [phrases, setPhrases] = useState((editing?.input.phrases ?? []).join("\n"));
  const save = useQualityMutation((input: MonitorInput) => (editing?.id ? improveApi.updateMonitor(editing.id, input) : improveApi.createMonitor(input)));
  const remove = useQualityMutation((id: number) => improveApi.deleteMonitor(id));
  const [preview, setPreview] = useState<{ key: string; result: MonitorPreview } | null>(null);
  const [testing, setTesting] = useState(false);
  const list = phrases.split("\n").map((value) => value.trim()).filter(Boolean);
  const error = save.error ?? remove.error;
  const previewKey = `${form.sender_type}|${list.join("|")}`;
  const test = () => {
    setTesting(true);
    improveApi.previewMonitor({ phrases: list, sender_type: form.sender_type, days })
      .then((result) => setPreview({ key: previewKey, result }), () => setPreview(null))
      .finally(() => setTesting(false));
  };
  const submit = () => save.mutate({ ...form, phrases: list }, { onSuccess: onClose });

  return (
    <Sheet open={editing !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent className="w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{editing?.id ? "Edit monitor" : "New monitor"}</SheetTitle>
          <SheetDescription>Flags a conversation when a message contains one of the phrases. Matching ignores case.</SheetDescription>
        </SheetHeader>
        <form className="grid gap-4 overflow-y-auto px-4" onSubmit={(event) => { event.preventDefault(); submit(); }}>
          <label className="grid gap-1.5 text-sm">Name
            <Input value={form.name} maxLength={120} required onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Mentions a chargeback" />
          </label>
          <label className="grid gap-1.5 text-sm">What it catches <span className="text-xs text-muted-foreground">Optional</span>
            <Input value={form.description} maxLength={500} onChange={(event) => setForm({ ...form, description: event.target.value })} />
          </label>
          <label className="grid gap-1.5 text-sm">Phrases, one per line
            <Textarea rows={5} value={phrases} onChange={(event) => setPhrases(event.target.value)} placeholder={"chargeback\ndispute with my bank"} />
          </label>
          <fieldset className="grid gap-1.5 text-sm">
            <legend className="mb-1.5">Whose messages</legend>
            <div className="flex flex-wrap gap-1.5">
              {senders.map((sender) => (
                <button key={sender.value} type="button" aria-pressed={form.sender_type === sender.value} onClick={() => setForm({ ...form, sender_type: sender.value })}
                  className={`h-7 rounded-md border px-2.5 text-xs focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${form.sender_type === sender.value ? "border-foreground/20 bg-muted text-foreground" : "border-border text-muted-foreground"}`}>{sender.label}</button>
              ))}
            </div>
          </fieldset>
          <fieldset className="grid gap-1.5 text-sm">
            <legend className="mb-1.5">Severity</legend>
            <div className="flex gap-1.5">
              {(["low", "medium", "high"] as const).map((severity) => (
                <button key={severity} type="button" aria-pressed={form.severity === severity} onClick={() => setForm({ ...form, severity })}
                  className={`flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs capitalize focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${form.severity === severity ? "border-foreground/20 bg-muted text-foreground" : "border-border text-muted-foreground"}`}>
                  <span className={`size-1.5 rounded-full ${severityTone[severity]}`} aria-hidden />{severity}
                </button>
              ))}
            </div>
          </fieldset>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} className="size-4 accent-primary" />
            Monitor is on
          </label>
          <div className="grid gap-2">
            <Button type="button" variant="outline" size="sm" className="w-fit" disabled={list.length === 0 || testing} onClick={test}>
              {testing ? "Testing…" : `Test on the last ${days} days`}
            </Button>
            {preview && preview.key === previewKey && <PreviewResult preview={preview.result} />}
          </div>
          {error && <p role="alert" className="text-sm text-danger">{error instanceof ApiError ? error.message : "The monitor could not be saved."}</p>}
        </form>
        <SheetFooter className="flex-row justify-between">
          {editing?.id ? (
            <Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate(editing.id!, { onSuccess: onClose })}><Trash2 data-icon="inline-start" aria-hidden />Delete</Button>
          ) : <span />}
          <Button disabled={!form.name.trim() || list.length === 0 || save.isPending} onClick={submit}>{editing?.id ? "Save monitor" : "Create monitor"}</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

export function QualityPage({ onCoach, onOpenConversation, onOpenRun }: { onCoach: (seed: GuidanceSeed) => void; onOpenConversation: (pk: number) => void; onOpenRun: (runId: string) => void }) {
  const [days, setDays] = useState(30);
  const [monitorKey, setMonitorKey] = useState<string | null>(null);
  const [status, setStatus] = useState<"open" | "all">("open");
  const [editing, setEditing] = useState<{ id: number | null; input: MonitorInput } | null>(null);
  const query = useQuality(days);
  const review = useQualityMutation((input: { flag: Flag; status: ReviewStatus }) => improveApi.review(input.flag.monitor_key, input.flag.conversation_pk, input.status));
  const flags = (query.data?.flags ?? []).filter((flag) => (!monitorKey || flag.monitor_key === monitorKey) && (status === "all" || flag.status === "open"));
  const selected = query.data?.monitors.find((monitor) => monitor.key === monitorKey);

  const coach = (flag: Flag) => {
    review.mutate({ flag, status: "coached" });
    onCoach(coachingSeed({ title: `${flag.monitor_name}: ${flag.customer_name}`, context: flag.excerpt ? `a customer writes “${flag.excerpt}”` : flag.detail, intent: flag.intent, runId: flag.run_id }));
  };

  return (
    <div className="min-w-0">
      <PageHeader title="Quality" description="Monitors read every conversation and flag the ones worth a second look. Review them, dismiss them, or turn them into guidance."
        actions={<>
          <WindowPicker value={days} onChange={setDays} />
          <Button size="sm" onClick={() => setEditing({ id: null, input: blankMonitor })}><Plus data-icon="inline-start" aria-hidden />New monitor</Button>
        </>} />
      <div className="grid gap-4 p-4 sm:p-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Section title="Monitors" className="order-2 lg:order-1">
          {!query.data ? <Skeleton className="m-4 h-40" /> : (
            <ul className="grid gap-0.5 p-2">
              <MonitorRow monitor={{ key: "", name: "All monitors", description: "Every flag in this window", severity: "low", built_in: true, enabled: true, sender_type: null, phrases: [], open_flags: query.data.monitors.reduce((sum, item) => sum + item.open_flags, 0), total_flags: 0 }}
                active={monitorKey === null} onSelect={() => setMonitorKey(null)} />
              {query.data.monitors.map((monitor) => {
                const id = customMonitorId(monitor.key);
                return (
                  <MonitorRow key={monitor.key} monitor={monitor} active={monitorKey === monitor.key} onSelect={() => setMonitorKey(monitor.key)}
                    onEdit={id ? () => setEditing({ id, input: { name: monitor.name, description: monitor.description, sender_type: monitor.sender_type ?? "customer", phrases: monitor.phrases, severity: monitor.severity, enabled: monitor.enabled } }) : undefined} />
                );
              })}
            </ul>
          )}
        </Section>
        <Section title={selected?.name ?? "Flagged conversations"} description={selected?.description} className="order-1 lg:order-2"
          actions={
            <div role="radiogroup" aria-label="Review status" className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5">
              {(["open", "all"] as const).map((value) => (
                <button key={value} type="button" role="radio" aria-checked={status === value} onClick={() => setStatus(value)}
                  className={`h-6 rounded-md px-2 text-xs focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${status === value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                  {value === "open" ? "Needs review" : "All"}
                </button>
              ))}
            </div>
          }>
          {query.isError && <p role="alert" className="px-4 py-3 text-sm text-danger">Quality data is unavailable.</p>}
          {query.data && <MonitorOverview flags={(query.data.flags).filter((flag) => !monitorKey || flag.monitor_key === monitorKey)} conversations={query.data.conversations} days={days} />}
          {!query.data ? <Skeleton className="m-4 h-40" /> : flags.length === 0 ? <EmptyNote>{status === "open" ? "Nothing left to review. Switch to All to see past decisions." : "No conversations were flagged in this window."}</EmptyNote> : (
            <ul className="divide-y divide-border">
              {flags.map((flag) => (
                <FlagRow key={`${flag.monitor_key}:${flag.conversation_pk}`} flag={flag} busy={review.isPending}
                  onReview={(next) => review.mutate({ flag, status: next })}
                  onCoach={() => coach(flag)}
                  onOpenConversation={() => onOpenConversation(flag.conversation_pk)}
                  onOpenRun={flag.run_id ? () => onOpenRun(flag.run_id!) : undefined} />
              ))}
            </ul>
          )}
        </Section>
      </div>
      <MonitorSheet key={editing ? `${editing.id ?? "new"}` : "closed"} editing={editing} days={days} onClose={() => setEditing(null)} />
    </div>
  );
}
