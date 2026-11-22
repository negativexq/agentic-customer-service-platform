import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/primitives/button";
import { Input } from "@/components/primitives/input";
import { Skeleton } from "@/components/primitives/skeleton";
import { Textarea } from "@/components/primitives/textarea";
import { Group, Row } from "../settings/layout";
import { useSavedReplies, useSavedReplyMutation, useSaveSlaTargets, useSlaTargets, workspaceApi } from "./queries";
import { formatTarget } from "./sla";
import type { Priority, SavedReply } from "./types";

const priorityLabels: Record<Priority, string> = { urgent: "Urgent", high: "High", medium: "Medium", low: "Low" };

/** First-response targets for customers handed to a person. Changing them needs the studio editor role. */
export function ServiceLevelSettings() {
  const targets = useSlaTargets();
  const save = useSaveSlaTargets();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const values = targets.data ?? [];
  const changed = values.some((item) => draft[item.priority] !== undefined && Number(draft[item.priority]) !== item.first_response_minutes);
  const valid = Object.values(draft).every((value) => /^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= 10_080);

  return (
    <Group title="Service levels" description="How fast a person should first reply after the AI assistant hands a customer over, by priority. The inbox counts down against these.">
      {targets.isPending ? <div className="grid gap-2 p-4"><Skeleton className="h-5" /><Skeleton className="h-5" /></div> : (
        <>
          {values.map((item) => (
            <Row key={item.priority} label={priorityLabels[item.priority]} hint={formatTarget(Number(draft[item.priority] ?? item.first_response_minutes) || item.first_response_minutes)}>
              <Input type="number" min={1} max={10080} inputMode="numeric" aria-label={`${priorityLabels[item.priority]} first reply target in minutes`} className="h-8 w-24 text-right tabular-nums"
                value={draft[item.priority] ?? String(item.first_response_minutes)} onChange={(event) => setDraft({ ...draft, [item.priority]: event.target.value })} />
              <span className="text-xs text-muted-foreground">min</span>
            </Row>
          ))}
          <div className="flex items-center justify-end gap-2 px-4 py-2.5">
            {save.error && <span role="alert" className="mr-auto text-xs text-danger">{save.error.message}</span>}
            {save.isSuccess && !changed && <span className="mr-auto text-xs text-muted-foreground">Saved.</span>}
            <Button variant="ghost" size="sm" disabled={!changed} onClick={() => setDraft({})}>Cancel</Button>
            <Button size="sm" disabled={!changed || !valid || save.isPending}
              onClick={() => save.mutate(values.map((item) => ({ priority: item.priority, first_response_minutes: Number(draft[item.priority] ?? item.first_response_minutes) })), { onSuccess: () => setDraft({}) })}>
              Save targets
            </Button>
          </div>
        </>
      )}
    </Group>
  );
}

function ReplyEditor({ initial, saving, onSave, onCancel }: { initial?: SavedReply; saving: boolean; onSave: (title: string, content: string) => void; onCancel: () => void }) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [content, setContent] = useState(initial?.content ?? "");
  return (
    <form className="grid gap-2 px-4 py-3" onSubmit={(event) => { event.preventDefault(); if (title.trim() && content.trim()) onSave(title.trim(), content.trim()); }}>
      <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Name, e.g. Refund timing" aria-label="Saved reply name" maxLength={120} autoFocus />
      <Textarea value={content} onChange={(event) => setContent(event.target.value)} rows={3} maxLength={4000} aria-label="Saved reply text"
        placeholder="Hi {first_name}, refunds reach your account within 5–10 business days." />
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{"{first_name}"} and {"{name}"} are filled in with the customer's name.</span>
        <span className="flex gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          <Button type="submit" size="sm" disabled={saving || !title.trim() || !content.trim()}>Save reply</Button>
        </span>
      </div>
    </form>
  );
}

/** Team-wide reusable replies. Operators insert them into the composer and can edit before sending. */
export function SavedRepliesSettings() {
  const replies = useSavedReplies();
  const create = useSavedReplyMutation((input: { title: string; content: string }) => workspaceApi.createSavedReply(input.title, input.content));
  const update = useSavedReplyMutation((input: { id: number; title: string; content: string }) => workspaceApi.updateSavedReply(input.id, input.title, input.content));
  const remove = useSavedReplyMutation((id: number) => workspaceApi.deleteSavedReply(id));
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const error = create.error ?? update.error ?? remove.error;

  return (
    <Group title="Saved replies" description="Answers your team sends often. Insert one from the reply box, then edit it before sending. Shared with everyone in this workspace.">
      {replies.isPending ? <div className="grid gap-2 p-4"><Skeleton className="h-5" /><Skeleton className="h-5" /></div> : (
        <>
          {(replies.data ?? []).map((reply) => editing === reply.id ? (
            <ReplyEditor key={reply.id} initial={reply} saving={update.isPending} onCancel={() => setEditing(null)}
              onSave={(title, content) => update.mutate({ id: reply.id, title, content }, { onSuccess: () => setEditing(null) })} />
          ) : (
            <div key={reply.id} className="flex items-start gap-3 px-4 py-3">
              <div className="grid min-w-0 flex-1 gap-0.5">
                <span className="text-sm text-foreground">{reply.title}</span>
                <span className="line-clamp-2 text-xs text-muted-foreground">{reply.content}</span>
                <span className="text-[11px] text-muted-foreground">Used {reply.use_count} time{reply.use_count === 1 ? "" : "s"} · added by {reply.created_by}</span>
              </div>
              <Button size="icon-sm" variant="ghost" aria-label={`Edit ${reply.title}`} onClick={() => setEditing(reply.id)}><Pencil /></Button>
              <Button size="icon-sm" variant="ghost" aria-label={`Delete ${reply.title}`} disabled={remove.isPending} onClick={() => remove.mutate(reply.id)}><Trash2 /></Button>
            </div>
          ))}
          {editing === "new" ? (
            <ReplyEditor saving={create.isPending} onCancel={() => setEditing(null)} onSave={(title, content) => create.mutate({ title, content }, { onSuccess: () => setEditing(null) })} />
          ) : (
            <div className="flex items-center justify-between gap-2 px-4 py-2.5">
              <span className="text-xs text-muted-foreground">{(replies.data ?? []).length === 0 ? "No saved replies yet." : ""}</span>
              <Button size="sm" variant="outline" onClick={() => setEditing("new")}><Plus />Add saved reply</Button>
            </div>
          )}
          {error && <p role="alert" className="px-4 pb-3 text-xs text-danger">{error.message}</p>}
        </>
      )}
    </Group>
  );
}
