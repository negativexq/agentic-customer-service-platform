import type { AgentRun } from "../../types";
import { isRecorded, parseTimestamp, runStatus, type Tone } from "../runs/runPresentation";

export type ConversationSummary = {
  id: string;
  customerId: number;
  runs: AgentRun[]; // oldest first
  lastAt: string;
  lastIntent: string;
  status: { label: string; tone: Tone };
  active: boolean;
};

const customerNames: Record<number, string> = { 1: "Ada", 2: "Grace", 3: "Alan" };
export const customerName = (id: number) => customerNames[id] ?? `Customer #${id}`;

const ACTIVE_WINDOW_MS = 60 * 60 * 1000;
const openLabels = new Set(["Awaiting confirmation", "Needs input"]);

/** Groups persisted run projections into conversations; recorded demo runs are excluded. */
export function groupConversations(runs: AgentRun[], now = Date.now()): ConversationSummary[] {
  const byId = new Map<string, AgentRun[]>();
  for (const run of runs) {
    if (isRecorded(run)) continue;
    const list = byId.get(run.conversation_id) ?? [];
    list.push(run);
    byId.set(run.conversation_id, list);
  }
  return [...byId].map(([id, list]) => {
    const sorted = [...list].sort((a, b) => a.started_at.localeCompare(b.started_at));
    const last = sorted[sorted.length - 1];
    const status = runStatus(last);
    const recent = now - parseTimestamp(last.started_at).getTime() < ACTIVE_WINDOW_MS;
    return {
      id,
      customerId: last.customer_id,
      runs: sorted,
      lastAt: last.started_at,
      lastIntent: last.intent,
      status,
      active: recent || openLabels.has(status.label),
    };
  }).sort((a, b) => b.lastAt.localeCompare(a.lastAt));
}
