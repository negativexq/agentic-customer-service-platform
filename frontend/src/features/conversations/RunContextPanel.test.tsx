import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AgentRun } from "../../types";
import { RunContextView } from "./RunContextPanel";

const base = {
  rag_documents: [{ citation_id: "c1", title: "Refund policy", section: "Damaged items", source: "kb", score: 0.91, chunk_id: "refund-1", citation_preview: "Damaged items can be refunded within 30 days." }],
  memory: {
    item_count: 1, keys: ["response_style"], types: ["preference"],
    read_items: [{ key: "response_style", memory_type: "preference", content: "Prefers short answers", redaction_state: "not_required" }],
    written_item: { key: "contact", memory_type: "preference", content: "Contact via [REDACTED_EMAIL]", redaction_state: "redacted" },
    write_status: "persisted",
  },
} as unknown as AgentRun;

describe("RunContextPanel", () => {
  it("shows retrieved chunk text and memory read and written", () => {
    const html = renderToStaticMarkup(<RunContextView run={base} />);
    expect(html).toContain("Damaged items can be refunded within 30 days.");
    expect(html).toContain("Prefers short answers");
    expect(html).toContain("Contact via [REDACTED_EMAIL]");
    expect(html).toContain("Sensitive values masked");
  });

  it("explains when nothing was retrieved or stored", () => {
    const html = renderToStaticMarkup(<RunContextView run={{ ...base, rag_documents: [], memory: { item_count: 0, keys: [], types: [] } } as AgentRun} />);
    expect(html).toContain("No knowledge was retrieved");
    expect(html).toContain("No customer memory was loaded");
    expect(html).toContain("did not store anything");
  });

  it("shows live memory content and marks deleted memories without text", () => {
    const live = { ...base.memory, read_items: [{ memory_id: 3, key: "response_style", memory_type: "preference", content: null, status: "deleted" }], written_item: null };
    const html = renderToStaticMarkup(<RunContextView run={base} live={live} />);
    expect(html).toContain("This memory was deleted since the run.");
    expect(html).not.toContain("Prefers short answers");
  });
});
