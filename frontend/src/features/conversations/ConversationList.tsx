import { PenSquare, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { formatRelative, humanize, toneDot } from "../runs/runPresentation";
import { customerName, type ConversationSummary } from "./conversations";

type Props = {
  conversations: ConversationSummary[];
  selectedId: string;
  draftId: string | null;
  onSelect: (conversation: ConversationSummary) => void;
  onNew: () => void;
};

type Filter = "active" | "past";

function initials(name: string) {
  return name.startsWith("Customer") ? "#" : name.slice(0, 1);
}

export function ConversationList({ conversations, selectedId, draftId, onSelect, onNew }: Props) {
  const [filter, setFilter] = useState<Filter>("active");
  const [query, setQuery] = useState("");
  const counts = useMemo(() => ({
    active: conversations.filter((item) => item.active).length,
    past: conversations.filter((item) => !item.active).length,
  }), [conversations]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return conversations.filter((item) => (filter === "active" ? item.active : !item.active)).filter((item) => !needle
      || [item.id, item.lastIntent, customerName(item.customerId)].some((value) => value?.toLowerCase().includes(needle)));
  }, [conversations, filter, query]);

  return (
    <nav aria-label="Conversation list" className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 px-3 pt-3 pb-2">
        <h2 className="text-sm font-medium text-foreground">Inbox</h2>
        <button type="button" onClick={onNew} className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground">
          <PenSquare className="size-3.5" aria-hidden="true" />New
        </button>
      </div>
      <div className="grid gap-2 px-3 pb-2">
        <div className="grid grid-cols-2 rounded-md bg-muted p-0.5 text-xs" role="tablist" aria-label="Conversation status">
          {(["active", "past"] as const).map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={filter === item}
              onClick={() => setFilter(item)}
              className={`rounded px-2 py-1 transition-colors ${filter === item ? "bg-background text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground"}`}
            >
              {item === "active" ? "Active" : "Past"} <span className="tabular-nums opacity-70">{counts[item]}</span>
            </button>
          ))}
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search conversations"
            aria-label="Search conversations"
            className="h-8 w-full rounded-md border border-border bg-transparent pr-2 pl-8 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
          />
        </div>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {draftId && filter === "active" && (
          <li>
            <div className={`flex items-center gap-2.5 rounded-md px-2 py-2 ${selectedId === draftId ? "bg-accent" : ""}`} aria-current={selectedId === draftId ? "true" : undefined}>
              <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-dashed border-border text-muted-foreground"><PenSquare className="size-3.5" aria-hidden="true" /></span>
              <span className="grid min-w-0">
                <span className="truncate text-sm text-foreground">New conversation</span>
                <span className="truncate text-xs text-muted-foreground">Draft</span>
              </span>
            </div>
          </li>
        )}
        {visible.length === 0 && (
          <li className="px-2 py-8 text-center text-xs text-muted-foreground">
            {query ? "No conversations match." : filter === "active" ? "No active conversations." : "No past conversations."}
          </li>
        )}
        {visible.map((item) => {
          const name = customerName(item.customerId);
          const selected = item.id === selectedId;
          return (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => onSelect(item)}
                aria-current={selected ? "true" : undefined}
                className={`flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-accent/60 focus-visible:bg-accent/60 focus-visible:outline-none ${selected ? "bg-accent" : ""}`}
              >
                <span className="relative flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground">
                  {initials(name)}
                  <span className={`absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full border-2 border-background ${toneDot[item.status.tone]}`} aria-hidden="true" />
                </span>
                <span className="grid min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm text-foreground">{name}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{formatRelative(item.lastAt)}</span>
                  </span>
                  <span className="truncate text-xs text-muted-foreground">{humanize(item.lastIntent)} · {item.status.label}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
