import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/primitives/tooltip";
import type { AgentRun } from "../../types";
import { decisionPath, toneDot, toneText, type Tone } from "./runPresentation";

const segment: Record<Tone, string> = {
  success: "bg-success/70",
  warning: "bg-warning/80",
  danger: "bg-danger/80",
  info: "bg-info/70",
  neutral: "bg-border-strong",
};

/** Four segments that show how far a run travelled from model proposal to business outcome. */
export function DecisionPath({ run, expanded = false }: { run: AgentRun; expanded?: boolean }) {
  const steps = decisionPath(run);
  const summary = steps.map((step) => `${step.stage}: ${step.value}`).join(". ");
  if (expanded) {
    return (
      <ol className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-4" aria-label="Decision path">
        {steps.map((step) => (
          <li key={step.stage} className="flex min-w-0 flex-col gap-1 bg-card px-3 py-2.5">
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={`size-1.5 rounded-full ${toneDot[step.tone]}`} aria-hidden="true" />
              {step.stage}
            </span>
            <span className={`truncate text-sm font-medium ${step.tone === "neutral" ? "text-foreground" : toneText[step.tone]}`}>{step.value}</span>
          </li>
        ))}
      </ol>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex items-center gap-0.5 py-1" aria-label={summary} role="img" />}>
        {steps.map((step) => <span key={step.stage} className={`h-1.5 w-5 rounded-full ${segment[step.tone]}`} />)}
      </TooltipTrigger>
      <TooltipContent className="grid gap-0.5 text-left">
        {steps.map((step) => <span key={step.stage}><span className="opacity-70">{step.stage}</span> {step.value}</span>)}
      </TooltipContent>
    </Tooltip>
  );
}
