import { Brain } from "lucide-react";
import type { MemoryRecord, MemoryUsage } from "../types";
import { Badge, EmptyState, Panel, SectionHeader, StatusIndicator } from "./ui";

function formatTimestamp(value: string | null): string {
  return value ? new Date(value).toISOString() : "no expiration";
}

function memoryCount(usage: MemoryUsage): number {
  return usage.retrieved_count ?? usage.items_used ?? usage.item_count;
}

function memoryLabel(value: string | undefined, fallback: string): string {
  return value ? value.replace(/_/g, " ") : fallback;
}

function memoryPurpose(value: string | undefined, retrieved: boolean): string {
  if (!retrieved) return "Not used";
  if (value === "context_enrichment") return "Customer preference enrichment";
  return memoryLabel(value, "Not recorded");
}

export function MemoryPanel({
  usage,
  records,
  embedded = false,
}: {
  usage: MemoryUsage;
  records: MemoryRecord[];
  embedded?: boolean;
}) {
  const retrievedCount = memoryCount(usage);
  const retrieved = usage.retrieved ?? retrievedCount > 0;
  const purpose = memoryPurpose(usage.purpose ?? usage.context_usage, retrieved);
  const decisionImpact = memoryLabel(usage.decision_influence, retrieved ? "context only" : "not used");
  const authorityImpact = usage.authority_influence === "none" ? "No impact" : memoryLabel(usage.authority_influence, "Not applicable");
  const content = (
    <>
      <div className="notice notice-info">
        <Brain size={15} aria-hidden="true" />
        <span>
          Memory can enrich context but cannot authorize actions or bypass validation and
          confirmation. Memory content is intentionally hidden from the operator projection.
        </span>
      </div>
      <div className="mt-4">
        <div className="context-role-header">
          <div className="flex flex-wrap items-center gap-2">
            <div className="eyebrow">Memory context</div>
            <Badge tone="info">CUSTOMER STATE</Badge>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted">User-specific preferences and bounded context for contextual continuity.</p>
          <div className="mt-2 text-xs text-muted">{retrievedCount} item{retrievedCount === 1 ? "" : "s"} used in run</div>
        </div>
        <div className="context-summary mt-3">
          <div><span className="field-label">Retrieved</span><div className="mt-1 text-xs text-main">{retrieved ? `✓ ${retrievedCount}` : "Not used"}</div></div>
          <div><span className="field-label">Why memory was loaded</span><div className="mt-1 text-xs text-main">{purpose}</div></div>
          <div><span className="field-label">Used by</span><div className="mt-1 text-xs text-main">{retrieved ? "Context assembly" : "Not used"}</div></div>
          <div><span className="field-label">Decision impact</span><div className="mt-1 text-xs capitalize text-main">{decisionImpact}</div></div>
          <div><span className="field-label">Execution authority</span><div className="mt-1 text-xs text-main">{authorityImpact}</div></div>
        </div>
        <div className="memory-role-card mt-3">
          <div><span className="field-label">Memory role</span><div className="mt-1 text-sm font-medium text-main">Context enrichment only</div></div>
          <div className="memory-role-grid mt-3"><div><span className="field-label">Used for</span><div className="mt-1 text-xs text-muted">Personalization · contextual continuity</div></div><div><span className="field-label">Not used for</span><div className="mt-1 text-xs text-muted">Authorization · policy override · execution authority · confirmation bypass</div></div></div>
        </div>
        <div className="context-summary mt-3"><div><span className="field-label">Retrieved keys</span><div className="mt-1 text-xs text-main">{usage.keys.length ? usage.keys.join(" · ") : "Not recorded"}</div></div><div><span className="field-label">Context types</span><div className="mt-1 text-xs text-main">{usage.types.length ? usage.types.join(" · ") : "Not recorded"}</div></div><div><span className="field-label">Previous interactions</span><div className="mt-1 text-xs text-muted">Unavailable in current projection</div></div></div>
        {records.length === 0 ? (
          <EmptyState
            title="No persistent memory found"
            description="This customer has no visible active memory records."
            icon={Brain}
          />
        ) : (
          <div className="mt-3 space-y-2">
            {records.map((record) => (
              <div className="memory-row" key={record.id}>
                <div>
                  <div className="text-sm font-medium text-main">
                    {record.memory_type.replace(/_/g, " ")}
                  </div>
                  <div className="mt-1 font-mono text-xs text-muted">
                    {record.normalized_key}
                  </div>
                  <div className="mt-1 text-xs text-muted">Source · {record.source.replace(/_/g, " ")} · provenance recorded</div>
                  <div className="mt-1 text-xs text-muted">
                    Created {formatTimestamp(record.created_at)} · updated{" "}
                    {formatTimestamp(record.updated_at)} · expires {formatTimestamp(record.expires_at)}
                  </div>
                </div>
                <StatusIndicator label={record.status} tone="success" compact />
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
  return embedded ? (
    <div>{content}</div>
  ) : (
    <Panel title="Customer memory" eyebrow="Contextual evidence">
      <SectionHeader
        title="Personalization context"
        description="Customer-scoped metadata approved by the memory policy."
      />
      {content}
    </Panel>
  );
}
