import { parseTimestamp } from "../runs/runPresentation";
import type { SlaStatus } from "./types";

/** "12m left", "3h 5m left", "Overdue 4m": the customer's wait against the first-response target. */
export function slaLabel(sla: SlaStatus, now = Date.now()): string {
  const minutes = Math.round((parseTimestamp(sla.due_at).getTime() - now) / 60_000);
  const span = (value: number) => {
    const abs = Math.abs(value);
    if (abs < 60) return `${abs}m`;
    if (abs < 1440) return `${Math.floor(abs / 60)}h${abs % 60 ? ` ${abs % 60}m` : ""}`;
    return `${Math.floor(abs / 1440)}d`;
  };
  return minutes < 0 ? `Overdue ${span(minutes)}` : `${span(minutes)} left`;
}

/** "5h" or "12m": the inbox row's compact form; the full label stays in its tooltip. */
export function slaShort(sla: SlaStatus, now = Date.now()): string {
  const minutes = Math.abs(Math.round((parseTimestamp(sla.due_at).getTime() - now) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h`;
  return `${Math.floor(minutes / 1440)}d`;
}

export const slaTone: Record<SlaStatus["state"], string> = {
  ok: "text-muted-foreground",
  at_risk: "text-warning",
  breached: "text-danger",
};

export function formatTarget(minutes: number): string {
  if (minutes % 1440 === 0) return `${minutes / 1440} day${minutes === 1440 ? "" : "s"}`;
  if (minutes % 60 === 0) return `${minutes / 60} hour${minutes === 60 ? "" : "s"}`;
  return `${minutes} min`;
}
