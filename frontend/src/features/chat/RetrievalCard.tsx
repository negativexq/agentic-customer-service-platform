import { BookOpen } from "lucide-react";
import type { AgentRun } from "../../types";
import { Badge, Card } from "../../components/ui";

export function RetrievalCard({ run }: { run: AgentRun }) {
  const documents = run.rag_documents;
  const retrieval = run.retrieval_metadata;
  if (documents.length === 0 && retrieval.retrieval_count === 0) return null;
  return (
    <Card className="p-4" data-testid="retrieval-card" aria-label="RAG retrieval activity">
      <div className="flex items-center gap-2"><BookOpen size={15} className="text-info" aria-hidden="true" /><span className="text-sm font-medium text-main">RAG retrieval</span><Badge tone={documents.length > 0 ? "success" : "neutral"}>{documents.length || retrieval.retrieval_count} source{(documents.length || retrieval.retrieval_count) === 1 ? "" : "s"}</Badge></div>
      {documents.length > 0 ? <div className="mt-3 space-y-2">{documents.slice(0, 3).map((document) => <div className="rounded-md border border-border bg-void/20 px-3 py-2" key={document.citation_id}><div className="flex items-center justify-between gap-2"><span className="text-xs font-medium text-main">{document.title}</span><span className="font-mono text-xs text-muted">{document.chunk_id ?? document.citation_id}</span></div><div className="mt-1 flex flex-wrap gap-2 text-xs text-muted"><span>{document.source}</span><span>score {document.score.toFixed(2)}</span>{document.grounding_status && <Badge tone="success">{document.grounding_status}</Badge>}</div></div>)}</div> : <p className="mt-3 text-xs text-muted">Retrieval ran, but document metadata is unavailable from the current operator projection.</p>}
      <p className="mt-3 text-xs leading-5 text-muted">Retrieved evidence supports grounding; it does not grant execution authority.</p>
    </Card>
  );
}
