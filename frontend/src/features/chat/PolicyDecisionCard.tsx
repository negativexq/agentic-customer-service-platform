import { ShieldCheck } from "lucide-react";
import type { AgentRun } from "../../types";
import { Badge, Card } from "../../components/ui";

function decisionTone(outcome: string): "success" | "warning" | "danger" | "neutral" {
  if (outcome.includes("deny") || outcome.includes("reject")) return "danger";
  if (outcome.includes("confirm") || outcome.includes("pending")) return "warning";
  if (outcome.includes("allow") || outcome.includes("pass")) return "success";
  return "neutral";
}

export function PolicyDecisionCard({ run }: { run: AgentRun }) {
  const decision = run.evidence.decision ?? "not_recorded";
  const reason = run.evidence.reason ?? run.decision_reason ?? "Reason not recorded in the operator projection.";
  const validationStage = run.evidence.validation_stage ?? "not_recorded";
  const executionStatus = run.evidence.execution_status ?? "not_attempted";
  return (
    <Card className="p-4" data-testid="policy-decision-card" aria-label="Decision and validation activity">
      <div className="flex items-center gap-2"><ShieldCheck size={15} className="text-warning" aria-hidden="true" /><span className="text-sm font-medium text-main">Decision and validation</span><Badge tone={decisionTone(decision)}>{decision.replace(/_/g, " ")}</Badge></div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2"><div><span className="field-label">Decision</span><div className="mt-1 text-xs text-main">{decision.replace(/_/g, " ")}</div></div><div><span className="field-label">Validation stage</span><div className="mt-1 text-xs text-main">{validationStage.replace(/_/g, " ")}</div></div><div><span className="field-label">Execution status</span><div className="mt-1 text-xs text-main">{executionStatus.replace(/_/g, " ")}</div></div><div><span className="field-label">Confirmation</span><div className="mt-1 text-xs text-main">{run.evidence.confirmation.required ? "Required" : run.evidence.confirmation.status.replace(/_/g, " ")}</div></div></div>
      <div className="mt-3 border-l-2 border-warning/60 pl-3"><span className="field-label">Reason</span><p className="mt-1 text-xs leading-5 text-muted">{reason}</p></div>
      <p className="mt-3 text-xs leading-5 text-muted">Business validation and policy outcomes are projected separately. Model output remains a proposal.</p>
    </Card>
  );
}
