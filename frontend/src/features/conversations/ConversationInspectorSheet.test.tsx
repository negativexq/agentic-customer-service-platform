import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AgentRun } from "../../types";
import { ConversationDebugView } from "./ConversationInspectorSheet";

function run(id: string, startedAt: string, extra: Partial<AgentRun>): AgentRun {
  return {
    run_id: id, started_at: startedAt, intent: "faq", status: "completed", failure_category: null, duration_ms: 900, tools: [], rag_documents: [],
    memory: { item_count: 0, keys: [], types: [] }, provider_metadata: { provider: "openai", model: "m", latency_ms: 800, input_tokens: 100, output_tokens: 20, cost_usd: null },
    trace: [], policy: [], proposal: null,
    ...extra,
  } as unknown as AgentRun;
}

describe("ConversationDebugView", () => {
  it("summarises every turn with the knowledge and memory it used", () => {
    const runs = [
      run("r2", "2026-11-22T10:01:00", { intent: "refund_request", memory: { item_count: 0, keys: [], types: [], written_item: { key: "contact", memory_type: "preference" } } }),
      run("r1", "2026-11-22T10:00:00", { rag_documents: [{ citation_id: "c1", chunk_id: "k1", title: "Refund policy", section: "Damaged items", source: "kb", score: 0.91 }], memory: { item_count: 1, keys: ["style"], types: ["preference"], read_items: [{ key: "style", memory_type: "preference" }] } }),
    ];
    const html = renderToStaticMarkup(<ConversationDebugView runs={runs} onOpenRun={() => {}} />);
    expect(html).toContain("AI turns");
    expect(html).toContain("240");
    expect(html.indexOf("Faq")).toBeLessThan(html.indexOf("Refund request"));
    expect(html).toContain("Refund policy");
    expect(html).toContain("turn 1");
    expect(html).toContain("contact");
  });

  it("explains an empty conversation", () => {
    expect(renderToStaticMarkup(<ConversationDebugView runs={[]} onOpenRun={() => {}} />)).toContain("has not run in this conversation yet");
  });
});
