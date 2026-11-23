import { AlertTriangle, Bell, CircleCheck, Clock, ScanEye, ServerCrash } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/primitives/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/primitives/dropdown-menu";
import { formatRelative } from "../features/runs/runPresentation";
import { isUnread, readSeen, writeSeen, type ConsoleNotification } from "./notifications";

const toneIcon = (item: ConsoleNotification) => {
  if (item.id.startsWith("health")) return ServerCrash;
  if (item.id.startsWith("sla")) return Clock;
  if (item.id.startsWith("quality")) return ScanEye;
  return AlertTriangle;
};

const toneClass: Record<ConsoleNotification["tone"], string> = {
  danger: "bg-danger/12 text-danger",
  warning: "bg-warning/14 text-warning",
  info: "bg-info/12 text-info",
};

export function NotificationsMenu({ items, onSelect }: { items: ConsoleNotification[]; onSelect: (item: ConsoleNotification) => void }) {
  const [seen, setSeen] = useState(readSeen);
  const unread = items.filter((item) => isUnread(item, seen));
  const markAllRead = () => setSeen(writeSeen(items));

  return (
    <DropdownMenu onOpenChange={(open) => { if (!open) markAllRead(); }}>
      <DropdownMenuTrigger render={
        <Button variant="ghost" size="icon" className="relative" aria-label={unread.length ? `Notifications, ${unread.length} new` : "Notifications"} />
      }>
        <Bell aria-hidden="true" />
        {unread.length > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] leading-none font-semibold text-white tabular-nums ring-2 ring-background" aria-hidden="true">
            {unread.length > 9 ? "9+" : unread.length}
          </span>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="w-[min(24rem,calc(100vw-2rem))] p-0">
        <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2.5">
          <span className="text-sm font-medium text-foreground">Notifications</span>
          {unread.length > 0 && (
            <button type="button" onClick={markAllRead} className="rounded text-xs text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
              Mark all as read
            </button>
          )}
        </div>
        {items.length === 0 ? (
          <div className="flex items-center gap-2.5 px-3 py-8 text-sm text-muted-foreground">
            <CircleCheck className="size-4 text-success" aria-hidden="true" />Nothing needs your attention.
          </div>
        ) : (
          <div className="max-h-[min(28rem,70vh)] overflow-y-auto p-1">
            {items.map((item) => {
              const Icon = toneIcon(item);
              const fresh = isUnread(item, seen);
              return (
                <DropdownMenuItem key={item.id} onClick={() => onSelect(item)} className="items-start gap-3 rounded-lg px-2.5 py-2.5">
                  <span className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg ${toneClass[item.tone]}`} aria-hidden="true">
                    <Icon className="size-3.5" />
                  </span>
                  <span className="grid min-w-0 flex-1 gap-0.5">
                    <span className={`text-[13px] leading-5 ${fresh ? "font-medium text-foreground" : "text-foreground/80"}`}>{item.title}</span>
                    <span className="truncate text-xs text-muted-foreground">{item.detail}</span>
                    {item.at && <span className="text-[11px] text-muted-foreground/80">{formatRelative(item.at)}</span>}
                  </span>
                  {fresh && <span className="mt-2 size-1.5 shrink-0 rounded-full bg-info" aria-label="New" />}
                </DropdownMenuItem>
              );
            })}
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
