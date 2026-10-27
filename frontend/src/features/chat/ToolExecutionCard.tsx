import { Wrench } from "lucide-react";
import type { ToolEvent } from "../../types";
import { Badge, Card } from "../../components/ui";

function projectedStatus(status: string): { label: string; tone: "success" | "warning" | "danger" } {
  if (status === "completed" || status === "executed") return { label: "Completed", tone: "success" };
  if (status === "failed_during_execution" || status === "failed") return { label: "Failed during execution", tone: "danger" };
  return { label: "Blocked before execution", tone: "warning" };
}

export function ToolExecutionCard({ tools }: { tools: ToolEvent[] }) {
  if (tools.length === 0) return null;
  return (
    <Card className="p-4" data-testid="tool-execution-card" aria-label="Tool execution activity">
      <div className="flex items-center gap-2"><Wrench size={15} className="text-warning" aria-hidden="true" /><span className="text-sm font-medium text-main">Tool calls</span><Badge tone="neutral">{tools.length}</Badge></div>
      <div className="mt-3 space-y-2">
        {tools.map((tool) => { const status = projectedStatus(tool.status); return <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-void/20 px-3 py-2 text-xs" key={`${tool.name}-${tool.status}`}><span className="font-mono text-main">{tool.name}</span><span className="flex items-center gap-2"><Badge tone={status.tone}>{status.label}</Badge><span className="text-muted">{tool.duration_ms.toFixed(1)} ms</span></span></div>; })}
      </div>
      <p className="mt-3 text-xs leading-5 text-muted">Tool arguments are intentionally omitted. Execution remains behind validation and authority checks.</p>
    </Card>
  );
}
