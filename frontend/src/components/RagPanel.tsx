import { BookOpen, CheckCircle2, FileText, ShieldAlert } from "lucide-react";
import type { AnswerGrounding, RagDocument } from "../types";
import { Badge, EmptyState, Panel, SectionHeader } from "./ui";

type RagPanelProps = {
  documents: RagDocument[];
  grounding?: AnswerGrounding;
  retrievalRecorded?: boolean;
  embedded?: boolean;
};

const toneForGrounding = (grounding: AnswerGrounding) => {
  if (grounding.status === "pass" && grounding.accepted) return "success" as const;
  if (grounding.status === "conflict") return "warning" as const;
  if (grounding.status === "rejected") return "danger" as const;
  return "neutral" as const;
};

export function RagPanel({
  documents,
  grounding,
  retrievalRecorded = false,
  embedded = false,
}: RagPanelProps) {
  const emptyState = retrievalRecorded ? (
    <EmptyState
      title="Evidence unavailable from current projection"
      description="A retrieval stage was recorded, but no document metadata is available to this operator view."
      icon={BookOpen}
    />
  ) : (
    <EmptyState
      title="No retrieval stage recorded"
      description="This run contains no recorded RAG retrieval stage."
      icon={BookOpen}
    />
  );
  const groundingRecorded = grounding && grounding.status !== "not_recorded";
  const content = (
    <>
      <div className="context-role-header">
        <div className="flex flex-wrap items-center gap-2">
          <div className="eyebrow">RAG evidence</div>
          <Badge tone="success">KNOWLEDGE RETRIEVAL</Badge>
        </div>
        <p className="mt-1 text-xs leading-5 text-muted">
          External knowledge retrieval and citation-constrained answer grounding.
        </p>
      </div>
      {groundingRecorded ? (
        <div data-testid="grounding-status" className="mt-3 rounded-lg border border-subtle bg-surface-soft p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {grounding.accepted ? (
                <CheckCircle2 size={15} className="text-success" aria-hidden="true" />
              ) : (
                <ShieldAlert size={15} className="text-warning" aria-hidden="true" />
              )}
              <span className="text-sm font-medium text-main">Grounded answer validation</span>
            </div>
            <Badge tone={toneForGrounding(grounding)}>{grounding.status.replace(/_/g, " ")}</Badge>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            <div><dt className="text-muted">Sources used</dt><dd className="mt-1 font-medium text-main">{grounding.sources_used}</dd></div>
            <div><dt className="text-muted">Citations</dt><dd className="mt-1 font-medium text-main">{grounding.citation_count}</dd></div>
            <div><dt className="text-muted">Unsupported claims</dt><dd className="mt-1 font-medium text-main">{grounding.unsupported_claim_count}</dd></div>
            <div><dt className="text-muted">Confidence</dt><dd className="mt-1 font-medium text-main">{grounding.confidence === null ? "Not recorded" : `${Math.round(grounding.confidence * 100)}%`}</dd></div>
          </dl>
        </div>
      ) : null}
      {documents.length === 0 ? (
        emptyState
      ) : (
        <div className="mt-3 space-y-3">
          <div className="grounding-status">
            <CheckCircle2 size={15} className="text-success" aria-hidden="true" />
            <div>
              <div className="text-xs font-medium text-main">Evidence retrieved</div>
              <div className="mt-1 text-xs text-muted">
                Bounded document metadata supports grounding inspection; raw content is not exposed.
              </div>
            </div>
          </div>
          <div className="space-y-2">
            {documents.map((doc) => (
              <div className="source-row" key={doc.citation_id}>
                <div className="source-icon"><FileText size={15} aria-hidden="true" /></div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-main">{doc.title}</div>
                  <div className="mt-1 text-xs text-muted">Document section · {doc.section}</div>
                  <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-xs text-info">
                    <span>Citation [{doc.citation_id}]</span>
                    <span className="text-muted">Source {doc.source}</span>
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="field-label">Score</div>
                  <Badge tone="info">{doc.score.toFixed(2)}</Badge>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
  return embedded ? (
    <div data-testid="evidence-panel">{content}</div>
  ) : (
    <div data-testid="evidence-panel"><Panel title="Knowledge retrieval" eyebrow="Grounded evidence">
      <SectionHeader title="Retrieved sources" description="Document metadata only; raw knowledge content stays private." />
      {content}
    </Panel></div>
  );
}
