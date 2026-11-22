import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAuthProvider } from "../../auth/provider";
import { initializeApiAuth, setApiAuthProvider } from "../../api/client";
import { coachingSeed } from "./coaching";
import { GapCard } from "./OptimizePage";
import { PerformanceSummaryStrip } from "./PerformancePage";
import { customMonitorId, FlagRow } from "./QualityPage";
import { improveApi } from "./queries";
import { ResultRow, ScenarioPicker } from "./SimulationsPage";
import type { Flag, Gap, Scenario } from "./types";

const noop = () => {};
afterEach(() => { vi.unstubAllGlobals(); setApiAuthProvider(createAuthProvider("local_demo", null)); });

describe("improve loop", () => {
  it("summarises resolution, handoff and CSAT", () => {
    const html = renderToStaticMarkup(<PerformanceSummaryStrip summary={{
      window_days: 30, conversations: 10, ai_resolved: 7, handed_off: 3, ai_resolution_rate: 0.7, handoff_rate: 0.3,
      csat_average: 4.25, csat_responses: 4, csat_ai_average: 4.5, csat_human_average: null, ai_runs: 12, failed_runs: 1,
    }} />);
    expect(html).toContain("70%");
    expect(html).toContain("30%");
    expect(html).toContain("4.3");
    expect(html).toContain("4.5 / —");
  });

  it("shows a gap with its examples and the fix that resolves it", () => {
    const gap: Gap = {
      kind: "content", key: "content:shipping_policy", title: "No answer in knowledge", description: "Searched but found nothing", intent: "shipping_policy",
      count: 4, fix: "knowledge", examples: [{ question: "Do you ship to Iceland?", conversation_pk: 3, run_id: "run-1", at: new Date().toISOString() }],
    };
    const html = renderToStaticMarkup(<GapCard gap={gap} onFix={noop} onCoach={noop} onOpenConversation={noop} onOpenRun={noop} />);
    expect(html).toContain("Do you ship to Iceland?");
    expect(html).toContain("Add knowledge");
    expect(html).toContain("Coach the assistant");
    expect(html).toContain("4×");
  });

  it("offers review actions only for open flags", () => {
    const flag: Flag = {
      monitor_key: "frustration", monitor_name: "Frustration", severity: "high", conversation_pk: 1, conversation_id: "c-1", customer_id: 1,
      customer_name: "Ada", detail: "Customer sounded frustrated", excerpt: "this is ridiculous", at: new Date().toISOString(), status: "open", note: "", run_id: null, intent: null,
    };
    const open = renderToStaticMarkup(<FlagRow flag={flag} busy={false} onReview={noop} onCoach={noop} onOpenConversation={noop} />);
    expect(open).toContain("Mark reviewed");
    expect(open).not.toContain("Reopen");
    const done = renderToStaticMarkup(<FlagRow flag={{ ...flag, status: "dismissed" }} busy={false} onReview={noop} onCoach={noop} onOpenConversation={noop} />);
    expect(done).toContain("Reopen");
    expect(done).toContain("Dismissed");
  });

  it("only custom monitors are editable", () => {
    expect(customMonitorId("custom:7")).toBe(7);
    expect(customMonitorId("frustration")).toBeNull();
  });

  it("turns an observation into a draft guidance rule", () => {
    const seed = coachingSeed({ title: "Refunds", context: "a customer asks about refunds", intent: "refund_policy", runId: "run-9" });
    expect(seed.intents).toEqual(["refund_policy"]);
    expect(seed.source_run_id).toBe("run-9");
    expect(seed.instruction).toContain("a customer asks about refunds");
    expect(coachingSeed({ title: "x", context: "y", intent: "not_an_intent" }).intents).toEqual([]);
  });

  it("lists scenarios with safety markers and a transcript per result", () => {
    const scenarios: Scenario[] = [{ id: "refund-1", category: "refund", customer_id: 1, turns: ["I want a refund"], expected_intent: "refund_request", completion: "complete", expected_tools: [], forbidden_tools: [], critical_safety: true }];
    const picker = renderToStaticMarkup(<ScenarioPicker scenarios={scenarios} selected={new Set(["refund-1"])} onChange={noop} />);
    expect(picker).toContain("refund-1");
    expect(picker).toContain("Safety");
    const row = renderToStaticMarkup(<ul><ResultRow open onToggle={noop} result={{
      scenario_id: "refund-1", category: "refund", passed: false, critical_safety: true, failure_reasons: ["expected request_refund"],
      selected_tools: [], executed_tools: [], latency_ms: 4, transcript: [{ user: "I want a refund", assistant: "Which order?", intent: "refund_request", tool: null, status: "needs_input" }],
    }} /></ul>);
    expect(row).toContain("expected request_refund");
    expect(row).toContain("Which order?");
  });

  it("starts a simulation against the draft", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 1 }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    setApiAuthProvider(createAuthProvider("local_demo", "token"));
    await initializeApiAuth();
    await improveApi.simulate(["refund-1"], "draft", "try");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/ui/simulations");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ scenario_ids: ["refund-1"], against: "draft", label: "try" });
  });
});
