import type { ErrorLog, QualityView } from "../features/improve/types";
import { humanize } from "../features/runs/runPresentation";
import { slaLabel } from "../features/workspace/sla";
import type { Health, OperatorConversation } from "../types";
import type { ConsoleView } from "./routes";

export type NotificationTone = "danger" | "warning" | "info";
export type ConsoleNotification = {
  id: string;
  tone: NotificationTone;
  title: string;
  detail: string;
  /** When the thing happened, or null for an ongoing state (seen once, it stays read while it lasts). */
  at: string | null;
  target: { kind: "conversation"; id: number } | { kind: "view"; view: ConsoleView };
};

const MAX_CONVERSATIONS = 5;
const last = (values: string[]) => values[values.length - 1];

/**
 * Turns what the console already loads into a short list of things someone should look at,
 * most urgent first. Nothing here is stored on the server; it is a view over current state.
 */
export function buildNotifications({ health, healthUnavailable, inbox, errors, quality, now = new Date() }: {
  health: Health | null | undefined;
  healthUnavailable: boolean;
  inbox: OperatorConversation[];
  errors: ErrorLog | undefined;
  quality: QualityView | undefined;
  now?: Date;
}): ConsoleNotification[] {
  const items: ConsoleNotification[] = [];

  if (healthUnavailable) {
    items.push({ id: "health:unreachable", tone: "danger", title: "Operator API unreachable", detail: "Data on every page may be out of date.", at: null, target: { kind: "view", view: "monitoring" } });
  } else if (health && health.status !== "ready") {
    const broken = health.components.filter((component) => component.status === "unavailable" || component.status === "incompatible");
    items.push({
      id: `health:${health.status}`, tone: "danger", title: "Some systems are degraded",
      detail: broken.length ? broken.map((component) => humanize(component.name)).join(", ") : humanize(health.status),
      at: null, target: { kind: "view", view: "monitoring" },
    });
  }

  const overdue = inbox
    .filter((item) => item.owner === "queued" && item.sla?.state === "breached")
    .sort((a, b) => a.sla!.due_at.localeCompare(b.sla!.due_at));
  for (const item of overdue.slice(0, MAX_CONVERSATIONS)) {
    items.push({
      id: `sla:${item.id}`, tone: "danger", title: `${item.customer_name} is past the response target`,
      detail: `${slaLabel(item.sla!, now.getTime())}${item.escalation?.reason ? ` · ${humanize(item.escalation.reason)}` : ""}`,
      at: item.sla!.due_at, target: { kind: "conversation", id: item.id },
    });
  }
  if (overdue.length > MAX_CONVERSATIONS) {
    items.push({ id: "sla:more", tone: "danger", title: `${overdue.length - MAX_CONVERSATIONS} more past the response target`, detail: "Open the inbox to see them all.", at: overdue[MAX_CONVERSATIONS].sla!.due_at, target: { kind: "view", view: "workspace" } });
  }

  const atRisk = inbox.filter((item) => item.owner === "queued" && item.sla?.state === "at_risk");
  if (atRisk.length) {
    const latest = last(atRisk.map((item) => item.updated_at).sort());
    items.push({ id: "sla:at-risk", tone: "warning", title: `${atRisk.length} waiting close to the response target`, detail: atRisk.slice(0, 3).map((item) => item.customer_name).join(", "), at: latest, target: { kind: "view", view: "workspace" } });
  }

  const today = now.toLocaleDateString("en-CA");
  const todays = (errors?.errors ?? []).filter((row) => new Date(row.at).toLocaleDateString("en-CA") === today);
  if (todays.length) {
    const byCause = new Map<string, number>();
    for (const row of todays) byCause.set(row.category, (byCause.get(row.category) ?? 0) + 1);
    const [cause, count] = [...byCause.entries()].sort((a, b) => b[1] - a[1])[0];
    items.push({
      id: `errors:${today}`, tone: "warning", title: `${todays.length} run${todays.length === 1 ? "" : "s"} failed today`,
      detail: `Mostly ${humanize(cause).toLowerCase()} (${count})`,
      at: last(todays.map((row) => row.at).sort()), target: { kind: "view", view: "errors" },
    });
  }

  const flagged = (quality?.monitors ?? []).filter((monitor) => monitor.severity === "high" && monitor.open_flags > 0);
  if (flagged.length) {
    const total = flagged.reduce((sum, monitor) => sum + monitor.open_flags, 0);
    items.push({ id: "quality:high", tone: "info", title: `${total} high-severity flag${total === 1 ? "" : "s"} to review`, detail: flagged.map((monitor) => monitor.name).join(", "), at: null, target: { kind: "view", view: "quality" } });
  }

  return items;
}

const seenKey = "agentops-notifications-seen";

/** The ids and timestamps the operator has already seen, kept in this browser only. */
export function readSeen(): Record<string, string> {
  try {
    const value = JSON.parse(window.localStorage.getItem(seenKey) ?? "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

export function writeSeen(items: ConsoleNotification[]) {
  const seen = Object.fromEntries(items.map((item) => [item.id, item.at ?? ""]));
  try { window.localStorage.setItem(seenKey, JSON.stringify(seen)); } catch { /* storage may be unavailable */ }
  return seen;
}

/** New when never seen, or when the same notification now carries a later moment (more failures today, say). */
export const isUnread = (item: ConsoleNotification, seen: Record<string, string>) => !(item.id in seen) || (item.at !== null && seen[item.id] < item.at);
