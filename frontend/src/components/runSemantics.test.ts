import { describe, expect, it } from "vitest";
import type { AgentRun } from "../types";
import { deriveRunSemantics } from "./runSemantics";

const run = (overrides: Partial<AgentRun> = {}): AgentRun => ({
  run_id: "run-test",
  request_id: "request-test",
  conversation_id: "conversation-test",
  action_id: null,
  customer_id: 1,
  intent: "refund_request",
  request_type: "write_action",
  status: "completed",
  started_at: "2026-08-24T00:00:00Z",
  duration_ms: 10,
  trace_id: "trace-test",
  path: [],
  failure_category: null,
  degraded_components: [],
  recovery_action: null,
  memory: { item_count: 0, keys: [], types: [] },
  tools: [],
  policy: [],
  rag_documents: [],
  retrieval_metadata: { backend: "test", embedding_provider: "test", reranker_enabled: false, retrieval_count: 0, latency_seconds: 0, fallback_status: "none", hybrid: false, fusion_strategy: "none", dense_candidate_count: 0, sparse_candidate_count: 0 },
  trace: [],
  decision_reason: null,
  evidence: { decision: "require_confirmation", validation_stage: "policy_evaluation", execution_status: "blocked", authority: "confirmation_required", grounding: { status: "not_recorded", reference_type: null, trusted_source: null }, compiler: { status: "passed", selected_tool: "request_refund", requires_retrieval: false, reason: null }, target_validation: { status: "validated" }, confirmation: { status: "pending", required: true, action_id: "action-test", risk_level: 2 }, write_outcome: { status: "pending_confirmation" } },
  ...overrides,
});

describe("run semantic projection", () => {
  it("derives awaiting confirmation from the final decision", () => {
    expect(deriveRunSemantics(run()).status).toBe("waiting_confirmation");
    expect(deriveRunSemantics(run()).authority).toBe("confirmation_required");
  });

  it("does not retain a waiting badge after denial", () => {
    const denied = run({
      evidence: { ...run().evidence, decision: "deny", execution_status: "not_attempted", authority: "not_granted", write_outcome: { status: "blocked" } },
      policy: [{ action_id: null, tool_name: "request_refund", risk_level: 2, outcome: "deny", reason_codes: [] }],
    });
    expect(deriveRunSemantics(denied).status).toBe("blocked");
    expect(deriveRunSemantics(denied).waiting).toBe(false);
    expect(deriveRunSemantics(denied).authority).toBe("not_granted");
  });

  it("treats a completed read as read access, not mutation authority", () => {
    const read = run({
      intent: "order_lookup",
      request_type: "read_action",
      // As the backend projects a read: nothing is written, so the write status is not_applicable.
      evidence: { ...run().evidence, decision: "allow", execution_status: "not_applicable", authority: "read_access", write_outcome: { status: "not_applicable" } },
      tools: [{ name: "get_order", risk_level: 0, status: "completed", duration_ms: 2, result_fields: ["status"] }],
    });
    expect(deriveRunSemantics(read).status).toBe("completed");
    expect(deriveRunSemantics(read).executed).toBe(false);
    expect(deriveRunSemantics(read).authority).toBe("read_access");
  });

  it("treats a confirmed write the backend reports as executed as completed", () => {
    const refund = run({
      evidence: { ...run().evidence, decision: "allow", execution_status: "executed", authority: "controlled_execution", confirmation: { status: "executed", required: true, action_id: "action-test", risk_level: 2 }, write_outcome: { status: "executed" } },
      tools: [{ name: "request_refund", risk_level: 2, status: "completed", duration_ms: 5, result_fields: ["status"] }],
    });
    expect(deriveRunSemantics(refund).status).toBe("completed");
    expect(deriveRunSemantics(refund).executed).toBe(true);
    expect(deriveRunSemantics(refund).authority).toBe("controlled_execution");
  });

  it("does not call a failed run completed", () => {
    const failed = run({ status: "error", evidence: { ...run().evidence, decision: "allow", execution_status: "failed", authority: "not_granted", write_outcome: { status: "failed" } } });
    expect(deriveRunSemantics(failed).status).toBe("failed");
  });
});
