import { describe, expect, it } from "vitest";
import { actorName, groupByDay, handlingSummary, matches, serverSearch, span } from "./presentation";
import type { AuditEvent } from "./types";

const base: Omit<AuditEvent, "id" | "at" | "action" | "kind" | "actor" | "summary"> = {
  conversation_pk: 7, conversation_id: "conv-7", customer_id: 1, customer_name: "Ada Lovelace", detail: null, run_id: null,
};
const event = (id: string, at: string, action: string, actor: AuditEvent["actor"], kind: AuditEvent["kind"] = "handoff"): AuditEvent =>
  ({ ...base, id, at, action, actor, kind, summary: action });
const ai = { type: "ai", id: null, name: "AI assistant" } as const;
const op = { type: "operator", id: "operator-local-demo", name: "Demo operator" } as const;
const you = { actorId: "operator-local-demo", name: "Alex Morgan" };

describe("audit presentation", () => {
  it("names the signed-in operator as themselves", () => {
    expect(actorName(op, you)).toBe("Alex Morgan (you)");
    expect(actorName({ ...op, id: "someone-else" }, you)).toBe("Demo operator");
    expect(actorName(ai, you)).toBe("AI assistant");
  });

  it("groups consecutive entries by calendar day", () => {
    const now = new Date("2026-11-23T15:00:00Z");
    const groups = groupByDay([
      event("a", "2026-11-23T12:30:00", "x", ai),
      event("b", "2026-11-23T12:00:00", "x", ai),
      event("c", "2026-11-22T12:00:00", "x", ai),
      event("d", "2026-11-18T12:00:00", "x", ai),
    ], now);
    expect(groups.map((group) => [group.label, group.events.length])).toEqual([["Today", 2], ["Yesterday", 1], ["Wednesday 18 November", 1]]);
  });

  it("summarises who held the conversation and for how long", () => {
    const trail = [
      event("1", "2026-11-23T09:00:00", "conversation.started", { type: "customer", id: null, name: "Ada Lovelace" }, "conversation"),
      event("2", "2026-11-23T09:05:00", "handoff.queued", ai),
      event("3", "2026-11-23T09:17:00", "operator.claimed", op),
      event("4", "2026-11-23T09:40:00", "conversation.closed", op, "conversation"),
    ];
    const lines = handlingSummary(trail, you);
    expect(lines[0]).toBe("AI assistant handled it for 5m");
    expect(lines[1]).toBe("Waited 12m in the queue");
    expect(lines[2]).toMatch(/^Alex Morgan \(you\) picked it up at \d{2}:17/);
    expect(lines[3]).toMatch(/^Closed by Alex Morgan \(you\) at \d{2}:40/);
  });

  it("searches across customer, person and conversation", () => {
    const item = event("1", "2026-11-23T09:00:00", "operator.claimed", op);
    expect(matches(item, "alex", you)).toBe(true);
    expect(matches(item, "CONV-7", you)).toBe(true);
    expect(matches(item, "grace", you)).toBe(false);
  });

  it("asks the server for the signed-in operator by actor id", () => {
    expect(serverSearch(" Alex ", you)).toBe("operator-local-demo");
    expect(serverSearch("refund", you)).toBe("refund");
    expect(serverSearch("a", you)).toBe("a");
  });

  it("reads server times as UTC", () => {
    const item = event("1", "2026-11-23T12:00:00", "x", ai);
    expect(groupByDay([item], new Date("2026-11-23T12:00:00Z"))[0].label).toBe("Today");
  });

  it("formats spans compactly", () => {
    expect(span("2026-11-23T09:00:00", "2026-11-23T09:00:40")).toBe("40s");
    expect(span("2026-11-23T09:00:00", "2026-11-23T12:30:00")).toBe("3h 30m");
    expect(span("2026-11-20T09:00:00", "2026-11-23T12:00:00")).toBe("3d 3h");
  });
});
