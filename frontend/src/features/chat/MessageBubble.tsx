import { Bot, UserRound } from "lucide-react";
import type { ReactNode } from "react";
import { Badge } from "../../components/ui";

export type ChatMessage = {
  id: string;
  role: "customer" | "agent";
  content: string;
  timestamp: string;
  state?: string;
};

function formatTime(timestamp: string): string {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function MessageBubble({ message }: { message: ChatMessage }): ReactNode {
  const customer = message.role === "customer";
  return (
    <article
      className={`flex gap-3 ${customer ? "justify-start" : "justify-end"}`}
      data-testid={`${message.role}-message`}
    >
      <div className={`flex max-w-[88%] gap-2.5 sm:max-w-[76%] ${customer ? "" : "flex-row-reverse"}`}>
        <span
          className={`mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ${
            customer ? "border-info/30 bg-info/10 text-info" : "border-success/30 bg-success/10 text-success"
          }`}
          aria-hidden="true"
        >
          {customer ? <UserRound size={14} /> : <Bot size={14} />}
        </span>
        <div className={`min-w-0 ${customer ? "" : "text-right"}`}>
          <div className="mb-1 flex items-center gap-2 text-xs text-muted">
            <span className="font-medium text-main">{customer ? "Customer" : "Agent"}</span>
            <span>{formatTime(message.timestamp)}</span>
            {!customer && message.state && <Badge tone={message.state === "awaiting confirmation" ? "warning" : "neutral"}>{message.state}</Badge>}
          </div>
          <div className={`rounded-xl border px-3.5 py-3 text-sm leading-6 ${customer ? "border-info/20 bg-info/5 text-main" : "border-success/20 bg-success/5 text-main"}`}>
            {message.content}
          </div>
        </div>
      </div>
    </article>
  );
}
