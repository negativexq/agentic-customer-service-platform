import { Archive, ArchiveRestore, Database, Pencil, Plus, RotateCcw, Search, Upload } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/primitives/button";
import { Input } from "@/components/primitives/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { Skeleton } from "@/components/primitives/skeleton";
import { Textarea } from "@/components/primitives/textarea";
import { PageHeader } from "../../components/layout/PageHeader";
import { humanize } from "../runs/runPresentation";
import { when } from "./DraftBar";
import { Markdown } from "./Markdown";
import { studioApi, useKnowledge, useKnowledgeDocument, useKnowledgeMutation, useKnowledgeStatus } from "./queries";
import type { KnowledgeDocument, KnowledgeDocumentDetail, KnowledgeSearchResult } from "./types";

function IndexStatusBar() {
  const status = useKnowledgeStatus();
  const publish = useKnowledgeMutation(() => studioApi.publishKnowledge());
  const data = status.data;
  if (!data) return null;
  const upToDate = !data.pending_changes;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-muted/30 px-4 py-2.5 text-sm sm:px-6" data-testid="knowledge-index-status">
      <span className="inline-flex items-center gap-2 text-foreground">
        <span className={`size-1.5 rounded-full ${upToDate ? "bg-success" : "bg-warning"}`} aria-hidden="true" />
        {upToDate ? "Live search index is up to date" : "Changes are not in the live search index yet"}
      </span>
      <span className="text-muted-foreground">
        <Database className="mr-1 inline size-3.5 align-[-2px]" aria-hidden="true" />
        {data.active_chunk_count ?? 0} live passages · {data.corpus_chunk_count} in the library · {humanize(data.backend)} index
      </span>
      {!upToDate && (
        data.publish_allowed
          ? <Button size="sm" className="ml-auto" disabled={publish.isPending} onClick={() => publish.mutate(undefined)}><Upload />Publish to index</Button>
          : <span className="ml-auto text-xs text-muted-foreground">{data.publish_blocked_reason}</span>
      )}
      {publish.error && <p role="alert" className="basis-full text-xs text-danger">{publish.error.message}</p>}
    </div>
  );
}

