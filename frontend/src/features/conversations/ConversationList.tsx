import { FlaskConical, Search, Star } from "lucide-react";
import { useMemo, useState } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/primitives/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/primitives/tooltip";
import { usePreferences } from "@/lib/preferences";
import type { OperatorConversation } from "../../types";
import { formatRelative, toneDot } from "../runs/runPresentation";
import { handler, inboxFilters, initials, matchesFilter, previewText, type InboxFilter } from "./conversations";
import { slaLabel, slaTone } from "../workspace/sla";

type Props = {
  conversations: OperatorConversation[];
  loading: boolean;
  selectedId: number | null;
  draftActive: boolean;
  onSelect: (conversation: OperatorConversation) => void;
  onNewTest: () => void;
};

const priorityTone: Record<string, string> = { urgent: "text-danger", high: "text-danger", medium: "text-warning", low: "text-muted-foreground" };

export function ConversationList({ conversations, loading, selectedId, draftActive, onSelect, onNewTest }: Props) {
  const preferences = usePreferences();
  const [filter, setFilter] = useState<InboxFilter>(preferences.defaultInboxView);
  const [query, setQuery] = useState("");
  const counts = useMemo(
    () => Object.fromEntries(inboxFilters.map((item) => [item.id, conversations.filter((c) => matchesFilter(c, item.id)).length])) as Record<InboxFilter, number>,
    [conversations],
  );
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return conversations
      .filter((item) => matchesFilter(item, filter))
      .filter((item) => !needle || [item.customer_name, item.conversation_id, item.last_message?.content].some((value) => value?.toLowerCase().includes(needle)))
      // Waiting customers first (unless turned off in Settings), then most recent activity.
      .sort((a, b) => (preferences.openWaitingFirst ? Number(b.owner === "queued") - Number(a.owner === "queued") : 0) || b.updated_at.localeCompare(a.updated_at));
  }, [conversations, filter, query, preferences.openWaitingFirst]);

  return (
    <nav aria-label="Conversation list" className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1.5 px-3 pt-3 pb-2">
        <Select value={filter} onValueChange={(value) => setFilter((value as InboxFilter) ?? "all")}>
          <SelectTrigger size="sm" className="h-8 min-w-0 flex-1 border-transparent bg-transparent px-2 text-sm font-medium shadow-none hover:bg-accent" aria-label="Inbox view">
            <SelectValue>{(value: string) => {
              const item = inboxFilters.find((entry) => entry.id === value) ?? inboxFilters[0];
              return <span className="flex items-center gap-2">{item.label}<span className="text-xs font-normal tabular-nums text-muted-foreground">{counts[item.id]}</span></span>;
            }}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {inboxFilters.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                <span className="flex w-full items-center justify-between gap-6">
                  {item.label}
                  <span className={`text-xs tabular-nums ${item.id === "queued" && counts.queued > 0 ? "text-warning" : "text-muted-foreground"}`}>{counts[item.id]}</span>
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {counts.queued > 0 && filter !== "queued" && (
          <button type="button" onClick={() => setFilter("queued")} className="inline-flex h-6 shrink-0 items-center gap-1 rounded-full bg-warning/12 px-2 text-[11px] text-warning" title="Conversations waiting for an operator">
            {counts.queued} waiting
          </button>
        )}
        <Tooltip>
          <TooltipTrigger render={
            <button type="button" onClick={onNewTest} aria-label="New test conversation"
              className={`inline-flex size-8 shrink-0 items-center justify-center rounded-md hover:bg-accent hover:text-foreground ${draftActive ? "bg-accent text-foreground" : "text-muted-foreground"}`} />
          }>
            <FlaskConical className="size-4" aria-hidden="true" />
          </TooltipTrigger>
          <TooltipContent>New test conversation</TooltipContent>
        </Tooltip>
      </div>
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search customers or messages"
            aria-label="Search conversations"
            className="h-8 w-full rounded-md border border-border bg-transparent pr-2 pl-8 text-sm outline-none placeholder:text-muted-foreground focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
          />
        </div>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {visible.length === 0 && (
          <li className="px-2 py-8 text-center text-xs text-muted-foreground">
            {loading ? "Loading conversations…" : query ? "No conversations match." : filter === "queued" ? "Nobody is waiting for a person." : filter === "mine" ? "You are not handling any conversation." : "No conversations here yet."}
          </li>
        )}
        {visible.map((item) => {
          const who = handler(item);
          const selected = item.id === selectedId;
          return (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => onSelect(item)}
                aria-current={selected ? "true" : undefined}
                className={`flex w-full gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-accent/60 focus-visible:bg-accent/60 focus-visible:outline-none ${selected ? "bg-accent" : ""}`}
              >
                <span className="relative mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground">
                  {initials(item.customer_name)}
                  <span className={`absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full border-2 border-background ${toneDot[who.tone]}`} aria-hidden="true" />
                </span>
                <span className="grid min-w-0 flex-1 gap-0.5">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{item.customer_name}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{formatRelative(item.updated_at)}</span>
                  </span>
                  <span className="truncate text-xs text-muted-foreground">{previewText(item.last_message)}</span>
                  <span className="flex min-w-0 items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="truncate">{who.short}</span>
                    {item.escalation && item.owner !== "closed" && item.owner !== "ai" && (
                      <span className={`shrink-0 ${priorityTone[item.escalation.priority] ?? "text-muted-foreground"}`}>{item.escalation.priority}</span>
                    )}
                    {item.sla && <span className={`ml-auto shrink-0 tabular-nums ${slaTone[item.sla.state]}`} data-testid="inbox-sla">{slaLabel(item.sla)}</span>}
                    {!item.sla && item.csat_rating != null && <span className="ml-auto inline-flex shrink-0 items-center gap-0.5" aria-label={`Rated ${item.csat_rating} of 5`}><Star className="size-3 fill-warning text-warning" aria-hidden="true" />{item.csat_rating}</span>}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
