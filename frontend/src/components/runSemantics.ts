import type { AgentResponse, AgentRun } from "../types";

export type RunSemanticStatus =
  | "waiting_confirmation"
  | "completed"
  | "blocked"
  | "declined"
  | "failed"
  | "needs_input"
  | "suspended"
  | "replaced"
  | "replayed"
  | "recorded";

export type RunSemantics = {
  decision: string;
  executionStatus: string;
  authority: string;
  status: RunSemanticStatus;
  clarification: boolean;
  waiting: boolean;
  denied: boolean;
  executed: boolean;
};

function workflowStatus(run: AgentRun): "suspended" | "replaced" | undefined {
  const event = [...run.trace].reverse().find((item) => item.event_key?.startsWith("workflow."));
  const state = event?.metadata?.workflow_state;
  if (state === "suspended") return "suspended";
  if (state === "superseded") return "replaced";
  return undefined;
}

export function deriveRunSemantics(run: AgentRun): RunSemantics {
  const evidence = run.evidence ?? {};
  const projectedDecision = evidence.decision ?? "not_recorded";
  const clarification = projectedDecision === "clarification_required";
  const workflow = workflowStatus(run);
  const executionStatus = evidence.execution_status ?? "not_recorded";
  const authority = evidence.authority ?? "not_granted";
  const waiting = projectedDecision === "require_confirmation";
  // The backend reports a write that ran as "executed"; "completed" is kept for older projections.
  const executed = executionStatus === "executed" || executionStatus === "completed";
  const denied = projectedDecision === "deny";
  const failedValidation = projectedDecision === "validation_failed";

  let status: RunSemanticStatus = "recorded";
  if (workflow) status = workflow;
  else if (clarification) status = "needs_input";
  else if (waiting) status = "waiting_confirmation";
  else if (projectedDecision === "already_completed" || executionStatus === "not_repeated") status = "replayed";
  else if (executed) status = "completed";
  // A system fault, a safeguard that stopped the request, or a request that could not be done as asked.
  else if (run.status === "error") status = "failed";
  else if (run.status === "declined" || failedValidation) status = "declined";
  else if (denied || run.status === "blocked") status = "blocked";
  // Reads and answers write nothing ("not_applicable"), so the run's own outcome is the result.
  else if (run.status === "completed") status = "completed";

  return {
    decision: projectedDecision,
    executionStatus,
    authority,
    status,
    clarification,
    waiting,
    denied,
    executed,
  };
}

export function responseState(response: AgentResponse): string {
  const pendingStatus = response.pending_action?.status;
  if (pendingStatus === "pending" || pendingStatus === "confirmed") return "awaiting confirmation";
  if (pendingStatus === "rejected" || pendingStatus === "expired" || pendingStatus === "failed") return "blocked";
  if (response.tool_call?.status === "executed" || response.tool_call?.status === "completed") return "completed";
  if (response.error_category === "invalid_tool_arguments") return "needs input";
  if (response.error_category === "policy_denied" || response.error_category === "duplicate_action") return "blocked";
  if (response.error_category) return "contained";
  return "decision recorded";
}
