import { Activity, Clock3, MessageSquare, ShieldAlert, UserRound } from "lucide-react";
import type { AgentRun } from "../types";
import { Badge, EmptyState, StatusIndicator } from "./ui";

type Props = {
  runs: AgentRun[];
  selectedRunId: string | null;
  currentConversationId: string;
  onSelect: (run: AgentRun) => void;
};

function runStatus(status: string): { label: string; tone: "success" | "warning" | "danger" | "info" | "neutral" } {
  if (status === "waiting_confirmation") return { label: "Awaiting confirmation", tone: "warning" };
  if (status === "completed") return { label: "Completed", tone: "success" };
  if (status === "error") return { label: "Escalated", tone: "danger" };
  return { label: status.replace(/_/g, " "), tone: "info" };
}

export function WorkspaceRail({ runs, selectedRunId, currentConversationId, onSelect }: Props) {
  return (
    <aside className="workspace-rail surface" aria-label="Conversation list">
      <div className="border-b border-border p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="eyebrow">Operator queue</div>
            <h2 className="section-title mt-1">Conversations</h2>
          </div>
          <Badge tone="neutral">{runs.length} recent</Badge>
        </div>
        <p className="mt-2 text-xs leading-5 text-muted">Persisted run projections, not message history.</p>
      </div>
      <div className="space-y-2 p-2">
        <div className="rail-session rounded-lg border border-info/30 bg-info/5 p-3" aria-current="true">
          <div className="flex items-start gap-2.5">
            <div className="rail-icon rail-icon-active"><Activity size={14} aria-hidden="true" /></div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-main">Current workspace</span>
                <StatusIndicator label="Active" tone="success" compact />
              </div>
              <div className="mt-1 truncate font-mono text-xs text-muted">{currentConversationId}</div>
            </div>
          </div>
        </div>
        {runs.length === 0 ? (
          <EmptyState title="No persisted runs" description="A completed request will appear here with its bounded operator metadata." icon={MessageSquare} />
        ) : runs.map((run) => {
          const status = runStatus(run.status);
          const selected = run.run_id === selectedRunId;
          return (
            <button
              type="button"
              key={run.run_id}
              className={`rail-item w-full text-left ${selected ? "rail-item-selected" : ""}`}
              onClick={() => onSelect(run)}
              aria-pressed={selected}
            >
              <div className="flex items-start gap-2.5">
                <div className="rail-icon"><UserRound size={14} aria-hidden="true" /></div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <span className="truncate text-xs font-semibold text-main">Customer #{run.customer_id}</span>
                    <span className="shrink-0 text-xs text-muted">{new Date(run.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                  </div>
                  <div className="mt-1 truncate font-mono text-xs text-muted">{run.conversation_id}</div>
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <span className="truncate text-xs text-muted">{run.intent || "Intent not recorded"}</span>
                    <StatusIndicator label={status.label} tone={status.tone} compact />
                  </div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
      <div className="mt-auto border-t border-border p-4 text-xs leading-5 text-muted">
        <div className="flex items-center gap-2"><Clock3 size={13} aria-hidden="true" /> bounded operational metadata</div>
        <div className="mt-2 flex items-center gap-2"><ShieldAlert size={13} className="text-warning" aria-hidden="true" /> proposal ≠ authority</div>
      </div>
    </aside>
  );
}