function SearchPreview({ open, onClose, onOpenDocument }: { open: boolean; onClose: () => void; onOpenDocument: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const search = useKnowledgeMutation((text: string) => studioApi.searchKnowledge(text));
  const result: KnowledgeSearchResult | undefined = search.data;
  return (
    <Sheet open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <SheetContent side="right" className="w-full gap-0 p-0 data-[side=right]:sm:max-w-xl" data-testid="knowledge-search">
        <SheetHeader className="border-b border-border px-5 py-4 pr-12">
          <SheetTitle>Test retrieval</SheetTitle>
          <SheetDescription>See which passages a customer question would retrieve from the current library, including unpublished edits. Runs locally, without paid embeddings.</SheetDescription>
        </SheetHeader>
        <form className="flex gap-2 border-b border-border px-5 py-3" onSubmit={(event) => { event.preventDefault(); if (query.trim()) search.mutate(query.trim()); }}>
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="How long do refunds take?" aria-label="Customer question" autoFocus />
          <Button type="submit" disabled={!query.trim() || search.isPending}><Search />Search</Button>
        </form>
        <div className="grid content-start gap-3 overflow-y-auto p-5">
          {search.error && <p role="alert" className="text-sm text-danger">{search.error.message}</p>}
          {result && <p className="text-xs text-muted-foreground">Ranked with free local embeddings over the current library. The live index may order results differently.</p>}
          {result && result.hits.length === 0 && <p className="text-sm text-muted-foreground">Nothing in the library matches. The assistant would say it cannot find an answer; add an article to fill the gap.</p>}
          {result?.hits.map((hit, index) => (
            <article key={hit.chunk_id} className="grid gap-1.5 rounded-lg border border-border p-3">
              <div className="flex items-baseline justify-between gap-3">
                <button type="button" onClick={() => onOpenDocument(hit.document_id)} className="truncate text-left text-sm font-medium text-foreground hover:underline">
                  {index + 1}. {hit.title}<span className="font-normal text-muted-foreground"> · {hit.section}</span>
                </button>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">score {hit.score.toFixed(2)}</span>
              </div>
              <p className="line-clamp-4 text-sm whitespace-pre-line text-muted-foreground">{hit.content}</p>
            </article>
          ))}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function ArticleEditor({ initial, saving, error, onSave, onCancel }: { initial: { title: string; content: string }; saving: boolean; error: Error | null; onSave: (title: string, content: string) => void; onCancel: () => void }) {
  const [title, setTitle] = useState(initial.title);
  const [content, setContent] = useState(initial.content);
  const valid = title.trim() && content.trim();
  return (
    <form className="grid gap-3" onSubmit={(event) => { event.preventDefault(); if (valid) onSave(title.trim(), content); }}>
      <Input value={title} onChange={(event) => setTitle(event.target.value)} aria-label="Article title" placeholder="Article title" maxLength={200} autoFocus />
      <Textarea value={content} onChange={(event) => setContent(event.target.value)} aria-label="Article content" rows={22} className="font-mono text-xs leading-relaxed"
        placeholder={"## Section heading\n\nWrite the policy in short paragraphs. Each section becomes passages the assistant can cite."} />
      <p className="text-xs text-muted-foreground">Markdown. Start each topic with a “##” heading so passages are cited by section.</p>
      {error && <p role="alert" className="text-sm text-danger">{error.message}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button type="submit" disabled={!valid || saving}>Save</Button>
      </div>
    </form>
  );
}

function DocumentView({ summary, detail }: { summary: KnowledgeDocument; detail: KnowledgeDocumentDetail }) {
  const [editing, setEditing] = useState(false);
  const save = useKnowledgeMutation((input: { title: string; content: string }) => studioApi.saveArticle(detail.document_id, input.title, input.content));
  const action = useKnowledgeMutation((name: "archive" | "unarchive" | "revert") => studioApi.articleAction(detail.document_id, name));
  const [tab, setTab] = useState<"content" | "passages">("content");

  if (editing) {
    return <ArticleEditor initial={detail} saving={save.isPending} error={save.error} onCancel={() => setEditing(false)}
      onSave={(title, content) => save.mutate({ title, content }, { onSuccess: () => setEditing(false) })} />;
  }
  return (
    <article className="grid content-start gap-4" aria-label={detail.title}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h2 className="text-base font-semibold text-foreground">{detail.title}</h2>
          <p className="text-xs text-muted-foreground">
            {summary.origin === "built_in" ? summary.edited_in_console ? "Built-in article, edited in the console" : "Built-in article" : "Written in the console"}
            {summary.updated_at && ` · updated ${when(summary.updated_at)}${summary.updated_by ? ` by ${summary.updated_by}` : ""}`}
            {` · cited in ${summary.runs_citing} run${summary.runs_citing === 1 ? "" : "s"} (30 d)`}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {!detail.archived && <Button size="sm" variant="outline" onClick={() => setEditing(true)}><Pencil />Edit</Button>}
          {summary.edited_in_console && summary.origin === "built_in" && (
            <Button size="sm" variant="ghost" disabled={action.isPending} onClick={() => action.mutate("revert")}><RotateCcw />Revert to original</Button>
          )}
          {detail.archived
            ? <Button size="sm" variant="ghost" disabled={action.isPending} onClick={() => action.mutate("unarchive")}><ArchiveRestore />Restore</Button>
            : <Button size="sm" variant="ghost" disabled={action.isPending} onClick={() => action.mutate("archive")}><Archive />Archive</Button>}
        </div>
      </header>
      {detail.archived && <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">Archived. The assistant no longer retrieves this article once the index is published.</p>}
      {action.error && <p role="alert" className="text-sm text-danger">{action.error.message}</p>}

      <div role="tablist" aria-label="Article view" className="flex gap-4 border-b border-border text-sm">
        {(["content", "passages"] as const).map((name) => (
          <button key={name} type="button" role="tab" aria-selected={tab === name} onClick={() => setTab(name)}
            className={`-mb-px border-b-2 pb-2 ${tab === name ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
            {name === "content" ? "Article" : `Passages · ${detail.sections.reduce((sum, section) => sum + section.chunks.length, 0)}`}
          </button>
        ))}
      </div>
      {tab === "content" ? (
        <Markdown source={detail.content} />
      ) : (
        <div className="grid gap-4">
          {detail.sections.map((section) => (
            <section key={section.section} className="grid gap-2">
              <h3 className="text-sm font-medium text-foreground">{section.section}</h3>
              <ul className="grid gap-2">
                {section.chunks.map((chunk) => (
                  <li key={chunk.chunk_id} className="grid gap-1 rounded-lg border border-border p-3">
                    <div className="flex items-baseline justify-between gap-3 text-xs text-muted-foreground">
                      <span className="truncate font-mono">{chunk.chunk_id}</span>
                      <span className="shrink-0 tabular-nums">{chunk.runs_citing === 0 ? "Never cited" : `Cited in ${chunk.runs_citing} run${chunk.runs_citing === 1 ? "" : "s"}`}</span>
                    </div>
                    <p className="text-sm whitespace-pre-line text-foreground">{chunk.content}</p>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </article>
  );
}

export function KnowledgePage({ initialDocument }: { initialDocument: string | null }) {
  const documents = useKnowledge();
  const [selected, setSelected] = useState<string | null>(initialDocument);
  const [creating, setCreating] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const create = useKnowledgeMutation((input: { title: string; content: string }) => studioApi.createArticle(input.title, input.content));

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (documents.data ?? []).filter((doc) => (showArchived || !doc.archived) && (!needle || doc.title.toLowerCase().includes(needle)));
  }, [documents.data, filter, showArchived]);
  const archivedCount = (documents.data ?? []).filter((doc) => doc.archived).length;
  const currentId = creating ? null : selected ?? visible[0]?.document_id ?? null;
  const summary = documents.data?.find((doc) => doc.document_id === currentId);
  const detail = useKnowledgeDocument(currentId);

  return (
    <>
      <PageHeader title="Knowledge" description="The articles the AI assistant searches to answer policy questions. Answers cite these passages; anything not covered here is answered with “I don’t know”."
        actions={<>
          <Button size="sm" variant="outline" onClick={() => setSearchOpen(true)}><Search />Test retrieval</Button>
          <Button size="sm" onClick={() => setCreating(true)}><Plus />New article</Button>
        </>} />
      <IndexStatusBar />
      <div className="grid min-h-0 flex-1 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <aside className="grid content-start gap-2 border-b border-border py-3 pr-3 pl-4 sm:pl-6 lg:border-r lg:border-b-0">
          <Input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter articles" aria-label="Filter articles" className="h-8" />
          {documents.isPending ? <div className="grid gap-1.5"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : (
            <nav aria-label="Articles" className="grid gap-0.5">
              {visible.map((doc) => {
                const active = doc.document_id === currentId;
                return (
                  <button key={doc.document_id} type="button" onClick={() => { setCreating(false); setSelected(doc.document_id); }} aria-current={active ? "true" : undefined}
                    className={`grid gap-0.5 rounded-lg px-3 py-2 text-left transition-colors ${active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"}`}>
                    <span className={`truncate text-sm font-medium ${doc.archived ? "line-through" : ""}`}>{doc.title}</span>
                    <span className="text-xs text-muted-foreground">
                      {doc.chunk_count} passages · {doc.runs_citing} citing runs{doc.origin === "console" ? " · console" : doc.edited_in_console ? " · edited" : ""}
                    </span>
                  </button>
                );
              })}
            </nav>
          )}
          {archivedCount > 0 && (
            <button type="button" className="inline-flex min-h-8 items-center justify-self-start rounded-md px-3 text-xs text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none" onClick={() => setShowArchived(!showArchived)}>
              {showArchived ? "Hide archived" : `Show archived (${archivedCount})`}
            </button>
          )}
        </aside>
        <div className="min-w-0 p-4 sm:p-6">
          {creating ? (
            <div className="grid gap-3">
              <h2 className="text-base font-semibold text-foreground">New article</h2>
              <ArticleEditor initial={{ title: "", content: "" }} saving={create.isPending} error={create.error} onCancel={() => setCreating(false)}
                onSave={(title, content) => create.mutate({ title, content }, { onSuccess: (doc) => { setCreating(false); setSelected(doc.document_id); } })} />
            </div>
          ) : detail.data && summary ? (
            <DocumentView key={detail.data.document_id} summary={summary} detail={detail.data} />
          ) : detail.isPending && currentId ? <div className="grid gap-3"><Skeleton className="h-8 w-1/2" /><Skeleton className="h-64" /></div>
            : <p className="text-sm text-muted-foreground">{documents.isError ? "Articles could not be loaded." : "Select an article."}</p>}
        </div>
      </div>
      <SearchPreview open={searchOpen} onClose={() => setSearchOpen(false)} onOpenDocument={(id) => { setSearchOpen(false); setCreating(false); setSelected(id); }} />
    </>
  );
}
