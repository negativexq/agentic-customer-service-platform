import { ArrowUp, Bug, MessagesSquare, ShieldCheck, Wrench } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { AgentRun, ConversationTurn } from "../../types";
import { formatRelative, humanize, runStatus, toneDot } from "../runs/runPresentation";
import { customerName } from "./conversations";

type Props = {
  customerId: number;
  conversationId: string;
  history: AgentRun[];
  turns: ConversationTurn[];
  busy: boolean;
  error: string | null;
  canChangeCustomer: boolean;
  onCustomerChange: (id: number) => void;
  onSend: (message: string) => Promise<void>;
  onInspect: (runId: string) => void;
};

const customers = [1, 2, 3];

function InspectButton({ runId, onInspect }: { runId: string; onInspect: (runId: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onInspect(runId)}
      className="inline-flex items-center gap-1 rounded px-1 text-muted-foreground opacity-70 transition-opacity hover:text-foreground hover:opacity-100 focus-visible:opacity-100"
      aria-label={`Inspect run ${runId}`}
    >
      <Bug className="size-3" aria-hidden="true" />Inspect
    </button>
  );
}

/** A past turn. The backend keeps decision metadata only, never message text, so this is a compact record. */
function HistoryTurn({ run, onInspect }: { run: AgentRun; onInspect: (runId: string) => void }) {
  const status = runStatus(run);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground">
      <span className={`size-1.5 rounded-full ${toneDot[status.tone]}`} aria-hidden="true" />
      <span className="text-sm text-foreground">{humanize(run.intent) || "Request"}</span>
      <span>{status.label}</span>
      <span>{formatRelative(run.started_at)}</span>
      <span className="ml-auto"><InspectButton runId={run.run_id} onInspect={onInspect} /></span>
    </li>
  );
}

export function ConversationThread({ customerId, conversationId, history, turns, busy, error, canChangeCustomer, onCustomerChange, onSend, onInspect }: Props) {
  const [message, setMessage] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const empty = history.length === 0 && turns.length === 0;

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [turns.length, history.length, conversationId, busy]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!message.trim() || busy) return;
    const value = message;
    setMessage("");
    await onSend(value);
  };

  return (
    <section className="flex h-full min-h-[560px] flex-col" aria-label="Conversation">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
        {canChangeCustomer ? (
          <>
            <label className="sr-only" htmlFor="customer-scope">Select customer</label>
            <select
              id="customer-scope"
              data-testid="customer-scope"
              value={customerId}
              onChange={(event) => onCustomerChange(Number(event.target.value))}
              className="h-8 rounded-md border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
            >
              {customers.map((id) => <option key={id} value={id}>{customerName(id)} · customer_00{id}</option>)}
            </select>
          </>
        ) : (
          <span className="text-sm font-medium text-foreground" data-testid="customer-scope-label">{customerName(customerId)}</span>
        )}
        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground" title="Conversation id">{conversationId}</span>
        <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className={`size-1.5 rounded-full ${busy ? "animate-pulse bg-info" : "bg-success"}`} aria-hidden="true" />
          {busy ? "Agent is replying" : "Ready"}
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5" aria-live="polite">
        {empty && !busy ? (
          <div className="mx-auto flex h-full max-w-sm flex-col items-center justify-center gap-2 text-center">
            <MessagesSquare className="size-5 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm text-foreground">Start a conversation</p>
            <p className="text-xs leading-5 text-muted-foreground">Ask about an order, a refund, or a policy as {customerName(customerId)}.</p>
          </div>
        ) : (
          <ol className="mx-auto grid max-w-2xl gap-4">
            {history.length > 0 && (
              <li className="text-center text-xs leading-5 text-muted-foreground">Earlier turns. For privacy, message text isn’t stored, only what the agent decided.</li>
            )}
            {history.map((run) => <HistoryTurn key={run.run_id} run={run} onInspect={onInspect} />)}
            {turns.map(({ request, response }, index) => (
              <li className="grid gap-2" key={`${response.agent_run_id}-${index}`}>
                <div className="flex justify-end" data-testid="conversation-request">
                  <p className="max-w-[85%] rounded-xl rounded-br-sm bg-primary px-3.5 py-2 text-sm whitespace-pre-wrap text-primary-foreground">{request}</p>
                </div>
                <div className="grid max-w-[90%] gap-1" data-testid="agent-response">
                  <p className="text-sm leading-6 whitespace-pre-wrap text-foreground">{response.message}</p>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
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
                    <InspectButton runId={response.agent_run_id} onInspect={onInspect} />
                  </div>
                </div>
              </li>
            ))}
            {busy && (
              <li className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="size-1.5 animate-pulse rounded-full bg-info" aria-hidden="true" />
                Generating bounded response…
              </li>
            )}
          </ol>
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-border p-3">
        {error && <p className="mx-auto mb-2 max-w-2xl rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">{error}</p>}
        <form className="mx-auto flex max-w-2xl items-center gap-2 rounded-lg border border-border bg-background p-1 pl-3 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/30" onSubmit={submit}>
          <label className="sr-only" htmlFor="agent-message">Message to agent</label>
          <input
            data-testid="agent-message"
            id="agent-message"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder={empty ? "Write the first message" : "Reply"}
            className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            disabled={busy}
          />
          <button
            data-testid="send-agent-message"
            type="submit"
            disabled={busy || !message.trim()}
            className="inline-flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground transition-opacity disabled:opacity-40"
          >
            <ArrowUp className="size-4" aria-hidden="true" /><span className="sr-only">Send message</span>
          </button>
        </form>
      </div>
    </section>
  );
}
