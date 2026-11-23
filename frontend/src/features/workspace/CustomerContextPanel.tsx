import { Brain, Mail, MessageSquare, Package, Star, Ticket } from "lucide-react";
import type { ReactNode } from "react";
import { Skeleton } from "@/components/primitives/skeleton";
import { useMemory } from "@/lib/queries";
import { formatRelative, humanize } from "../runs/runPresentation";
import { useCustomerContext } from "./queries";
import { UI_LOCALE } from "@/lib/utils";

function Section({ icon, title, count, children }: { icon: ReactNode; title: string; count?: number; children: ReactNode }) {
  return (
    <section className="grid gap-2 border-b border-border px-4 py-4 last:border-b-0">
      <h3 className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <span aria-hidden="true">{icon}</span>{title}
        {count !== undefined && <span className="tabular-nums">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>;
}

const statusTone: Record<string, string> = {
  delivered: "text-success", shipped: "text-info", processing: "text-warning", pending: "text-warning", cancelled: "text-muted-foreground",
  open: "text-warning", in_progress: "text-info", resolved: "text-success", closed: "text-muted-foreground",
};

export function Stars({ rating, size = "size-3" }: { rating: number; size?: string }) {
  return (
    <span className="inline-flex items-center gap-0.5" aria-label={`${rating} out of 5`}>
      {[1, 2, 3, 4, 5].map((value) => <Star key={value} className={`${size} ${value <= rating ? "fill-warning text-warning" : "text-muted-foreground/40"}`} aria-hidden="true" />)}
    </span>
  );
}

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

/** What the operator needs about the customer without leaving the conversation. */
export function CustomerContextPanel({ conversationPk, customerId, onOpenConversation }: { conversationPk: number; customerId: number; onOpenConversation: (id: number) => void }) {
  const context = useCustomerContext(conversationPk);
  const memory = useMemory(customerId, true);
  const data = context.data;
  if (context.isPending) return <div className="grid gap-3 p-4"><Skeleton className="h-16" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>;
  if (!data) return <p className="p-4 text-sm text-muted-foreground">Customer details could not be loaded.</p>;
  const activeMemory = (memory.data ?? []).filter((item) => item.status === "active");

  return (
    <div className="grid content-start" data-testid="customer-context">
      <div className="grid gap-1 border-b border-border px-4 py-4">
        <span className="text-sm font-medium text-foreground">{data.customer.name}</span>
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><Mail className="size-3" aria-hidden="true" />{data.customer.email}</span>
        <dl className="mt-2 grid gap-1 text-xs">
          {([
            ["Customer since", new Date(data.customer.customer_since).toLocaleDateString(UI_LOCALE, { month: "short", year: "numeric" })],
            ["Conversations", String(data.conversation_count)],
            ["Average rating", data.average_rating === null ? "Not rated yet" : `${data.average_rating.toFixed(1)} / 5`],
          ] as const).map(([label, value]) => (
            <div key={label} className="flex items-baseline justify-between gap-3"><dt className="text-muted-foreground">{label}</dt><dd className="tabular-nums text-foreground">{value}</dd></div>
          ))}
        </dl>
        {data.feedback && (
          <div className="mt-2 grid gap-1 rounded-lg border border-border px-3 py-2">
            <span className="flex items-center justify-between text-xs text-muted-foreground">Rated this conversation <Stars rating={data.feedback.rating} /></span>
            {data.feedback.comment && <p className="text-xs text-foreground">“{data.feedback.comment}”</p>}
          </div>
        )}
      </div>

      <Section icon={<Package className="size-3.5" />} title="Orders" count={data.orders.length}>
        {data.orders.length === 0 ? <Empty>No orders.</Empty> : (
          <ul className="grid gap-1.5">
            {data.orders.map((order) => (
              <li key={order.id} className="flex items-baseline justify-between gap-2 text-sm">
                <span className="text-foreground">#{order.id}<span className={`ml-2 text-xs ${statusTone[order.status] ?? "text-muted-foreground"}`}>{humanize(order.status)}</span></span>
                <span className="text-xs tabular-nums text-muted-foreground">{money.format(Number(order.total_amount))} · {formatRelative(order.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section icon={<Ticket className="size-3.5" />} title="Tickets" count={data.tickets.length}>
        {data.tickets.length === 0 ? <Empty>No support tickets.</Empty> : (
          <ul className="grid gap-2">
            {data.tickets.map((ticket) => (
              <li key={ticket.id} className="grid gap-0.5 text-sm">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="text-foreground">#{ticket.id} {humanize(ticket.category)}</span>
                  <span className={`text-xs ${statusTone[ticket.status] ?? "text-muted-foreground"}`}>{humanize(ticket.status)}</span>
                </span>
                <span className="line-clamp-2 text-xs text-muted-foreground">{ticket.description}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section icon={<MessageSquare className="size-3.5" />} title="Other conversations" count={data.other_conversations.length}>
        {data.other_conversations.length === 0 ? <Empty>This is their first conversation.</Empty> : (
          <ul className="grid gap-1">
            {data.other_conversations.map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => onOpenConversation(item.id)} className="grid w-full gap-0.5 rounded-md px-2 py-1.5 text-left hover:bg-accent/50">
                  <span className="flex items-baseline justify-between gap-2 text-xs">
                    <span className="text-foreground">{item.owner === "ai" ? "AI handling" : item.owner === "closed" ? "Resolved" : item.owner === "queued" ? "Waiting for a person" : "With a person"}</span>
                    <span className="flex items-center gap-1.5 text-muted-foreground">{item.rating !== null && <Stars rating={item.rating} />}{formatRelative(item.updated_at)}</span>
                  </span>
                  {item.last_message && <span className="truncate text-xs text-muted-foreground">{item.last_message}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section icon={<Brain className="size-3.5" />} title="What the assistant remembers" count={activeMemory.length}>
        {activeMemory.length === 0 ? <Empty>No stored memories.</Empty> : (
          <ul className="flex flex-wrap gap-1.5">
            {activeMemory.map((item) => <li key={item.id} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground" title={humanize(item.memory_type)}>{item.normalized_key}</li>)}
          </ul>
        )}
      </Section>
    </div>
  );
}
