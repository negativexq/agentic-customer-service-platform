import { describe, expect, it } from "vitest";
import type { AgentRun } from "../../types";
import { runStatus } from "./runPresentation";

const base = {
  run_id: "r", request_id: "q", conversation_id: "c", action_id: null, customer_id: 1, intent: "refund_request", request_type: "action",
  status: "completed", started_at: "2026-11-23T10:00:00Z", duration_ms: 10, trace_id: null, path: [], failure_category: null, degraded_components: [],
  recovery_action: null, memory: { item_count: 0, keys: [], types: [] }, tools: [], policy: [], rag_documents: [],
  retrieval_metadata: { backend: "none", embedding_provider: "none", reranker_enabled: false, retrieval_count: 0, latency_seconds: 0, fallback_status: "none", hybrid: false, fusion_strategy: "none", dense_candidate_count: 0, sparse_candidate_count: 0 },
  decision_reason: null, evidence: {}, trace: [],
} as unknown as AgentRun;

const withStatus = (status: string, decision?: string) => ({ ...base, status, evidence: decision ? { decision } : {} }) as AgentRun;

describe("run status labels", () => {
  it("keeps red for system failures only", () => {
    expect(runStatus(withStatus("error"))).toEqual({ label: "Failed", tone: "danger" });
    expect(runStatus(withStatus("blocked", "deny"))).toEqual({ label: "Blocked", tone: "warning" });
    expect(runStatus(withStatus("declined", "validation_failed"))).toEqual({ label: "Declined", tone: "neutral" });
  });

  it("reads a memory refusal as declined even though its decision says deny", () => {
    expect(runStatus(withStatus("declined", "deny")).label).toBe("Declined");
  });
});
