import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ReplyBlock } from "../../types";
import { bubbleText, ReplyBlocks } from "./ReplyBlocks";

const orders: ReplyBlock = {
  type: "orders",
  orders: [
    { id: 1, status: "delivered", total_amount: "149.99", created_at: "2026-01-10T00:00:00", refund_status: "requested" },
    { id: 2, status: "shipped", total_amount: "39.50", created_at: "2026-01-11T00:00:00", refund_status: null },
  ],
};
const confirm: ReplyBlock = { type: "quick_replies", purpose: "confirmation", replies: [{ label: "Evet, onaylıyorum", reply: "Evet, onaylıyorum", tone: "primary" }] };

describe("ReplyBlocks", () => {
  it("draws the order table in the reply's language", () => {
    const html = renderToStaticMarkup(<ReplyBlocks blocks={[orders]} text="2 siparişiniz var: #1: teslim edildi" />);
    expect(html).toContain("Sipariş");
    expect(html).toContain("Kargoda");
    expect(html).toContain("İnceleniyor");
    expect(html).toContain("#2");
  });

  it("shows quick replies only where the reader can answer as the customer", () => {
    expect(renderToStaticMarkup(<ReplyBlocks blocks={[confirm]} text="Onaylıyor musunuz?" />)).not.toContain("onaylıyorum");
    expect(renderToStaticMarkup(<ReplyBlocks blocks={[confirm]} text="Onaylıyor musunuz?" onReply={() => undefined} />)).toContain("Evet, onaylıyorum");
  });

  it("keeps only the lead in the bubble when a table lists the rows", () => {
    expect(bubbleText("You have 2 orders: #1: delivered; #2: shipped.", [orders])).toBe("You have 2 orders:");
    expect(bubbleText("Order #5 is now cancelled.", [{ type: "order", order: orders.orders[0] }])).toBe("Order #5 is now cancelled.");
    expect(bubbleText("Plain answer.", [])).toBe("Plain answer.");
    const card: ReplyBlock = { type: "order", order: orders.orders[1] };
    expect(bubbleText("Order #2: shipped, $39.50, placed Jan 11, 2026.", [card])).toBe("Here are the order details:");
    expect(bubbleText("Order #2: shipped, $39.50, placed Jan 11, 2026. Delivery depends on the service.", [card])).toBe("Delivery depends on the service.");
  });
});
