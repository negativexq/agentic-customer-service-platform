import { describe, expect, it } from "vitest";
import type { OperatorConversation } from "../types";
import { buildNotifications, isUnread } from "./notifications";

const now = new Date("2026-11-23T12:00:00Z");
const conversation = (id: number, state: "ok" | "at_risk" | "breached", due: string): OperatorConversation => ({
  id, conversation_id: `c-${id}`, customer_id: id, customer_name: `Customer ${id}`, owner: "queued", assigned_to_me: false, assignee_name: null,
  ownership_version: 1, created_at: due, updated_at: due, last_message: null, escalation: { priority: "high", reason: "refund_dispute", summary: "", status: "open", created_at: due },
  sla: { state, due_at: due, target_minutes: 60 } as OperatorConversation["sla"],
});

describe("console notifications", () => {
  it("puts an unreachable API and overdue customers first, oldest overdue on top", () => {
    const items = buildNotifications({
      health: undefined, healthUnavailable: true, errors: undefined, quality: undefined, now,
      inbox: [conversation(1, "breached", "2026-11-23T11:00:00Z"), conversation(2, "breached", "2026-11-23T09:00:00Z"), conversation(3, "ok", "2026-11-23T13:00:00Z")],
    });
    expect(items.map((item) => item.id)).toEqual(["health:unreachable", "sla:2", "sla:1"]);
    expect(items[1].target).toEqual({ kind: "conversation", id: 2 });
    expect(items[1].detail).toContain("Refund dispute");
  });

  it("summarises today's failures by their most common cause", () => {
    const row = (at: string, category: string) => ({ run_id: at, conversation_id: "c", customer_id: 1, at, intent: "x", status: "failed", category, failed_tools: [], degraded_components: [], recovery_action: null });
    const items = buildNotifications({
      health: { status: "ready", components: [] }, healthUnavailable: false, inbox: [], quality: undefined, now,
      errors: { window_days: 7, total_runs: 10, failed_runs: 3, categories: [], daily: [], errors: [row("2026-11-23T10:00:00Z", "runtime_error"), row("2026-11-23T11:00:00Z", "runtime_error"), row("2026-11-20T11:00:00Z", "llm_unavailable")] },
    });
    expect(items).toHaveLength(1);
    expect(items[0].title).toBe("2 runs failed today");
    expect(items[0].detail).toBe("Mostly runtime error (2)");
  });

  it("stays read once seen, unless the same notice moves to a later moment", () => {
    const item = { id: "errors:today", tone: "warning" as const, title: "", detail: "", at: "2026-11-23T10:00:00Z", target: { kind: "view" as const, view: "errors" as const } };
    expect(isUnread(item, {})).toBe(true);
    expect(isUnread(item, { "errors:today": "2026-11-23T10:00:00Z" })).toBe(false);
    expect(isUnread({ ...item, at: "2026-11-23T11:00:00Z" }, { "errors:today": "2026-11-23T10:00:00Z" })).toBe(true);
    expect(isUnread({ ...item, at: null }, { "errors:today": "" })).toBe(false);
  });
});
