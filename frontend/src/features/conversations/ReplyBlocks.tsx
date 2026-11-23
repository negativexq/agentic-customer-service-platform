import type { OrderRow, QuickReply, ReplyBlock, TicketRow } from "../../types";
import { parseTimestamp, toneDot, type Tone } from "../runs/runPresentation";

type Lang = "en" | "tr";

// The reply text decides the language: blocks sit under it and must read the same way.
const TURKISH = /[çğıöşüÇĞİÖŞÜ]|\b(?:sipariş|merhaba|teşekkür|iade|kargo)\w*/;
export const replyLanguage = (text: string): Lang => (TURKISH.test(text) ? "tr" : "en");

/**
 * With a table or card under it, the bubble keeps only the lead ("You have 2 orders:")
 * instead of repeating every row. The full text stays the stored reply.
 */
export function bubbleText(text: string, blocks: ReplyBlock[]): string {
  // A compound answer holds one paragraph per request; each is shortened on its own.
  return text.split("\n\n").map((paragraph) => shortenLead(paragraph, blocks)).join("\n\n");
}

function shortenLead(text: string, blocks: ReplyBlock[]): string {
  const lang = replyLanguage(text);
  const colon = text.indexOf(":");
  if (blocks.some((block) => block.type === "orders" || block.type === "tickets") && colon > 0 && colon < 80 && /#\d/.test(text.slice(colon))) {
    return `${text.slice(0, colon)}:`;
  }
  const card = blocks.find((block) => block.type === "order" || block.type === "ticket");
  if (card && /^\S+\s+#\d+:/.test(text)) {
    // The card holds the first sentence's facts; anything after it (refund status, delivery
    // guidance) still needs saying.
    const rest = text.split(/(?<=\.)\s+/).slice(1).join(" ").trim();
    if (rest) return rest;
    return card.type === "order" ? (lang === "tr" ? "Sipariş bilgileri:" : "Here are the order details:") : (lang === "tr" ? "Talep bilgileri:" : "Here are the ticket details:");
  }
  return text;
}

const copy = {
  en: { order: "Order", status: "Status", total: "Total", placed: "Placed", refund: "Refund", ticket: "Ticket", topic: "Topic", orders: "Orders", tickets: "Support tickets" },
  tr: { order: "Sipariş", status: "Durum", total: "Tutar", placed: "Tarih", refund: "İade", ticket: "Talep", topic: "Konu", orders: "Siparişler", tickets: "Destek talepleri" },
} as const;

const statusCopy: Record<Lang, Record<string, string>> = {
  en: { pending: "Pending", processing: "Processing", shipped: "Shipped", delivered: "Delivered", cancelled: "Cancelled", open: "Open", in_progress: "In progress", resolved: "Resolved", closed: "Closed", requested: "Requested", approved: "Approved", rejected: "Declined", completed: "Completed" },
  tr: { pending: "Beklemede", processing: "Hazırlanıyor", shipped: "Kargoda", delivered: "Teslim edildi", cancelled: "İptal edildi", open: "Açık", in_progress: "İşlemde", resolved: "Çözüldü", closed: "Kapandı", requested: "İnceleniyor", approved: "Onaylandı", rejected: "Reddedildi", completed: "Tamamlandı" },
};

const statusTone: Record<string, Tone> = {
  pending: "warning", processing: "info", shipped: "info", delivered: "success", cancelled: "neutral",
  open: "warning", in_progress: "info", resolved: "success", closed: "neutral",
  requested: "warning", approved: "success", rejected: "danger", completed: "success",
};

function Status({ value, lang }: { value: string; lang: Lang }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span className={`size-1.5 shrink-0 rounded-full ${toneDot[statusTone[value] ?? "neutral"]}`} aria-hidden="true" />
      {statusCopy[lang][value] ?? value.replace(/_/g, " ")}
    </span>
  );
}

const money = (value: string | number, lang: Lang) =>
  new Intl.NumberFormat(lang === "tr" ? "tr-TR" : "en-US", { style: "currency", currency: "USD" }).format(Number(value));
const day = (value: string, lang: Lang) =>
  parseTimestamp(value).toLocaleDateString(lang === "tr" ? "tr-TR" : "en-US", { day: "numeric", month: "short", year: "numeric" });

const frame = "w-full min-w-0 overflow-hidden rounded-xl border border-border bg-card text-sm";
const head = "px-2.5 py-2 text-left text-xs font-medium whitespace-nowrap text-muted-foreground";
const cell = "px-2.5 py-2 align-top";

