import { ArrowUp, Bot, Bug, ChevronLeft, FlaskConical, Hand, Lock, MessagesSquare, PanelRight, ShieldCheck, StickyNote, Trash2, Undo2, CheckCheck, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { AgentResponse, ConversationTurn, OperatorConversationDetail, OperatorIdentity, RecordedMessage } from "../../types";
import { formatRelative, humanize, parseTimestamp, toneDot } from "../runs/runPresentation";
import { handler, initials, senderLabel } from "./conversations";
import { bubbleText, ReplyBlocks } from "./ReplyBlocks";
import { FeedbackPrompt } from "../workspace/FeedbackPrompt";
import { useAddNote, useDeleteNote, useNotes } from "../workspace/queries";
import { SavedReplyMenu } from "../workspace/SavedReplyMenu";
import { slaLabel, slaTone } from "../workspace/sla";
import type { Note } from "../workspace/types";

export type ThreadTarget =
  | { kind: "draft"; conversationId: string; customerId: number }
  | { kind: "conversation"; id: number };

type Props = {
  target: ThreadTarget;
  detail: OperatorConversationDetail | undefined;
  loading: boolean;
  me: OperatorIdentity | undefined;
  localTurns: ConversationTurn[];
  canSimulateCustomer: boolean;
  busy: boolean;
  error: string | null;
  actionPending: boolean;
  actionError: string | null;
  onCustomerChange: (id: number) => void;
  onSendAsCustomer: (message: string) => Promise<void>;
  onReply: (message: string) => Promise<void>;
  onAction: (action: "claim" | "release" | "resolve") => void;
  onInspect: (runId: string) => void;
  onInspectConversation: () => void;
  contextOpen: boolean;
  onToggleContext: () => void;
  /** Narrow screens: return to the conversation list. */
  onBack?: () => void;
};

const testCustomers = [
  { id: 1, label: "Ada · customer_001" },
  { id: 2, label: "Grace · customer_002" },
  { id: 3, label: "Alan · customer_003" },
];

type Row = { key: string; message: RecordedMessage; response?: AgentResponse; note?: undefined } | { key: string; note: Note; message?: undefined; response?: undefined };

function time(iso: string) {
  return parseTimestamp(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function InspectButton({ runId, onInspect }: { runId: string; onInspect: (runId: string) => void }) {
  return (
    <button type="button" onClick={() => onInspect(runId)} aria-label={`Inspect run ${runId}`}
      className="inline-flex min-h-6 items-center gap-1 rounded px-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
      <Bug className="size-3" aria-hidden="true" />Inspect
    </button>
  );
}

function ResponseChips({ response }: { response: AgentResponse }) {
  return (
    <>
      {response.pending_action && (
        <span data-testid="confirmation-boundary" className="inline-flex items-center gap-1 text-warning"><ShieldCheck className="size-3" aria-hidden="true" />Confirmation required, no sensitive mutation yet</span>
      )}
      {(response.tool_call || response.pending_action) && (
        <span data-testid="execution-result" className="inline-flex items-center gap-1">
          <Wrench className="size-3" aria-hidden="true" />
          {response.tool_call ? `${response.tool_call.name ?? "tool"} · ${response.tool_call.status}` : `Awaiting confirmation · ${response.pending_action?.status}`}
        </span>
      )}
      {response.failure_category && <span className="text-warning">{humanize(response.failure_category)}</span>}
    </>
  );
}

/** Internal notes sit in the timeline but look nothing like a message: the customer never sees them. */
function NoteRow({ note, onDelete }: { note: Note; onDelete: (id: number) => void }) {
  return (
    <li className="mx-auto w-full max-w-[85%]" data-testid="internal-note">
      <div className="grid gap-1 rounded-lg border border-warning/30 bg-warning/8 px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs text-warning">
          <Lock className="size-3" aria-hidden="true" />Internal note · {note.author_name} · {time(note.created_at)}
          {note.written_by_me && (
            <button type="button" onClick={() => onDelete(note.id)} aria-label="Delete note" className="ml-auto rounded p-0.5 text-muted-foreground hover:text-foreground"><Trash2 className="size-3" aria-hidden="true" /></button>
          )}
        </span>
        <p className="text-sm leading-6 whitespace-pre-wrap text-foreground">{note.content}</p>
        {note.redacted && <span className="text-xs text-muted-foreground">Sensitive values hidden</span>}
      </div>
    </li>
  );
}

function Message({ row, customerName, onInspect, onQuickReply }: { row: { message: RecordedMessage; response?: AgentResponse }; customerName: string; onInspect: (runId: string) => void; onQuickReply?: (text: string) => void }) {
  const { message, response } = row;
  const blocks = message.blocks?.length ? message.blocks : response?.blocks ?? [];
  if (message.sender_type === "system") {
    return <li className="py-1 text-center text-xs text-muted-foreground">{message.content} · {time(message.created_at)}</li>;
  }
  const fromCustomer = message.sender_type === "customer";
  const bubble = fromCustomer
    ? "rounded-bl-sm bg-muted text-foreground"
    : message.sender_type === "operator"
      ? "rounded-br-sm bg-primary text-primary-foreground"
      : "rounded-br-sm border border-border bg-card text-foreground";
  return (
    <li className={`flex gap-2 ${fromCustomer ? "" : "flex-row-reverse"}`} data-testid={fromCustomer ? "conversation-request" : message.sender_type === "ai" ? "agent-response" : "operator-message"}>
      <span className={`mt-5 flex size-7 shrink-0 items-center justify-center rounded-full text-[11px] font-medium ${fromCustomer ? "bg-muted text-foreground" : message.sender_type === "ai" ? "bg-success/12 text-success" : "bg-primary/15 text-primary"}`} aria-hidden="true">
        {fromCustomer ? initials(customerName) : message.sender_type === "ai" ? <Bot className="size-3.5" /> : initials(message.sender_name ?? "Operator")}
      </span>
      <div className={`grid gap-1 ${blocks.length ? "w-full max-w-[min(92%,40rem)]" : "max-w-[78%]"} ${fromCustomer ? "justify-items-start" : "justify-items-end"}`}>
        <span className="px-1 text-xs text-muted-foreground">{senderLabel(message, customerName)} · {time(message.created_at)}</span>
        <p className={`rounded-xl px-3.5 py-2 text-sm leading-6 whitespace-pre-wrap ${bubble}`}>{message.sender_type === "ai" ? bubbleText(message.content, blocks) : message.content}</p>
        {message.sender_type === "ai" && <ReplyBlocks blocks={blocks} text={message.content} onReply={onQuickReply} />}
        {(message.agent_run_id && message.sender_type === "ai") || message.redacted || response ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-xs text-muted-foreground">
            {response && <ResponseChips response={response} />}
            {message.redacted && <span>Sensitive values hidden</span>}
            {message.agent_run_id && message.sender_type === "ai" && <InspectButton runId={message.agent_run_id} onInspect={onInspect} />}
          </div>
        ) : null}
      </div>
    </li>
  );
}

/** Chat-style "typing" bubble shown on the AI side while a reply is being written. */
function TypingIndicator() {
  return (
    <li className="flex flex-row-reverse gap-2" role="status" aria-live="polite" data-testid="ai-typing">
      <span className="mt-5 flex size-7 shrink-0 items-center justify-center rounded-full bg-success/12 text-success" aria-hidden="true"><Bot className="size-3.5" /></span>
      <div className="grid justify-items-end gap-1">
        <span className="px-1 text-xs text-muted-foreground">AI assistant is typing…</span>
        <span className="inline-flex h-10 items-center gap-1 rounded-xl rounded-br-sm border border-border bg-card px-4" aria-hidden="true">
          <span className="typing-dot size-1.5 rounded-full bg-muted-foreground" />
          <span className="typing-dot size-1.5 rounded-full bg-muted-foreground [animation-delay:0.2s]" />
          <span className="typing-dot size-1.5 rounded-full bg-muted-foreground [animation-delay:0.4s]" />
        </span>
      </div>
    </li>
  );
}

function ActionButton({ children, onClick, disabled, primary = false }: { children: ReactNode; onClick: () => void; disabled: boolean; primary?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className={`inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none disabled:opacity-50 pointer-coarse:h-10 ${primary ? "bg-primary text-primary-foreground hover:bg-primary/90" : "border border-border hover:bg-accent"}`}>
      {children}
    </button>
  );
}

export function ConversationThread(props: Props) {
  const { target, detail, loading, me, localTurns, canSimulateCustomer, busy, error, actionPending, actionError, onCustomerChange, onSendAsCustomer, onReply, onAction, onInspect, onInspectConversation, contextOpen, onToggleContext, onBack } = props;
  const [message, setMessage] = useState("");
  const [composeAs, setComposeAs] = useState<"reply" | "note">("reply");
  const pk = target.kind === "conversation" ? target.id : null;
  const notes = useNotes(pk);
  const addNote = useAddNote(pk ?? 0);
  const deleteNote = useDeleteNote(pk ?? 0);
  const endRef = useRef<HTMLDivElement>(null);
  const draft = target.kind === "draft";
  const customerName = detail?.customer_name ?? testCustomers.find((item) => draft && item.id === target.customerId)?.label.split(" · ")[0] ?? "Customer";
  const mine = detail?.owner === "human" && detail.assigned_to_me;

  const rows = useMemo<Row[]>(() => {
    const responses = new Map(localTurns.map((turn) => [turn.response.agent_run_id, turn.response]));
    const recorded = detail?.messages ?? [];
    const recordedRuns = new Set(recorded.map((item) => item.agent_run_id).filter(Boolean));
    const result: Row[] = recorded.map((item) => ({ key: `m-${item.sequence}`, message: item, response: item.sender_type === "ai" && item.agent_run_id ? responses.get(item.agent_run_id) : undefined }));
    for (const note of notes.data ?? []) {
      const at = parseTimestamp(note.created_at).getTime();
      const index = result.findIndex((row) => row.message !== undefined && parseTimestamp(row.message.created_at).getTime() > at);
      result.splice(index === -1 ? result.length : index, 0, { key: `n-${note.id}`, note });
    }
    // Turns sent this session that the transcript has not returned yet render immediately.
    localTurns.filter((turn) => !recordedRuns.has(turn.response.agent_run_id)).forEach((turn, index) => {
      const at = new Date().toISOString();
      result.push(
        { key: `l-${index}-c`, message: { sequence: -1, sender_type: "customer", sender_name: null, sent_by_me: false, content: turn.request, redacted: false, agent_run_id: turn.response.agent_run_id, created_at: at } },
        { key: `l-${index}-a`, message: { sequence: -1, sender_type: "ai", sender_name: null, sent_by_me: false, content: turn.response.message, redacted: false, agent_run_id: turn.response.agent_run_id, created_at: at }, response: turn.response },
      );
    });
    return result;
  }, [detail?.messages, localTurns, notes.data]);

  // The customer's message shows right away, followed by a typing bubble while the AI answers.
  const [pendingCustomer, setPendingCustomer] = useState<string | null>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [rows.length, busy, pendingCustomer]);

  // Quick replies answer as the customer, so they only work where the operator may simulate one.
  const lastRow = rows[rows.length - 1];
  const lastAiKey = lastRow?.message?.sender_type === "ai" ? lastRow.key : null;
  const quickReply = canSimulateCustomer && !mine && !busy && !actionPending
    ? async (value: string) => {
        setPendingCustomer(value);
        try { await onSendAsCustomer(value); } finally { setPendingCustomer(null); }
      }
    : undefined;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!message.trim() || (!noteMode && (busy || actionPending))) return;
    const value = message;
    setMessage("");
    if (noteMode) { addNote.mutate(value.trim(), { onError: () => setMessage(value) }); return; }
    if (mine) { await onReply(value); return; }
    setPendingCustomer(value);
    try { await onSendAsCustomer(value); } finally { setPendingCustomer(null); }
  };

  const who = detail ? handler(detail) : null;
  const composerMode: "operator" | "customer" | "watch" = mine ? "operator" : canSimulateCustomer ? "customer" : "watch";
  const noteMode = composeAs === "note" && pk !== null;

  return (
    <section className="flex h-full min-h-[560px] flex-col" aria-label="Conversation">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
        {onBack && (
          <button type="button" onClick={onBack} aria-label="Back to all conversations"
            className="-ml-1.5 inline-flex size-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
            <ChevronLeft className="size-4" aria-hidden="true" />
          </button>
        )}
        {draft ? (
          <>
            <span className="inline-flex items-center gap-1.5 rounded bg-warning/12 px-1.5 py-0.5 text-xs text-warning"><FlaskConical className="size-3" aria-hidden="true" />Test conversation</span>
            <label className="sr-only" htmlFor="customer-scope">Test as customer</label>
            <select id="customer-scope" data-testid="customer-scope" value={target.customerId} onChange={(event) => onCustomerChange(Number(event.target.value))}
              className="h-8 rounded-md border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30">
              {testCustomers.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
            <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">{target.conversationId}</span>
          </>
        ) : (
          <>
            <span className="flex size-8 items-center justify-center rounded-full bg-muted text-xs font-medium" aria-hidden="true">{initials(customerName)}</span>
            <span className="grid min-w-0">
              <span className="truncate text-sm font-medium text-foreground">{customerName}</span>
              <span className="truncate font-mono text-[11px] text-muted-foreground">{detail?.conversation_id ?? "…"}</span>
            </span>
            {who && (
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground" data-testid="conversation-handler">
                <span className={`size-1.5 rounded-full ${toneDot[who.tone]}`} aria-hidden="true" />
                <span className="text-foreground">{who.label}</span>
              </span>
            )}
            <span className="ml-auto flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><span className="size-1.5 animate-pulse rounded-full bg-success" aria-hidden="true" />Live</span>
              <ActionButton disabled={false} onClick={onInspectConversation}><Bug className="size-3.5" aria-hidden="true" />Debug</ActionButton>
              <button type="button" onClick={onToggleContext} aria-pressed={contextOpen} aria-label={contextOpen ? "Hide customer details" : "Show customer details"} title="Customer details"
                className={`inline-flex size-8 items-center justify-center rounded-md border border-border transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none pointer-coarse:size-10 ${contextOpen ? "bg-accent text-foreground" : "text-muted-foreground"}`}>
                <PanelRight className="size-3.5" aria-hidden="true" />
              </button>
              {detail && (detail.owner === "ai" || detail.owner === "queued") && (
                <ActionButton primary={detail.owner === "queued"} disabled={actionPending} onClick={() => onAction("claim")}><Hand className="size-3.5" aria-hidden="true" />Take over</ActionButton>
              )}
              {mine && (
                <>
                  <ActionButton disabled={actionPending} onClick={() => onAction("release")}><Undo2 className="size-3.5" aria-hidden="true" />Return to AI</ActionButton>
                  <ActionButton disabled={actionPending} onClick={() => onAction("resolve")}><CheckCheck className="size-3.5" aria-hidden="true" />Resolve</ActionButton>
                </>
              )}
            </span>
          </>
        )}
      </header>

      {detail?.escalation && (detail.owner === "queued" || detail.owner === "human") && (
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border bg-warning/6 px-4 py-2 text-xs">
          <span className="font-medium text-warning">Handoff · {detail.escalation.priority} priority</span>
          <span className="min-w-0 text-foreground">{detail.escalation.summary}</span>
          <span className="text-muted-foreground">Reason: {detail.escalation.reason}</span>
          {detail.sla && <span className={`ml-auto font-medium tabular-nums ${slaTone[detail.sla.state]}`} data-testid="conversation-sla">First reply {slaLabel(detail.sla)}</span>}
        </div>
      )}
      {actionError && <p className="border-b border-danger/30 bg-danger/10 px-4 py-2 text-xs text-danger" role="alert">{actionError}</p>}

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5" aria-live="polite">
        {rows.length === 0 && !busy && !pendingCustomer ? (
          <div className="mx-auto flex h-full max-w-sm flex-col items-center justify-center gap-2 text-center">
            <MessagesSquare className="size-5 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm text-foreground">{loading ? "Loading conversation…" : draft ? "Start a test conversation" : "No messages yet"}</p>
            {draft && <p className="text-xs leading-5 text-muted-foreground">You are writing as {customerName}. The AI assistant answers like it would for a real customer.</p>}
          </div>
        ) : (
          <ol className="mx-auto grid max-w-3xl gap-4">
            {rows.map((row) => row.note
              ? <NoteRow key={row.key} note={row.note} onDelete={(id) => deleteNote.mutate(id)} />
              : <Message key={row.key} row={row} customerName={customerName} onInspect={onInspect} onQuickReply={row.key === lastAiKey ? quickReply : undefined} />)}
            {pendingCustomer && (
              <li className="flex gap-2" data-testid="pending-customer-message">
                <span className="mt-5 flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-medium text-foreground" aria-hidden="true">{initials(customerName)}</span>
                <div className="grid max-w-[78%] justify-items-start gap-1">
                  <span className="px-1 text-xs text-muted-foreground">{customerName} · now</span>
                  <p className="rounded-xl rounded-bl-sm bg-muted px-3.5 py-2 text-sm leading-6 whitespace-pre-wrap text-foreground">{pendingCustomer}</p>
                </div>
              </li>
            )}
            {busy && <TypingIndicator />}
          </ol>
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-border p-3">
        {error && <p className="mx-auto mb-2 max-w-3xl rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">{error}</p>}
        {pk !== null && (
          <div className="mx-auto mb-1.5 flex max-w-3xl items-center gap-1" role="tablist" aria-label="Compose">
            {(["reply", "note"] as const).map((mode) => (
              <button key={mode} type="button" role="tab" aria-selected={composeAs === mode} onClick={() => setComposeAs(mode)}
                className={`inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs transition-colors ${composeAs === mode ? mode === "note" ? "bg-warning/12 text-warning" : "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                {mode === "note" && <StickyNote className="size-3" aria-hidden="true" />}{mode === "reply" ? (composerMode === "customer" ? "Message" : "Reply") : "Internal note"}
              </button>
            ))}
          </div>
        )}
        {canSimulateCustomer && detail?.owner === "closed" && !noteMode && (
          <div className="mb-2"><FeedbackPrompt pk={detail.id} conversationId={detail.conversation_id} customerId={detail.customer_id} customerName={customerName} current={detail.csat_rating ?? null} /></div>
        )}
        {composerMode === "watch" && !noteMode ? (
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
            <span>{detail?.owner === "human" ? `${who?.label ?? "Another operator is replying"}. You are watching live.` : detail?.owner === "closed" ? "This conversation is resolved. A new customer message reopens it to the AI assistant." : "You are watching live. Take over to reply to the customer yourself."}</span>
            {detail && (detail.owner === "ai" || detail.owner === "queued") && <ActionButton primary disabled={actionPending} onClick={() => onAction("claim")}><Hand className="size-3.5" aria-hidden="true" />Take over</ActionButton>}
          </div>
        ) : (
          <form className="mx-auto grid max-w-3xl gap-1.5" onSubmit={submit}>
            <span className="px-1 text-xs text-muted-foreground">
              {noteMode ? "Only your team sees notes. They are never sent to the customer or the AI assistant." : composerMode === "operator" ? `Replying to ${customerName} as ${me?.display_name ?? "operator"}` : `Testing as ${customerName}. Messages go to the AI assistant as the customer.`}
            </span>
            {addNote.error && <p role="alert" className="px-1 text-xs text-danger">{addNote.error.message}</p>}
            <div className={`flex items-center gap-2 rounded-xl border p-1 pl-3 transition-shadow focus-within:ring-3 ${noteMode ? "border-warning/40 bg-warning/6 focus-within:border-warning focus-within:ring-warning/20" : "border-border bg-background focus-within:border-ring focus-within:ring-ring/20"}`}>
              {composerMode === "operator" && !noteMode && <SavedReplyMenu customerName={customerName} disabled={busy || actionPending} onInsert={(text) => setMessage((current) => (current.trim() ? `${current.trimEnd()} ${text}` : text))} />}
              <label className="sr-only" htmlFor="agent-message">{noteMode ? "Internal note" : composerMode === "operator" ? "Reply to customer" : "Message as customer"}</label>
              <input data-testid={noteMode ? "internal-note-input" : composerMode === "operator" ? "operator-reply" : "agent-message"} id="agent-message" value={message} onChange={(event) => setMessage(event.target.value)}
                placeholder={noteMode ? "Write a note for your team" : composerMode === "operator" ? `Reply to ${customerName}` : "Write as the customer"}
                className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground focus-visible:outline-none" disabled={noteMode ? addNote.isPending : busy || actionPending} />
              <button data-testid={noteMode ? "save-internal-note" : composerMode === "operator" ? "send-operator-reply" : "send-agent-message"} type="submit" disabled={(noteMode ? addNote.isPending : busy || actionPending) || !message.trim()}
                className={`inline-flex size-8 items-center justify-center rounded-md transition-opacity disabled:opacity-40 ${noteMode ? "bg-warning text-background" : "bg-primary text-primary-foreground"}`}>
                <ArrowUp className="size-4" aria-hidden="true" /><span className="sr-only">{noteMode ? "Save note" : "Send message"}</span>
              </button>
            </div>
          </form>
        )}
        {detail && <p className="mx-auto mt-1.5 max-w-3xl px-1 text-[11px] text-muted-foreground">Started {formatRelative(detail.created_at)} · every message is recorded, sensitive values are masked</p>}
      </div>
    </section>
  );
}
