import { AlertTriangle, MessageSquareText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { AgentResponse, AgentRun } from "../../types";
import { Badge, Card, SectionHeader } from "../../components/ui";
import { AgentTimeline } from "./AgentTimeline";
import { ChatInput } from "./ChatInput";
import { MessageBubble, type ChatMessage } from "./MessageBubble";
import { PolicyDecisionCard } from "./PolicyDecisionCard";
import { RetrievalCard } from "./RetrievalCard";
import { ToolExecutionCard } from "./ToolExecutionCard";
import { TracePanel } from "./TracePanel";
import { responseState } from "../../components/runSemantics";

function newConversationId(): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Date.now().toString(36);
  return `chat-${id.slice(0, 8)}`;
}

const CHAT_SESSION_KEY = "agentic-ops.chat.session.v1";
type ChatSession = { conversationId: string; customerId: number; messages: ChatMessage[] };

function readChatSession(): ChatSession | null {
  try {
    const raw = sessionStorage.getItem(CHAT_SESSION_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const candidate = parsed as Partial<ChatSession>;
    if (typeof candidate.conversationId !== "string" || !Array.isArray(candidate.messages)) return null;
    return {
      conversationId: candidate.conversationId,
      customerId: typeof candidate.customerId === "number" ? candidate.customerId : 1,
      messages: candidate.messages.filter((item): item is ChatMessage => {
        if (typeof item !== "object" || item === null) return false;
        const message = item as Partial<ChatMessage>;
        return typeof message.id === "string" && (message.role === "customer" || message.role === "agent") && typeof message.content === "string" && typeof message.timestamp === "string";
      }),
    };
  } catch {
    return null;
  }
}

function sessionConversationId(): string {
  return readChatSession()?.conversationId ?? newConversationId();
}

export function agentState(response: AgentResponse): string {
  return responseState(response);
}

export function ChatWindow() {
  const [conversationId] = useState(sessionConversationId);
  const [customerId, setCustomerId] = useState(() => readChatSession()?.customerId ?? 1);
  const [messages, setMessages] = useState<ChatMessage[]>(() => readChatSession()?.messages ?? []);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const transcriptEnd = useRef<HTMLDivElement>(null);

  useEffect(() => { transcriptEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);
  useEffect(() => {
    try { sessionStorage.setItem(CHAT_SESSION_KEY, JSON.stringify({ conversationId, customerId, messages } satisfies ChatSession)); } catch { /* session persistence is best effort */ }
  }, [conversationId, customerId, messages]);
  useEffect(() => {
    let active = true;
    void api.conversation(conversationId).then((projection) => {
      if (!active || projection.runs.length === 0) return;
      setRun(projection.runs[projection.runs.length - 1]);
      setCustomerId(projection.customer_id);
    }).catch(() => { /* a new conversation has no projection yet */ });
    return () => { active = false; };
  }, [conversationId]);

  const send = async (message: string) => {
    const timestamp = new Date().toISOString();
    setMessages((current) => [...current, { id: `${timestamp}-customer`, role: "customer", content: message, timestamp }]);
    setBusy(true);
    setError(null);
    try {
      const response = await api.chat(conversationId, customerId, message);
      setMessages((current) => [...current, { id: `${timestamp}-agent`, role: "agent", content: response.message, timestamp: new Date().toISOString(), state: agentState(response) }]);
      try {
        setRun(await api.run(response.agent_run_id));
      } catch (projectionError) {
        setRun(null);
        setError(projectionError instanceof Error ? `Response returned, but the operator projection is unavailable: ${projectionError.message}` : "Response returned, but the operator projection is unavailable.");
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.reason === "input_too_long") {
        setError("Your message is too long. Please shorten it.");
      }
      else if (caught instanceof ApiError && caught.status === 401) setError("The operator authentication session is missing or expired.");
      else setError(caught instanceof Error ? caught.message : "The agent endpoint is unavailable.");
    } finally {
      setBusy(false);
    }
  };

  return <div className="space-y-5" data-testid="unified-chat"><div className="run-header"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="eyebrow">Unified agent experience</div><h1 className="mt-1 text-2xl font-semibold tracking-tight text-main">Customer conversation + agent observability</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-muted">Follow the customer request and the bounded system activity together: retrieval, policy, tools, memory, tracing, and escalation signals remain observable without exposing hidden reasoning.</p></div><div className="flex items-center gap-2"><Badge tone="info">proposal → decision → authority</Badge><Badge tone="neutral">read-only telemetry</Badge></div></div><div className="mt-5 flex flex-wrap items-center gap-3 border-t border-border pt-4"><label className="flex items-center gap-2 text-xs text-muted" htmlFor="chat-customer-id">Customer scope<input id="chat-customer-id" className="field-readonly w-20 font-mono" type="number" min={1} value={customerId} onChange={(event) => setCustomerId(Number(event.target.value) || 1)} data-testid="chat-customer-id" /></label><span className="font-mono text-xs text-muted">conversation {conversationId}</span></div></div><div className="grid gap-5 xl:grid-cols-[1.05fr_1fr_1fr]"><Card as="section" className="flex min-h-[680px] flex-col p-5" data-testid="customer-conversation"><SectionHeader eyebrow="Customer interaction" title="Conversation" description="User-visible messages returned by the existing agent endpoint. No prompts or hidden reasoning are shown." action={<MessageSquareText size={17} className="text-info" aria-hidden="true" />} /><div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">{messages.length === 0 ? <div className="flex min-h-[400px] items-center justify-center"><div className="max-w-sm text-center"><MessageSquareText size={28} className="mx-auto text-info" aria-hidden="true" /><h2 className="mt-4 text-sm font-medium text-main">Start a controlled customer request</h2><p className="mt-2 text-xs leading-5 text-muted">Try “I received a damaged product and want a refund” to see the response beside its evidence and decision activity.</p></div></div> : messages.map((message) => <MessageBubble key={message.id} message={message} />)}{busy && <div className="flex items-center gap-2 text-xs text-muted"><span className="activity-pulse" aria-hidden="true" />Agent processing through the configured workflow…</div>}<div ref={transcriptEnd} /></div>{error && <div className="notice notice-warning mt-4" role="alert"><AlertTriangle size={14} aria-hidden="true" /><span>{error}</span></div>}<ChatInput busy={busy} onSend={send} /></Card><div className="space-y-5"><AgentTimeline run={run} busy={busy} />{run ? <><RetrievalCard run={run} /><PolicyDecisionCard run={run} /><ToolExecutionCard tools={run.tools} /></> : null}</div><TracePanel run={run} /></div></div>;
}
