import { FilePlus2, MessageSquareText, PenLine } from "lucide-react";
import { Button } from "@/components/primitives/button";
import { Skeleton } from "@/components/primitives/skeleton";
import { EntityChip } from "../../components/EntityChip";
import { formatRelative, humanize, parseTimestamp } from "../runs/runPresentation";
import { useKnowledge, useUnanswered } from "./queries";
import type { UnansweredQuestion } from "./types";

export const UNANSWERED_DAYS = 30;

/** A new "##" section that answers the question, in the article's Markdown. */
export function sectionFor(question: string): string {
  const heading = question.trim().replace(/\s+/g, " ").replace(/[?.!]+$/, "");
  return `## ${heading.charAt(0).toUpperCase()}${heading.slice(1)}\n\n`;
}

export function UnansweredQuestions({ onAddToArticle, onNewArticle, onOpenConversation, onOpenArticle }: {
  onAddToArticle: (articleId: string, question: string) => void;
  onNewArticle: (question: string) => void;
  onOpenConversation: (pk: number) => void;
  onOpenArticle: (articleId: string) => void;
}) {
  const query = useUnanswered(UNANSWERED_DAYS);
  const articles = useKnowledge();
  const articleOf = (id: string | null) => (id ? articles.data?.find((doc) => doc.document_id === id) : undefined);
  const items = query.data ?? [];
  const asked = items.reduce((sum, item) => sum + item.asked, 0);

  // Edited in the console after the question was last asked: probably given the answer. A built-in
  // article's timestamp only records when it was loaded, so it does not count.
  const updatedSince = (item: UnansweredQuestion) => {
    const article = articleOf(item.article_id);
    if (!article?.updated_at || !(article.edited_in_console || article.origin === "console")) return false;
    return parseTimestamp(article.updated_at) > parseTimestamp(item.last_asked_at);
  };

  return (
    <section className="grid content-start gap-4" aria-label="Unanswered questions">
      <header className="grid gap-1">
        <h2 className="text-base font-semibold text-foreground">Unanswered questions</h2>
        <p className="max-w-2xl text-sm text-muted-foreground">
          Questions customers asked in the last {UNANSWERED_DAYS} days that no article could answer, so the assistant said it did not know. Add the answer to an article, then publish it to the index.
        </p>
      </header>
      {query.isError && <p role="alert" className="text-sm text-danger">Unanswered questions could not be loaded.</p>}
      {!query.data ? (
        <div className="grid gap-2"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground">Every policy question in the last {UNANSWERED_DAYS} days was answered from an article.</p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">{items.length} question{items.length === 1 ? "" : "s"}, asked {asked} time{asked === 1 ? "" : "s"}. Most asked first.</p>
          <ul className="grid divide-y divide-border rounded-xl border border-border bg-card">
            {items.map((item) => {
              const article = articleOf(item.article_id);
              const done = updatedSince(item);
              return (
                <li key={item.run_id} className={`grid gap-3 px-4 py-3.5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start ${done ? "opacity-60" : ""}`}>
                  <div className="grid min-w-0 gap-1.5">
                    <p className="text-sm leading-6 font-medium text-foreground">“{item.question}”</p>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
                      <span>{humanize(item.intent)}</span>
                      <span aria-hidden="true">·</span>
                      <span>{item.asked > 1 ? `Asked ${item.asked} times` : "Asked once"}, last {formatRelative(item.last_asked_at)}</span>
                      {done && <span className="rounded-md bg-success/10 px-1.5 py-0.5 text-success">Article updated since</span>}
                    </div>
                    {article && (
                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        Belongs in <EntityChip kind="knowledge" label={article.title} onOpen={() => onOpenArticle(article.document_id)} />
                      </div>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 sm:justify-end">
                    {item.conversation_pk !== null && (
                      <Button size="sm" variant="ghost" onClick={() => onOpenConversation(item.conversation_pk!)}><MessageSquareText />Conversation</Button>
                    )}
                    {article ? (
                      <>
                        <Button size="sm" variant="outline" onClick={() => onNewArticle(item.question)}><FilePlus2 />New article</Button>
                        <Button size="sm" onClick={() => onAddToArticle(article.document_id, item.question)}><PenLine />Add to {article.title}</Button>
                      </>
                    ) : (
                      <Button size="sm" onClick={() => onNewArticle(item.question)}><FilePlus2 />Write an article</Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
