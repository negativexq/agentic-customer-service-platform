import { BookOpen, Brain, PenLine } from "lucide-react";
import type { ReactNode } from "react";
import { useRunMemory } from "@/lib/queries";
import type { AgentRun, MemoryItem, MemoryUsage } from "../../types";
import { humanize } from "../runs/runPresentation";

function Section({ icon, title, count, children }: { icon: ReactNode; title: string; count?: number; children: ReactNode }) {
  return (
    <section className="grid gap-2">
      <h3 className="flex items-center gap-2 text-sm font-medium text-foreground">
        <span className="text-muted-foreground" aria-hidden="true">{icon}</span>
        {title}
        {count !== undefined && <span className="text-xs font-normal tabular-nums text-muted-foreground">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">{children}</p>;
}

function MemoryCard({ item }: { item: MemoryItem }) {
  return (
    <li className="grid gap-1.5 rounded-lg border border-border bg-background px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">{humanize(item.memory_type)}</span>
        <span className="font-mono text-muted-foreground">{item.key}</span>
        {item.redaction_state === "redacted" && <span className="text-warning">Sensitive values masked</span>}
      </div>
      {item.content
        ? <p className="text-sm leading-6 text-foreground">{item.content}</p>
        : <p className="text-xs text-muted-foreground">{memoryUnavailable(item.status)}</p>}
    </li>
  );
}

function memoryUnavailable(status?: string | null): string {
  if (status === "deleted") return "This memory was deleted since the run.";
  if (status === "expired") return "This memory has expired.";
  if (status === "loading") return "Loading…";
  return "This memory is no longer available.";
}

/** What the agent actually used: knowledge chunks it retrieved and memories it read or stored. */
export function RunContextPanel({ run }: { run: AgentRun }) {
  const resolved = useRunMemory(run.run_id);
  return <RunContextView run={run} live={resolved.data} loading={resolved.isLoading} />;
}

export function RunContextView({ run, live, loading }: { run: AgentRun; live?: MemoryUsage; loading?: boolean }) {
  const pending = (item: MemoryItem): MemoryItem => ({ ...item, status: loading ? "loading" : item.status });
  const read = (live?.read_items ?? run.memory.read_items ?? []).map((item) => (live ? item : pending(item)));
  const writtenSource = live ? live.written_item : run.memory.written_item;
  const written = writtenSource ? (live ? writtenSource : pending(writtenSource)) : null;
  return (
    <div className="grid gap-6" data-testid="run-context">
      <Section icon={<BookOpen className="size-4" />} title="Retrieved from the knowledge base" count={run.rag_documents.length}>
        {run.rag_documents.length === 0 ? <Empty>No knowledge was retrieved for this turn.</Empty> : (
          <ol className="grid gap-2">
            {run.rag_documents.map((doc, index) => (
              <li key={`${doc.citation_id}-${index}`} className="grid gap-1.5 rounded-lg border border-border bg-background px-3 py-2.5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-sm font-medium text-foreground">{doc.title}<span className="font-normal text-muted-foreground"> · {doc.section}</span></span>
                  <span className="font-mono text-xs tabular-nums text-muted-foreground">score {doc.score.toFixed(3)}</span>
                </div>
                {doc.citation_preview
                  ? <blockquote className="border-l-2 border-border pl-3 text-sm leading-6 text-muted-foreground">{doc.citation_preview}</blockquote>
                  : <p className="text-xs text-muted-foreground">Chunk text was not recorded for this run.</p>}
                <span className="truncate font-mono text-[11px] text-muted-foreground">{doc.chunk_id ?? doc.citation_id}{doc.document_version ? ` · v${doc.document_version}` : ""}</span>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section icon={<Brain className="size-4" />} title="Memory read" count={read.length || run.memory.item_count}>
        {read.length > 0 ? <ul className="grid gap-2">{read.map((item) => <MemoryCard key={item.memory_id ?? item.key} item={item} />)}</ul>
          : run.memory.item_count > 0 ? <Empty>{run.memory.item_count} memories were used; their content was not recorded for this run.</Empty>
          : <Empty>No customer memory was loaded.</Empty>}
      </Section>

      <Section icon={<PenLine className="size-4" />} title="Memory written">
        {written ? (
          <ul className="grid gap-2"><MemoryCard item={written} /></ul>
        ) : (
          <Empty>{run.memory.write_status ? `Nothing stored (${humanize(run.memory.write_status)}).` : "This turn did not store anything in memory."}</Empty>
        )}
      </Section>
    </div>
  );
}
