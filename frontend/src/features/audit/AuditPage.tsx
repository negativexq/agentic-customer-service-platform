import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, History, Search, ShieldAlert, UserRound } from "lucide-react";
import { Skeleton } from "@/components/primitives/skeleton";
import { Input } from "@/components/primitives/input";
import { Button } from "@/components/primitives/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { UI_LOCALE } from "@/lib/utils";
import { AgentOrb } from "../../components/AgentOrb";
import { PageHeader } from "../../components/layout/PageHeader";
import { initials } from "../../auth/demoSession";
import { EmptyNote, Section, WindowPicker } from "../improve/shared";
import { useAuditLog, useConversationTrail } from "./queries";
import { actorName, clock, eventTone, groupByDay, handlingSummary, kindLabels, kindOrder, serverSearch, stamp, type You } from "./presentation";
import type { AuditActor, AuditEvent, AuditKind } from "./types";

const toneDot = { neutral: "bg-muted-foreground/40", info: "bg-info", success: "bg-success", warning: "bg-warning", danger: "bg-danger" } as const;

function ActorMark({ actor, you, className = "size-7" }: { actor: AuditActor; you: You; className?: string }) {
  const name = actorName(actor, you);
  if (actor.type === "ai") return <AgentOrb className={className} />;
  if (actor.type === "operator") {
    return <span className={`flex shrink-0 items-center justify-center rounded-lg bg-[linear-gradient(135deg,#4c82ff,#a78bfa_60%,#f472b6)] text-[10px] font-semibold text-white ${className}`} aria-hidden="true">{initials(name)}</span>;
  }
  if (actor.type === "customer") return <span className={`flex shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-medium text-muted-foreground ${className}`} aria-hidden="true">{initials(actor.name) || <UserRound className="size-3.5" />}</span>;
  return <span className={`flex shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground ${className}`} aria-hidden="true"><ShieldAlert className="size-3.5" /></span>;
}

const sentence = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

function EventLine({ event, you }: { event: AuditEvent; you: You }) {
  return (
    <p className="text-sm leading-5 text-foreground/85">
      <span className="font-medium text-foreground">{actorName(event.actor, you)}</span> {sentence(event.summary)}
    </p>
  );
}

