import type { ConversationOwner, OperatorConversation, RecordedMessage } from "../../types";
import type { Tone } from "../runs/runPresentation";

export type InboxFilter = "all" | "open" | "queued" | "mine" | "closed";

export const inboxFilters: { id: InboxFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "open", label: "Open" },
  { id: "queued", label: "Needs human" },
  { id: "mine", label: "Mine" },
  { id: "closed", label: "Resolved" },
];

export function matchesFilter(conversation: OperatorConversation, filter: InboxFilter): boolean {
  if (filter === "all") return true;
  if (filter === "open") return conversation.owner !== "closed";
  if (filter === "queued") return conversation.owner === "queued";
  if (filter === "mine") return conversation.owner === "human" && conversation.assigned_to_me;
  return conversation.owner === "closed";
}

/** Who is answering the customer right now, in operator language. */
export function handler(conversation: Pick<OperatorConversation, "owner" | "assigned_to_me" | "assignee_name">): { label: string; short: string; tone: Tone } {
  const other = conversation.assignee_name ?? "Another operator";
  const owners: Record<ConversationOwner, { label: string; short: string; tone: Tone }> = {
    ai: { label: "AI assistant is replying", short: "AI assistant", tone: "success" },
    queued: { label: "Waiting for an operator", short: "Needs human", tone: "warning" },
    human: { label: conversation.assigned_to_me ? "You are replying" : `${other} is replying`, short: conversation.assigned_to_me ? "You" : other, tone: "info" },
    closed: { label: "Resolved", short: "Resolved", tone: "neutral" },
  };
  return owners[conversation.owner];
}

export function senderLabel(message: RecordedMessage, customerName: string): string {
  if (message.sender_type === "customer") return customerName;
  if (message.sender_type === "ai") return "AI assistant";
  if (message.sender_type === "operator") return message.sent_by_me ? `You · ${message.sender_name ?? "Operator"}` : message.sender_name ?? "Operator";
  return "System";
}

export function previewText(message: RecordedMessage | null): string {
  if (!message) return "No messages yet";
  const prefix = message.sender_type === "customer" ? "" : message.sender_type === "ai" ? "AI: " : message.sender_type === "operator" ? (message.sent_by_me ? "You: " : `${message.sender_name ?? "Operator"}: `) : "";
  return `${prefix}${message.content}`;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}
