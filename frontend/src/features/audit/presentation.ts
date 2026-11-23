import { UI_LOCALE } from "@/lib/utils";
import type { AuditActor, AuditEvent, AuditKind } from "./types";

export const kindOrder: AuditKind[] = ["conversation", "handoff", "operator", "assistant", "action", "safety"];

export const kindLabels: Record<AuditKind, string> = {
  conversation: "Opened and closed",
  handoff: "Handoffs",
  operator: "Operator work",
  assistant: "Assistant decisions",
  action: "Actions taken",
  safety: "Safety blocks",
};

/** Tone of the small marker next to each entry, by what the entry is about. */
export function eventTone(event: AuditEvent): "neutral" | "info" | "success" | "warning" | "danger" {
  if (event.action === "assistant.failed") return "danger";
  if (event.kind === "safety") return "warning";
  if (event.action === "conversation.closed" || event.kind === "action") return "success";
  if (event.kind === "handoff") return "info";
  return "neutral";
}

export type You = { actorId: string; name: string } | null;

/** The signed-in person reads as themselves, not as the backend's operator label. */
export function actorName(actor: AuditActor, you: You): string {
  if (actor.type === "operator" && you && actor.id === you.actorId) return `${you.name} (you)`;
  return actor.name;
}

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

export function dayLabel(iso: string, now = new Date()): string {
  const date = new Date(iso);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (dayKey(date) === dayKey(now)) return "Today";
  if (dayKey(date) === dayKey(yesterday)) return "Yesterday";
  return date.toLocaleDateString(UI_LOCALE, { weekday: "long", day: "numeric", month: "long", year: date.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

export const clock = (iso: string) => new Date(iso).toLocaleTimeString(UI_LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const stamp = (iso: string) => new Date(iso).toLocaleString(UI_LOCALE, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Entries in their given order, split into consecutive calendar days. */
export function groupByDay(events: AuditEvent[], now = new Date()): { label: string; events: AuditEvent[] }[] {
  const groups: { key: string; label: string; events: AuditEvent[] }[] = [];
  for (const event of events) {
    const key = dayKey(new Date(event.at));
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.events.push(event);
    else groups.push({ key, label: dayLabel(event.at, now), events: [event] });
  }
  return groups.map(({ label, events: items }) => ({ label, events: items }));
}

/**
 * What to send as the server-side search. The server knows the signed-in operator by their
 * actor id, not by the name shown in this console, so a search for that name asks for the id.
 */
export function serverSearch(query: string, you: You): string {
  const needle = query.trim().toLowerCase();
  if (you && needle.length >= 2 && you.name.toLowerCase().includes(needle)) return you.actorId;
  return query.trim();
}

export function matches(event: AuditEvent, query: string, you: You): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [event.customer_name, event.conversation_id, actorName(event.actor, you), event.summary, event.detail ?? "", event.run_id ?? ""]
    .some((value) => value.toLowerCase().includes(needle));
}

/** How long a person or the assistant held the conversation, read from its trail (oldest first). */
export function handlingSummary(trail: AuditEvent[], you: You): string[] {
  const lines: string[] = [];
  const started = trail.find((event) => event.action === "conversation.started");
  const taken = trail.find((event) => event.action === "operator.claimed" || event.action === "operator.took_over");
  const queued = trail.find((event) => event.action === "handoff.queued");
  const closed = [...trail].reverse().find((event) => event.action === "conversation.closed");
  if (started && (queued || taken)) lines.push(`AI assistant handled it for ${span(started.at, (queued ?? taken)!.at)}`);
  if (queued && taken && taken.at >= queued.at) lines.push(`Waited ${span(queued.at, taken.at)} in the queue`);
  if (taken) lines.push(`${actorName(taken.actor, you)} ${taken.action === "operator.claimed" ? "picked it up" : "took it over"} at ${clock(taken.at)}`);
  if (closed) lines.push(`Closed by ${actorName(closed.actor, you)} at ${clock(closed.at)}`);
  return lines;
}

export function span(fromIso: string, toIso: string): string {
  const seconds = Math.max(0, Math.round((new Date(toIso).getTime() - new Date(fromIso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