function ConversationTrail({ event, you, onClose, onOpenConversation }: { event: AuditEvent | null; you: You; onClose: () => void; onOpenConversation: (pk: number) => void }) {
  const query = useConversationTrail(event?.conversation_pk ?? null);
  const trail = useMemo(() => [...(query.data?.events ?? [])].reverse(), [query.data]);
  const summary = handlingSummary(trail, you);
  return (
    <Sheet open={event !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent side="right" className="w-full gap-0 p-0 data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b border-border px-5 py-4 pr-12">
          <SheetTitle>Conversation trail</SheetTitle>
          <SheetDescription>{event ? <>{event.customer_name} · <span className="font-mono text-xs">{event.conversation_id}</span></> : null}</SheetDescription>
        </SheetHeader>
        <div className="grid content-start gap-5 overflow-y-auto px-5 py-4">
          {query.isError && <p role="alert" className="text-sm text-danger">This conversation's trail could not be loaded.</p>}
          {!query.data && !query.isError && <Skeleton className="h-48 w-full rounded-xl" />}
          {summary.length > 0 && (
            <ul className="grid gap-1.5 rounded-xl border border-border bg-muted/40 px-3.5 py-3 text-[13px] text-foreground/85">
              {summary.map((line) => <li key={line} className="flex gap-2"><span className="mt-[7px] size-1 shrink-0 rounded-full bg-muted-foreground/60" aria-hidden="true" />{line}</li>)}
            </ul>
          )}
          {trail.length > 0 && (
            <ol className="relative grid gap-4" aria-label="Every step, oldest first">
              <span className="absolute top-3 bottom-3 left-[13.5px] w-px bg-border" aria-hidden="true" />
              {trail.map((item) => (
                <li key={item.id} className="relative grid grid-cols-[1.75rem_minmax(0,1fr)] gap-3">
                  <span className="relative flex size-7 items-center justify-center rounded-lg bg-background"><ActorMark actor={item.actor} you={you} /></span>
                  <div className="grid min-w-0 gap-0.5 pt-0.5">
                    <EventLine event={item} you={you} />
                    {item.detail && <p className="text-xs leading-5 text-muted-foreground">{item.detail}</p>}
                    <p className="flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-muted-foreground">
                      <span className={`size-1.5 rounded-full ${toneDot[eventTone(item)]}`} aria-hidden="true" />
                      <time dateTime={item.at}>{stamp(item.at)}</time>
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
        {event && (
          <div className="mt-auto flex justify-end border-t border-border px-5 py-3">
            <Button variant="outline" onClick={() => onOpenConversation(event.conversation_pk)}>Open conversation<ArrowUpRight /></Button>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export function AuditPage({ you, onOpenConversation, onOpenRun }: { you: You; onOpenConversation: (pk: number) => void; onOpenRun: (runId: string) => void }) {
  const [days, setDays] = useState(7);
  const [kind, setKind] = useState<AuditKind | null>(null);
  const [search, setSearch] = useState("");
  const [trailOf, setTrailOf] = useState<AuditEvent | null>(null);
  const [settled, setSettled] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const term = serverSearch(settled, you);
  const all = useAuditLog(days, null, term);
  const filtered = useAuditLog(days, kind, term);
  const shown = kind ? filtered : all;
  const data = shown.data;
  const counts = all.data?.counts ?? {};
  const events = useMemo(() => data?.events ?? [], [data]);
  const stale = shown.isPlaceholderData || search.trim() !== settled;
  const days_ = useMemo(() => groupByDay(events), [events]);
  const failed = all.isError || (kind !== null && filtered.isError);

  return (
    <div className="min-w-0">
      <PageHeader title="Audit log" description="Every step on every conversation, with its time and who did it: the assistant's decisions and refusals, handoffs to the team, takeovers, replies and closes." actions={<WindowPicker value={days} onChange={setDays} />} />
      <div className="grid gap-4 p-4 sm:p-6">
        {failed && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">The audit log is unavailable.</p>}
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="What to show">
          {[null, ...kindOrder].map((value) => {
            const count = value ? counts[value] ?? 0 : all.data?.total ?? 0;
            return (
              <button key={value ?? "all"} type="button" role="radio" aria-checked={kind === value} onClick={() => setKind(value)}
                className={`inline-flex h-8 items-center gap-2 rounded-lg border px-3 text-[13px] transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${kind === value ? "border-foreground/20 bg-muted text-foreground" : "border-border text-muted-foreground hover:text-foreground"}`}>
                {value ? kindLabels[value] : "Everything"}
                <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{all.data ? count.toLocaleString(UI_LOCALE) : "–"}</span>
              </button>
            );
          })}
        </div>
        <Section title="Activity" description={data ? `${data.total.toLocaleString(UI_LOCALE)} ${data.total === 1 ? "entry" : "entries"}${settled ? " match" : ""}, newest first${data.total > data.events.length ? `; showing the newest ${data.events.length}` : ""}` : undefined}
          actions={
            <label className="relative block w-full sm:w-72">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Customer, person, conversation or run" aria-label="Search the audit log" className="h-8 pl-8" />
            </label>
          }>
          {!data ? <div className="grid gap-2 p-4">{[0, 1, 2, 3].map((index) => <Skeleton key={index} className="h-12 w-full" />)}</div>
            : events.length === 0 ? <EmptyNote>{settled ? "Nothing matches that search." : `Nothing was recorded in the last ${days} days.`}</EmptyNote>
            : (
              <div aria-busy={stale} className={`transition-opacity duration-150 ${stale ? "opacity-60" : ""}`}>
                {days_.map((group) => (
                  <section key={group.label} aria-label={group.label}>
                    <h3 className="sticky top-0 z-[1] border-b border-border bg-card/95 px-4 py-2 text-xs font-medium text-muted-foreground backdrop-blur-sm">{group.label}<span className="ml-2 font-mono text-[11px] tabular-nums">{group.events.length}</span></h3>
                    <ol className="divide-y divide-border">
                      {group.events.map((event) => (
                        <li key={event.id} className="grid grid-cols-[4.5rem_1.75rem_minmax(0,1fr)] items-start gap-x-3 px-4 py-3 sm:grid-cols-[4.5rem_1.75rem_minmax(0,1fr)_auto]">
                          <time dateTime={event.at} title={stamp(event.at)} className="pt-1 font-mono text-xs tabular-nums text-muted-foreground">{clock(event.at)}</time>
                          <ActorMark actor={event.actor} you={you} />
                          <div className="grid min-w-0 gap-0.5">
                            <EventLine event={event} you={you} />
                            {event.detail && <p className="text-xs leading-5 text-muted-foreground">{event.detail}</p>}
                            <p className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                              <span className={`size-1.5 shrink-0 rounded-full ${toneDot[eventTone(event)]}`} aria-hidden="true" />
                              <span className="truncate">{event.customer_name} · {kindLabels[event.kind]}</span>
                            </p>
                          </div>
                          <div className="col-start-3 mt-2 flex items-center gap-1 sm:col-start-auto sm:mt-0 sm:justify-end">
                            <Button variant="ghost" size="sm" onClick={() => setTrailOf(event)} aria-label={`Show the full trail of ${event.customer_name}'s conversation`}><History />Trail</Button>
                            {event.run_id && <Button variant="ghost" size="sm" className="font-mono text-xs" onClick={() => onOpenRun(event.run_id!)} aria-label="Open the run">{event.run_id.slice(0, 8)}</Button>}
                          </div>
                        </li>
                      ))}
                    </ol>
                  </section>
                ))}
              </div>
            )}
        </Section>
      </div>
      <ConversationTrail event={trailOf} you={you} onClose={() => setTrailOf(null)} onOpenConversation={(pk) => { setTrailOf(null); onOpenConversation(pk); }} />
    </div>
  );
}