function OrdersTable({ orders, lang }: { orders: OrderRow[]; lang: Lang }) {
  const t = copy[lang];
  const showRefund = orders.some((order) => order.refund_status);
  return (
    <div className={frame}>
      <div className="overflow-x-auto">
        <table className="w-full" aria-label={t.orders}>
          <thead className="border-b border-border bg-muted/40">
            <tr>
              <th scope="col" className={head}>{t.order}</th>
              <th scope="col" className={head}>{t.status}</th>
              <th scope="col" className={`${head} text-right`}>{t.total}</th>
              <th scope="col" className={head}>{t.placed}</th>
              {showRefund && <th scope="col" className={head}>{t.refund}</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {orders.map((order) => (
              <tr key={order.id}>
                <td className={`${cell} font-mono text-xs tabular-nums`}>#{order.id}</td>
                <td className={cell}><Status value={order.status} lang={lang} /></td>
                <td className={`${cell} text-right tabular-nums`}>{money(order.total_amount, lang)}</td>
                <td className={`${cell} whitespace-nowrap text-muted-foreground`}>{day(order.created_at, lang)}</td>
                {showRefund && <td className={cell}>{order.refund_status ? <Status value={order.refund_status} lang={lang} /> : <span className="text-muted-foreground">—</span>}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OrderCard({ order, lang }: { order: OrderRow; lang: Lang }) {
  const t = copy[lang];
  return (
    <section className={frame} aria-label={`${t.order} #${order.id}`}>
      <header className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
        <span className="font-medium">{t.order} <span className="font-mono tabular-nums">#{order.id}</span></span>
        <Status value={order.status} lang={lang} />
      </header>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 px-3 py-2.5">
        <dt className="text-muted-foreground">{t.total}</dt><dd className="text-right tabular-nums">{money(order.total_amount, lang)}</dd>
        <dt className="text-muted-foreground">{t.placed}</dt><dd className="text-right">{day(order.created_at, lang)}</dd>
        {order.refund_status && <><dt className="text-muted-foreground">{t.refund}</dt><dd className="flex justify-end"><Status value={order.refund_status} lang={lang} /></dd></>}
      </dl>
    </section>
  );
}

function TicketsTable({ tickets, lang }: { tickets: TicketRow[]; lang: Lang }) {
  const t = copy[lang];
  return (
    <div className={frame}>
      <div className="overflow-x-auto">
        <table className="w-full" aria-label={t.tickets}>
          <thead className="border-b border-border bg-muted/40">
            <tr>
              <th scope="col" className={head}>{t.ticket}</th>
              <th scope="col" className={head}>{t.topic}</th>
              <th scope="col" className={head}>{t.status}</th>
              <th scope="col" className={head}>{t.order}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {tickets.map((ticket) => (
              <tr key={ticket.id}>
                <td className={`${cell} font-mono text-xs tabular-nums`}>#{ticket.id}</td>
                <td className={cell}>{ticket.category}</td>
                <td className={cell}><Status value={ticket.status} lang={lang} /></td>
                <td className={`${cell} font-mono text-xs tabular-nums text-muted-foreground`}>{ticket.order_id ? `#${ticket.order_id}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TicketCard({ ticket, lang }: { ticket: TicketRow; lang: Lang }) {
  const t = copy[lang];
  return (
    <section className={frame} aria-label={`${t.ticket} #${ticket.id}`}>
      <header className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
        <span className="font-medium">{t.ticket} <span className="font-mono tabular-nums">#{ticket.id}</span> <span className="font-normal text-muted-foreground">· {ticket.category}</span></span>
        <Status value={ticket.status} lang={lang} />
      </header>
      {(ticket.description || ticket.order_id) && (
        <div className="grid gap-1 px-3 py-2.5">
          {ticket.description && <p className="leading-6">{ticket.description}</p>}
          {ticket.order_id && <p className="text-xs text-muted-foreground">{t.order} <span className="font-mono">#{ticket.order_id}</span></p>}
        </div>
      )}
    </section>
  );
}

function QuickReplies({ replies, onReply }: { replies: QuickReply[]; onReply?: (text: string) => void }) {
  if (!onReply) return null;
  return (
    <div className="flex flex-wrap justify-end gap-2" role="group">
      {replies.map((item) => (
        <button key={item.reply} type="button" onClick={() => onReply(item.reply)} data-testid="quick-reply"
          className={`inline-flex min-h-8 items-center rounded-full px-3.5 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none pointer-coarse:min-h-10 ${item.tone === "primary" ? "bg-primary text-primary-foreground hover:bg-primary/90" : "border border-border bg-card hover:bg-accent"}`}>
          {item.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Tables, cards and quick replies under an AI reply. Quick replies show only where the
 * reader can answer as the customer (`onReply`), and only on the latest reply.
 */
export function ReplyBlocks({ blocks, text, onReply }: { blocks: ReplyBlock[]; text: string; onReply?: (text: string) => void }) {
  if (!blocks.length) return null;
  const lang = replyLanguage(text);
  return (
    <div className="grid w-full min-w-[min(100%,20rem)] gap-2" data-testid="reply-blocks">
      {blocks.map((block, index) => {
        switch (block.type) {
          case "orders": return <OrdersTable key={index} orders={block.orders} lang={lang} />;
          case "order": return <OrderCard key={index} order={block.order} lang={lang} />;
          case "tickets": return <TicketsTable key={index} tickets={block.tickets} lang={lang} />;
          case "ticket": return <TicketCard key={index} ticket={block.ticket} lang={lang} />;
          case "quick_replies": return <QuickReplies key={index} replies={block.replies} onReply={onReply} />;
        }
      })}
    </div>
  );
}
