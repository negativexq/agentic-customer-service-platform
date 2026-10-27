import type { AgentRun } from "../types";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ArchitectureOverview } from "./ArchitectureOverview";
import { OverviewDashboard } from "./OverviewDashboard";

describe("platform presentation surfaces", () => {
  it("keeps overview metrics bounded when no run projection is available", () => {
    const html = renderToStaticMarkup(<OverviewDashboard runs={[]} busy={false} onRunSafetyDemo={async () => undefined} onNavigate={() => undefined} />);
    expect(html).toContain("Agent Platform");
    expect(html).toContain("Explore the refund workflow");
    expect(html).toContain("LLM proposes, deterministic systems decide");
    expect(html).toContain("Not available");
    expect(html).toContain("RELEASE_GATE_PASS");
    expect(html).toContain("Historical evaluation artifacts");
    expect(html).toContain("Recorded demo fixtures are excluded");
    expect(html).toContain("Confirmation before mutation");
    expect(html).not.toContain("fake");
  });

  it("does not present recorded demo outcomes as runtime counts", () => {
    const demo = { run_id: "demo-refund", status: "completed" } as AgentRun;
    const html = renderToStaticMarkup(<OverviewDashboard runs={[demo]} busy={false} onRunSafetyDemo={async () => undefined} onNavigate={() => undefined} />);
    expect(html.match(/Not available/g)).toHaveLength(3);
  });

  it("renders the documented execution authority boundary", () => {
    const html = renderToStaticMarkup(<ArchitectureOverview />);
    expect(html).toContain("LLM is not the execution authority");
    expect(html).toContain("Provenance validation");
    expect(html).toContain("Policy and confirmation");
    expect(html).toContain("context enrichment");
    expect(html).toContain("Memory");
    expect(html).toContain("RAG evidence");
    expect(html).toContain("Evidence inputs");
    expect(html).toContain("Supporting information. Never execution authority.");
    expect(html).toContain("Decision");
    expect(html).toContain("Authority");
    expect(html).toContain("Why LLM is not the execution authority");
    expect(html).toContain("Valid refund request");
  });
});
