import { Lock, ShieldCheck } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { Skeleton } from "@/components/primitives/skeleton";
import { PageHeader } from "../../components/layout/PageHeader";
import { humanize } from "../runs/runPresentation";
import { DraftBar, when } from "./DraftBar";
import { useActions } from "./queries";
import type { ActionMode, ActionView, HandoffMode } from "./types";
import { useDraft } from "./useDraft";

const modeLabels: Record<ActionMode, string> = { enabled: "On", confirm: "Ask customer first", disabled: "Off" };

export function ModePicker({ action, value, onChange, disabled }: { action: ActionView; value: ActionMode; onChange: (mode: ActionMode) => void; disabled?: boolean }) {
  if (action.locked) {
    return <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><Lock className="size-3" aria-hidden="true" />Always on</span>;
  }
  return (
    <div role="radiogroup" aria-label={`${humanize(action.name)} mode`} className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5">
      {action.allowed_modes.map((mode) => {
        const active = mode === value;
        return (
          <button key={mode} type="button" role="radio" aria-checked={active} disabled={disabled} onClick={(event) => { event.stopPropagation(); if (!active) onChange(mode); }}
            className={`h-6 rounded-md px-2 text-xs whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none disabled:opacity-60 ${active ? mode === "disabled" ? "bg-background text-danger shadow-sm" : "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
            {modeLabels[mode]}
          </button>
        );
      })}
    </div>
  );
}

const handoffOptions: { value: HandoffMode; label: string; detail: string }[] = [
  { value: "offer_first", label: "Offer help first", detail: "The assistant asks what it is about and tries once. It connects a person straight away if the customer asks again, sounds upset, or the matter is sensitive, such as a disputed charge or account security." },
  { value: "immediate", label: "Connect right away", detail: "Every request for a person is handed to your team on the first message." },
];

function HandoffPicker({ value, onChange, disabled }: { value: HandoffMode; onChange: (mode: HandoffMode) => void; disabled?: boolean }) {
  return (
    <div role="radiogroup" aria-label="When a customer asks for a person" className="grid gap-2">
      {handoffOptions.map((option) => {
        const active = option.value === value;
        return (
          <button key={option.value} type="button" role="radio" aria-checked={active} disabled={disabled} onClick={() => { if (!active) onChange(option.value); }}
            className={`grid gap-0.5 rounded-lg border px-3 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none disabled:opacity-60 ${active ? "border-primary bg-primary/5" : "border-border hover:bg-accent"}`}>
            <span className="text-sm font-medium text-foreground">{option.label}</span>
            <span className="text-xs text-muted-foreground">{option.detail}</span>
          </button>
        );
      })}
    </div>
  );
}

function riskTone(level: number): string {
  return level >= 3 ? "text-danger" : level === 2 ? "text-warning" : "text-muted-foreground";
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="grid gap-2">
      <h3 className="text-xs font-medium text-muted-foreground">{label}</h3>
      {children}
    </section>
  );
}

export function ActionsPage({ initialAction, onOpenProcedure }: { initialAction: string | null; onOpenProcedure: (intent: string) => void }) {
  const actions = useActions();
  const draft = useDraft();
  const [selected, setSelected] = useState<string | null>(initialAction);
  const groups = useMemo(() => {
    const items = actions.data ?? [];
    return [
      { title: "Look up information", description: "Read-only. The customer must own what is looked up.", items: items.filter((item) => item.operation === "read") },
      { title: "Change things for the customer", description: "Writes. Policy, confirmation and revalidation still apply whatever you choose here.", items: items.filter((item) => item.operation === "write") },
    ];
  }, [actions.data]);
  const current = actions.data?.find((item) => item.name === selected) ?? null;
  const modeOf = (action: ActionView) => draft.working.actions[action.name] ?? "enabled";
  const setMode = (action: ActionView, mode: ActionMode) => draft.update((config) => {
    if (mode === "enabled") delete config.actions[action.name];
    else config.actions[action.name] = mode;
    return config;
  });

  return (
    <>
      <PageHeader title="Actions" description="What the AI assistant can do in your systems. You can switch an action off or make it ask the customer first; you can never give it more authority than its policy allows." />
      <DraftBar state={draft.state.data} />
      <div className="mx-auto grid w-full max-w-5xl gap-8 px-4 py-6 sm:px-6">
        {draft.save.error && <p role="alert" className="text-sm text-danger">{draft.save.error.message}</p>}
        {actions.isPending ? <div className="grid gap-2"><Skeleton className="h-14" /><Skeleton className="h-14" /><Skeleton className="h-14" /></div>
          : actions.isError ? <p className="text-sm text-muted-foreground">Actions could not be loaded.</p>
          : groups.map((group) => (
            <section key={group.title} className="grid gap-3">
              <div>
                <h2 className="text-sm font-medium text-foreground">{group.title}</h2>
                <p className="text-sm text-muted-foreground">{group.description}</p>
              </div>
              <ul className="grid divide-y divide-border rounded-xl border border-border bg-card">
                {group.items.map((action) => {
                  const mode = modeOf(action);
                  const changed = draft.state.data?.draft && mode !== action.published_mode;
                  return (
                    <li key={action.name}>
                      <div role="button" tabIndex={0} onClick={() => setSelected(action.name)} onKeyDown={(event) => { if (event.key === "Enter") setSelected(action.name); }}
                        className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 hover:bg-accent/40 focus-visible:bg-accent/40 focus-visible:outline-none" aria-label={`Open ${humanize(action.name)}`}>
                        <span className="grid min-w-0 flex-1 gap-0.5">
                          <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                            {humanize(action.name)}
                            {changed && <span className="text-xs font-normal text-warning">changed in draft</span>}
                          </span>
                          <span className="truncate text-xs text-muted-foreground">{action.description}</span>
                        </span>
                        <span className={`w-24 text-xs ${riskTone(action.risk_level)}`}>{action.risk_label}</span>
                        <span className="w-28 text-right text-xs tabular-nums text-muted-foreground">
                          {action.usage.calls === 0 ? "Not used in 30 days" : `${action.usage.calls} call${action.usage.calls === 1 ? "" : "s"} · 30 d`}
                        </span>
                        <ModePicker action={action} value={mode} disabled={draft.save.isPending} onChange={(next) => setMode(action, next)} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
      </div>

      <Sheet open={current !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <SheetContent side="right" className="w-full gap-0 p-0 data-[side=right]:sm:max-w-lg" data-testid="action-detail">
          {current && (
            <>
              <SheetHeader className="border-b border-border px-5 py-4 pr-12">
                <SheetTitle>{humanize(current.name)}</SheetTitle>
                <SheetDescription>{current.description}</SheetDescription>
              </SheetHeader>
              <div className="grid gap-6 overflow-y-auto p-5">
                <Field label="Mode">
                  <ModePicker action={current} value={modeOf(current)} disabled={draft.save.isPending} onChange={(next) => setMode(current, next)} />
                  <p className="text-xs text-muted-foreground">Live: {modeLabels[current.published_mode]}. Default policy: {humanize(current.default_policy)}.</p>
                </Field>
                {current.name === "escalate_to_human" && (
                  <Field label="When a customer asks for a person">
                    <HandoffPicker value={draft.working.handoff ?? "offer_first"} disabled={draft.save.isPending} onChange={(handoff) => draft.update((config) => ({ ...config, handoff }))} />
                  </Field>
                )}
                <Field label="Safeguards that always apply">
                  <ul className="grid gap-1.5">
                    {current.safeguards.map((item) => (
                      <li key={item} className="flex items-start gap-2 text-sm text-foreground"><ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden="true" />{item}</li>
                    ))}
                  </ul>
                </Field>
                <Field label="Information it needs">
                  <ul className="grid divide-y divide-border rounded-lg border border-border">
                    {current.inputs.map((input) => (
                      <li key={input.name} className="flex items-baseline justify-between gap-3 px-3 py-2 text-sm">
                        <span className="font-mono text-xs text-foreground">{input.name}{input.required ? "" : " (optional)"}</span>
                        <span className="text-xs text-muted-foreground">{input.description ?? input.type}</span>
                      </li>
                    ))}
                  </ul>
                </Field>
                <Field label="Used by procedures">
                  {current.procedures.length === 0 ? <p className="text-sm text-muted-foreground">No procedure uses this action.</p> : (
                    <div className="flex flex-wrap gap-1.5">
                      {current.procedures.map((intent) => (
                        <button key={intent} type="button" onClick={() => onOpenProcedure(intent)} className="rounded-md border border-border px-2 py-1 text-xs text-foreground hover:bg-accent">{humanize(intent)}</button>
                      ))}
                    </div>
                  )}
                </Field>
                <Field label="Last 30 days">
                  <dl className="grid grid-cols-3 divide-x divide-border rounded-lg border border-border text-sm">
                    {([["Calls", current.usage.calls], ["Succeeded", current.usage.succeeded], ["Failed", current.usage.failed]] as const).map(([label, value]) => (
                      <div key={label} className="grid gap-0.5 px-3 py-2"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="tabular-nums text-foreground">{value}</dd></div>
                    ))}
                  </dl>
                  <p className="text-xs text-muted-foreground">
                    {current.usage.denied} prevented by policy · {current.usage.awaiting_confirmation} waiting for the customer · last used {when(current.usage.last_used_at)}
                  </p>
                </Field>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
