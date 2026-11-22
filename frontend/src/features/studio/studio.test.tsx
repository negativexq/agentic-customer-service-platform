import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAuthProvider } from "../../auth/provider";
import { initializeApiAuth, setApiAuthProvider } from "../../api/client";
import { ModePicker } from "./ActionsPage";
import { ProcedureDetail } from "./ProceduresPage";
import { studioApi } from "./queries";
import type { ActionView, ProcedureView } from "./types";

const action = (extra: Partial<ActionView>): ActionView => ({
  name: "create_support_ticket", description: "Open a ticket", operation: "write", risk_level: 1, risk_label: "Low risk",
  default_policy: "allow", allowed_modes: ["enabled", "confirm", "disabled"], locked: false, published_mode: "enabled", draft_mode: null,
  safeguards: [], inputs: [], procedures: [], usage: { calls: 0, succeeded: 0, failed: 0, awaiting_confirmation: 0, denied: 0, last_used_at: null },
  ...extra,
});

afterEach(() => { vi.unstubAllGlobals(); setApiAuthProvider(createAuthProvider("local_demo", null)); });

describe("agent studio", () => {
  it("offers only the modes that narrow an action's authority", () => {
    const html = renderToStaticMarkup(<ModePicker action={action({})} value="confirm" onChange={() => {}} />);
    expect(html).toContain("Ask customer first");
    expect(html).toMatch(/aria-checked="true"[^>]*>Ask customer first/);
    const locked = renderToStaticMarkup(<ModePicker action={action({ name: "escalate_to_human", locked: true, allowed_modes: ["enabled"] })} value="enabled" onChange={() => {}} />);
    expect(locked).toContain("Always on");
    expect(locked).not.toContain("radio");
  });

  it("shows a procedure's steps, actions and outcome rates", () => {
    const procedure: ProcedureView = {
      intent: "order_cancel", name: "Cancel an order", summary: "Cancel after confirmation.", request_type: "write_action",
      steps: [
        { kind: "understand", title: "Understand the request", detail: "Model proposes", action: null, operator_note: null },
        { kind: "act", title: "Cancel", detail: "Run it", action: "cancel_order", operator_note: "Switched off by your team" },
      ],
      actions: ["cancel_order"], knowledge: ["cancellation-policy"], guidance_count: 2, usage_runs: 4, handoff_runs: 1, clarification_runs: 0, failed_runs: 0,
    };
    const html = renderToStaticMarkup(<ProcedureDetail procedure={procedure} onOpenAction={() => {}} onOpenGuidance={() => {}} onOpenKnowledge={() => {}} />);
    expect(html).toContain("cancel_order");
    expect(html).toContain("25%");
    expect(html).toContain("2 rules shape");
    expect(html).toContain("Switched off by your team");
  });

  it("saves drafts with PUT and the full configuration", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    setApiAuthProvider(createAuthProvider("local_demo", "token"));
    await initializeApiAuth();
    await studioApi.saveDraft({ guidance: [], actions: { cancel_order: "disabled" } }, "pause");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/ui/agent-config/draft");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ config: { guidance: [], actions: { cancel_order: "disabled" } }, note: "pause" });
  });
});
