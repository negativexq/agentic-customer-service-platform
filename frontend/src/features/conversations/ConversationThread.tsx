import { ArrowUp, Bot, Bug, FlaskConical, Hand, MessagesSquare, ShieldCheck, Undo2, CheckCheck, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { AgentResponse, ConversationTurn, OperatorConversationDetail, OperatorIdentity, RecordedMessage } from "../../types";
import { formatRelative, humanize, parseTimestamp, toneDot } from "../runs/runPresentation";
import { handler, initials, senderLabel } from "./conversations";

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
};

const testCustomers = [
  { id: 1, label: "Ada · customer_001" },
  { id: 2, label: "Grace · customer_002" },
  { id: 3, label: "Alan · customer_003" },
];

type Row = { key: string; message: RecordedMessage; response?: AgentResponse };

function time(iso: string) {
  return parseTimestamp(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function InspectButton({ runId, onInspect }: { runId: string; onInspect: (runId: string) => void }) {
  return (
    <button type="button" onClick={() => onInspect(runId)} aria-label={`Inspect run ${runId}`}
      className="inline-flex items-center gap-1 rounded px-1 text-muted-foreground opacity-70 transition-opacity hover:text-foreground hover:opacity-100 focus-visible:opacity-100">
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

function Message({ row, customerName, onInspect }: { row: Row; customerName: string; onInspect: (runId: string) => void }) {
  const { message, response } = row;
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
      <div className={`grid max-w-[78%] gap-1 ${fromCustomer ? "justify-items-start" : "justify-items-end"}`}>
        <span className="px-1 text-xs text-muted-foreground">{senderLabel(message, customerName)} · {time(message.created_at)}</span>
        <p className={`rounded-xl px-3.5 py-2 text-sm leading-6 whitespace-pre-wrap ${bubble}`}>{message.content}</p>
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

function ActionButton({ children, onClick, disabled, primary = false }: { children: ReactNode; onClick: () => void; disabled: boolean; primary?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className={`inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-sm transition-colors disabled:opacity-50 ${primary ? "bg-primary text-primary-foreground hover:bg-primary/90" : "border border-border hover:bg-accent"}`}>
      {children}
    </button>
  );
}

export function ConversationThread(props: Props) {
  const { target, detail, loading, me, localTurns, canSimulateCustomer, busy, error, actionPending, actionError, onCustomerChange, onSendAsCustomer, onReply, onAction, onInspect, onInspectConversation } = props;
  const [message, setMessage] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const draft = target.kind === "draft";
  const customerName = detail?.customer_name ?? testCustomers.find((item) => draft && item.id === target.customerId)?.label.split(" · ")[0] ?? "Customer";
  const mine = detail?.owner === "human" && detail.assigned_to_me;

  const rows = useMemo<Row[]>(() => {
    const responses = new Map(localTurns.map((turn) => [turn.response.agent_run_id, turn.response]));
    const recorded = detail?.messages ?? [];
    const recordedRuns = new Set(recorded.map((item) => item.agent_run_id).filter(Boolean));
    const result: Row[] = recorded.map((item) => ({ key: `m-${item.sequence}`, message: item, response: item.sender_type === "ai" && item.agent_run_id ? responses.get(item.agent_run_id) : undefined }));
    // Turns sent this session that the transcript has not returned yet render immediately.
    localTurns.filter((turn) => !recordedRuns.has(turn.response.agent_run_id)).forEach((turn, index) => {
      const at = new Date().toISOString();
      result.push(
        { key: `l-${index}-c`, message: { sequence: -1, sender_type: "customer", sender_name: null, sent_by_me: false, content: turn.request, redacted: false, agent_run_id: turn.response.agent_run_id, created_at: at } },
        { key: `l-${index}-a`, message: { sequence: -1, sender_type: "ai", sender_name: null, sent_by_me: false, content: turn.response.message, redacted: false, agent_run_id: turn.response.agent_run_id, created_at: at }, response: turn.response },
      );
    });
    return result;
  }, [detail?.messages, localTurns]);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [rows.length, busy]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!message.trim() || busy || actionPending) return;
    const value = message;
    setMessage("");
    if (mine) await onReply(value);
    else await onSendAsCustomer(value);
  };

  const who = detail ? handler(detail) : null;
  const composerMode: "operator" | "customer" | "watch" = mine ? "operator" : canSimulateCustomer ? "customer" : "watch";

  return (
    <section className="flex h-full min-h-[560px] flex-col" aria-label="Conversation">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
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
        </div>
      )}
      {actionError && <p className="border-b border-danger/30 bg-danger/10 px-4 py-2 text-xs text-danger" role="alert">{actionError}</p>}

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5" aria-live="polite">
        {rows.length === 0 && !busy ? (
          <div className="mx-auto flex h-full max-w-sm flex-col items-center justify-center gap-2 text-center">
            <MessagesSquare className="size-5 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm text-foreground">{loading ? "Loading conversation…" : draft ? "Start a test conversation" : "No messages yet"}</p>
            {draft && <p className="text-xs leading-5 text-muted-foreground">You are writing as {customerName}. The AI assistant answers like it would for a real customer.</p>}
          </div>
        ) : (
          <ol className="mx-auto grid max-w-3xl gap-4">
            {rows.map((row) => <Message key={row.key} row={row} customerName={customerName} onInspect={onInspect} />)}
            {busy && (
              <li className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="size-1.5 animate-pulse rounded-full bg-info" aria-hidden="true" />Generating bounded response…
              </li>
            )}
          </ol>
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-border p-3">
        {error && <p className="mx-auto mb-2 max-w-3xl rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">{error}</p>}
        {composerMode === "watch" ? (
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
            <span>{detail?.owner === "human" ? `${who?.label ?? "Another operator is replying"}. You are watching live.` : detail?.owner === "closed" ? "This conversation is resolved. A new customer message reopens it to the AI assistant." : "You are watching live. Take over to reply to the customer yourself."}</span>
            {detail && (detail.owner === "ai" || detail.owner === "queued") && <ActionButton primary disabled={actionPending} onClick={() => onAction("claim")}><Hand className="size-3.5" aria-hidden="true" />Take over</ActionButton>}
          </div>
        ) : (
          <form className="mx-auto grid max-w-3xl gap-1.5" onSubmit={submit}>
            <span className="px-1 text-xs text-muted-foreground">
              {composerMode === "operator" ? `Replying to ${customerName} as ${me?.display_name ?? "operator"}` : `Testing as ${customerName}. Messages go to the AI assistant as the customer.`}
            </span>
            <div className="flex items-center gap-2 rounded-lg border border-border bg-background p-1 pl-3 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/30">
              <label className="sr-only" htmlFor="agent-message">{composerMode === "operator" ? "Reply to customer" : "Message as customer"}</label>
              <input data-testid={composerMode === "operator" ? "operator-reply" : "agent-message"} id="agent-message" value={message} onChange={(event) => setMessage(event.target.value)}
                placeholder={composerMode === "operator" ? `Reply to ${customerName}` : "Write as the customer"}
                className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground" disabled={busy || actionPending} />
              <button data-testid={composerMode === "operator" ? "send-operator-reply" : "send-agent-message"} type="submit" disabled={busy || actionPending || !message.trim()}
                className="inline-flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground transition-opacity disabled:opacity-40">
                <ArrowUp className="size-4" aria-hidden="true" /><span className="sr-only">Send message</span>
              </button>
            </div>
          </form>
        )}
        {detail && <p className="mx-auto mt-1.5 max-w-3xl px-1 text-[11px] text-muted-foreground">Started {formatRelative(detail.created_at)} · every message is recorded, sensitive values are masked</p>}
      </div>
    </section>
  );
}
