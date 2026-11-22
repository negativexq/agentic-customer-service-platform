import { MessageSquareText } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/primitives/dropdown-menu";
import { useSavedReplies, useSavedReplyMutation, workspaceApi } from "./queries";

/** Fills `{first_name}` and `{name}` so a saved reply reads as written for this customer. */
export function personalise(content: string, customerName: string): string {
  const name = customerName.trim() || "there";
  return content.split("{first_name}").join(name.split(/\s+/)[0]).split("{name}").join(name);
}

export function SavedReplyMenu({ customerName, disabled, onInsert }: { customerName: string; disabled: boolean; onInsert: (text: string) => void }) {
  const replies = useSavedReplies();
  const used = useSavedReplyMutation((id: number) => workspaceApi.savedReplyUsed(id));
  const items = replies.data ?? [];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger disabled={disabled} aria-label="Insert a saved reply" title="Saved replies"
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40">
        <MessageSquareText className="size-4" aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="w-80">
        <DropdownMenuLabel>Saved replies</DropdownMenuLabel>
        {items.length === 0 && <p className="px-2 py-2 text-xs text-muted-foreground">No saved replies yet. Add them in Settings.</p>}
        {items.map((reply) => (
          <DropdownMenuItem key={reply.id} onClick={() => { onInsert(personalise(reply.content, customerName)); used.mutate(reply.id); }} className="grid gap-0.5">
            <span className="text-sm text-foreground">{reply.title}</span>
            <span className="line-clamp-2 text-xs text-muted-foreground">{reply.content}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
