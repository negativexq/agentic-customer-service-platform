import { History, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/primitives/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { Input } from "@/components/primitives/input";
import { parseTimestamp } from "../runs/runPresentation";
import { studioApi, useConfigMutation } from "./queries";
import type { AgentConfigState } from "./types";

export function when(value: string | null): string {
  if (!value) return "—";
  return parseTimestamp(value).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Guidance and action changes are drafted first and go live for every new conversation only when published.
 * The bar says what is live, whether a draft differs, and keeps every published version restorable.
 */
export function DraftBar({ state }: { state: AgentConfigState | undefined }) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [note, setNote] = useState("");
  const publish = useConfigMutation((text: string) => studioApi.publish(text));
  const discard = useConfigMutation(() => studioApi.discardDraft());
  const restore = useConfigMutation((version: number) => studioApi.restore(version));
  if (!state) return null;
  const error = publish.error ?? discard.error ?? restore.error;
  const live = state.published_version ? `Version ${state.published_version} is live` : "Default behaviour is live";

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-muted/30 px-4 py-2.5 text-sm sm:px-6" data-testid="draft-bar">
      {state.draft ? (
        <>
          <span className="inline-flex items-center gap-2 font-medium text-foreground">
            <span className="size-1.5 rounded-full bg-warning" aria-hidden="true" />
            Unpublished draft
          </span>
          <span className="text-muted-foreground">{live}. Customers get the draft only after you publish it.</span>
          <span className="ml-auto flex items-center gap-2">
            <Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="What changed? (optional)" aria-label="Publish note" className="h-7 w-56 text-xs" maxLength={500} />
            <Button size="sm" variant="ghost" disabled={discard.isPending} onClick={() => discard.mutate(undefined)}>Discard</Button>
            <Button size="sm" disabled={publish.isPending} onClick={() => publish.mutate(note, { onSuccess: () => setNote("") })}>Publish</Button>
            <Button size="sm" variant="ghost" onClick={() => setHistoryOpen(true)} aria-label="Version history"><History /></Button>
          </span>
        </>
      ) : (
        <>
          <span className="inline-flex items-center gap-2 text-foreground">
            <span className="size-1.5 rounded-full bg-success" aria-hidden="true" />
            {live}
          </span>
          <span className="text-muted-foreground">Edits below start a draft.</span>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setHistoryOpen(true)}><History />History</Button>
        </>
      )}
      {error && <p role="alert" className="basis-full text-xs text-danger">{error.message}</p>}

      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent side="right" className="w-full gap-0 p-0 data-[side=right]:sm:max-w-md">
          <SheetHeader className="border-b border-border px-5 py-4 pr-12">
            <SheetTitle>Version history</SheetTitle>
            <SheetDescription>Restoring a version copies it into a draft; nothing changes for customers until you publish.</SheetDescription>
          </SheetHeader>
          <ol className="grid divide-y divide-border overflow-y-auto" aria-label="Configuration versions">
            {state.versions.length === 0 && <li className="px-5 py-6 text-sm text-muted-foreground">Nothing has been published yet. The agent runs with its default behaviour.</li>}
            {state.versions.map((version) => (
              <li key={version.version} className="grid gap-1 px-5 py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-medium text-foreground">Version {version.version}
                    <span className={`ml-2 text-xs font-normal ${version.status === "published" ? "text-success" : version.status === "draft" ? "text-warning" : "text-muted-foreground"}`}>
                      {version.status === "published" ? "Live" : version.status === "draft" ? "Draft" : "Archived"}
                    </span>
                  </span>
                  {version.status === "archived" && (
                    <Button size="xs" variant="outline" disabled={restore.isPending} onClick={() => restore.mutate(version.version, { onSuccess: () => setHistoryOpen(false) })}>
                      <RotateCcw />Restore
                    </Button>
                  )}
                </div>
                {version.note && <p className="text-sm text-foreground">{version.note}</p>}
                <p className="text-xs text-muted-foreground">
                  {version.published_at ? `Published ${when(version.published_at)} by ${version.published_by}` : `Edited ${when(version.updated_at)} by ${version.created_by}`}
                </p>
              </li>
            ))}
          </ol>
        </SheetContent>
      </Sheet>
    </div>
  );
}
