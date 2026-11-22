import { GraduationCap, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/primitives/button";
import { Input } from "@/components/primitives/input";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { Skeleton } from "@/components/primitives/skeleton";
import { Textarea } from "@/components/primitives/textarea";
import { PageHeader } from "../../components/layout/PageHeader";
import { humanize } from "../runs/runPresentation";
import { DraftBar } from "./DraftBar";
import { intents, type GuidanceRule, type Intent } from "./types";
import { useDraft } from "./useDraft";

export type GuidanceSeed = Pick<GuidanceRule, "title" | "instruction" | "intents"> & { source_run_id?: string | null };

function newRule(seed?: GuidanceSeed | null): GuidanceRule {
  return {
    id: `g-${crypto.randomUUID().slice(0, 8)}`,
    title: seed?.title ?? "",
    instruction: seed?.instruction ?? "",
    intents: seed?.intents ?? [],
    enabled: true,
    source: seed ? "coaching" : "manual",
    source_run_id: seed?.source_run_id ?? null,
  };
}

const scopeableIntents = intents.filter((intent) => intent !== "unknown");

function RuleEditor({ rule, isNew, saving, onSave, onClose }: { rule: GuidanceRule; isNew: boolean; saving: boolean; onSave: (rule: GuidanceRule) => void; onClose: () => void }) {
  const [value, setValue] = useState(rule);
  const toggleIntent = (intent: Intent) => setValue((current) => ({
    ...current,
    intents: current.intents.includes(intent) ? current.intents.filter((item) => item !== intent) : [...current.intents, intent],
  }));
  const valid = value.title.trim().length > 0 && value.instruction.trim().length > 0;
  return (
    <form className="flex min-h-0 flex-1 flex-col" onSubmit={(event) => { event.preventDefault(); if (valid) onSave({ ...value, title: value.title.trim(), instruction: value.instruction.trim() }); }}>
      <div className="grid min-h-0 flex-1 content-start gap-5 overflow-y-auto p-5">
        <label className="grid gap-1.5 text-sm">
          <span className="font-medium text-foreground">Name</span>
          <Input value={value.title} maxLength={120} onChange={(event) => setValue({ ...value, title: event.target.value })} placeholder="Chargebacks go to a person" autoFocus />
        </label>
        <label className="grid gap-1.5 text-sm">
          <span className="font-medium text-foreground">Instruction</span>
          <Textarea value={value.instruction} maxLength={600} rows={5} onChange={(event) => setValue({ ...value, instruction: event.target.value })}
            placeholder="When a customer mentions a chargeback or a dispute with their bank, treat it as a request for a person." />
          <span className="text-xs text-muted-foreground">Describe how to read a request, in plain language. {600 - value.instruction.length} characters left.</span>
        </label>
        <fieldset className="grid gap-2">
          <legend className="mb-1 text-sm font-medium text-foreground">Applies to</legend>
          <p className="text-xs text-muted-foreground">Leave empty to apply to every request.</p>
          <div className="flex flex-wrap gap-1.5">
            {scopeableIntents.map((intent) => {
              const on = value.intents.includes(intent);
              return (
                <button key={intent} type="button" aria-pressed={on} onClick={() => toggleIntent(intent)}
                  className={`rounded-md border px-2 py-1 text-xs transition-colors ${on ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground hover:text-foreground"}`}>
                  {humanize(intent)}
                </button>
              );
            })}
          </div>
        </fieldset>
      </div>
      <SheetFooter className="flex-row justify-end gap-2 border-t border-border px-5 py-3">
        <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit" disabled={!valid || saving}>{isNew ? "Add to draft" : "Save to draft"}</Button>
      </SheetFooter>
    </form>
  );
}

export function GuidancePage({ seed, onSeedUsed }: { seed?: GuidanceSeed | null; onSeedUsed?: () => void }) {
  const draft = useDraft();
  const [editing, setEditing] = useState<{ rule: GuidanceRule; isNew: boolean } | null>(() => (seed ? { rule: newRule(seed), isNew: true } : null));
  const rules = draft.working.guidance;
  const publishedIds = new Set((draft.state.data?.published.guidance ?? []).map((rule) => JSON.stringify(rule)));

  const close = () => { setEditing(null); onSeedUsed?.(); };
  const save = (rule: GuidanceRule) => draft.update((config) => {
    const index = config.guidance.findIndex((item) => item.id === rule.id);
    if (index >= 0) config.guidance[index] = rule; else config.guidance.push(rule);
    return config;
  });

  return (
    <>
      <PageHeader title="Guidance" description="Rules in plain language that shape how the AI assistant reads requests. Guidance never grants authority: identity, policy and confirmation checks always win."
        actions={<Button size="sm" onClick={() => setEditing({ rule: newRule(), isNew: true })} disabled={rules.length >= 50}><Plus />New rule</Button>} />
      <DraftBar state={draft.state.data} />
      <div className="mx-auto grid w-full max-w-4xl gap-4 px-4 py-6 sm:px-6">
        {draft.save.error && <p role="alert" className="text-sm text-danger">{draft.save.error.message}</p>}
        {draft.state.isPending ? <div className="grid gap-2"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>
          : rules.length === 0 ? (
            <div className="grid justify-items-center gap-2 rounded-xl border border-dashed border-border px-6 py-12 text-center">
              <p className="text-sm font-medium text-foreground">No guidance yet</p>
              <p className="max-w-md text-sm text-muted-foreground">Add a rule when the assistant keeps reading a kind of request the wrong way, such as treating chargebacks as refund requests.</p>
              <Button size="sm" variant="outline" className="mt-2" onClick={() => setEditing({ rule: newRule(), isNew: true })}><Plus />New rule</Button>
            </div>
          ) : (
            <ul className="grid divide-y divide-border rounded-xl border border-border bg-card" aria-label="Guidance rules">
              {rules.map((rule) => (
                <li key={rule.id} className="grid gap-2 px-4 py-3">
                  <div className="flex items-start gap-3">
                    <div className="grid min-w-0 flex-1 gap-1">
                      <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                        <span className={rule.enabled ? "" : "text-muted-foreground line-through"}>{rule.title}</span>
                        {rule.source === "coaching" && <span className="inline-flex items-center gap-1 text-xs font-normal text-muted-foreground"><GraduationCap className="size-3" aria-hidden="true" />From coaching</span>}
                        {draft.state.data?.draft && !publishedIds.has(JSON.stringify(rule)) && <span className="text-xs font-normal text-warning">changed in draft</span>}
                      </span>
                      <p className="text-sm whitespace-pre-line text-muted-foreground">{rule.instruction}</p>
                      <p className="text-xs text-muted-foreground">{rule.intents.length === 0 ? "Every request" : rule.intents.map(humanize).join(", ")}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <button type="button" role="switch" aria-checked={rule.enabled} aria-label={`${rule.title} enabled`} disabled={draft.save.isPending}
                        onClick={() => save({ ...rule, enabled: !rule.enabled })}
                        className={`relative mr-1 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${rule.enabled ? "bg-primary" : "bg-muted-foreground/30"}`}>
                        <span className={`inline-block size-4 rounded-full bg-background shadow-sm transition-transform ${rule.enabled ? "translate-x-4.5" : "translate-x-0.5"}`} />
                      </button>
                      <Button size="icon-sm" variant="ghost" aria-label={`Edit ${rule.title}`} onClick={() => setEditing({ rule, isNew: false })}><Pencil /></Button>
                      <Button size="icon-sm" variant="ghost" aria-label={`Delete ${rule.title}`} disabled={draft.save.isPending}
                        onClick={() => draft.update((config) => ({ ...config, guidance: config.guidance.filter((item) => item.id !== rule.id) }))}><Trash2 /></Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        <p className="text-xs text-muted-foreground">Enabled rules are sent to the model with every matching request, after the system rules. Customer replies still come from approved templates and knowledge.</p>
      </div>

      <Sheet open={editing !== null} onOpenChange={(open) => { if (!open) close(); }}>
        <SheetContent side="right" className="w-full gap-0 p-0 data-[side=right]:sm:max-w-lg">
          <SheetHeader className="border-b border-border px-5 py-4 pr-12">
            <SheetTitle>{editing?.isNew ? "New guidance rule" : "Edit guidance rule"}</SheetTitle>
            <SheetDescription>Saved to the draft. Customers are affected only after you publish.</SheetDescription>
          </SheetHeader>
          {editing && <RuleEditor key={editing.rule.id} rule={editing.rule} isNew={editing.isNew} saving={draft.save.isPending}
            onClose={close} onSave={(rule) => { save(rule); close(); }} />}
        </SheetContent>
      </Sheet>
    </>
  );
}
