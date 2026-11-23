import { Inbox } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({ title, description, icon: Icon = Inbox, action }: { title: string; description: string; icon?: LucideIcon; action?: ReactNode }) {
  return <div className="empty-state"><Icon size={18} strokeWidth={1.6} aria-hidden="true" /><div><div className="text-sm font-medium text-main">{title}</div><p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>{action && <div className="mt-3">{action}</div>}</div></div>;
}
