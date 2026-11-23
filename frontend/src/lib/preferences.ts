import { useSyncExternalStore } from "react";

/** Per-browser console preferences. They change only how this console behaves for the operator. */
export type LiveUpdates = "fast" | "normal" | "slow";
export type InboxView = "all" | "open" | "queued" | "mine" | "closed";

export type Preferences = {
  defaultInboxView: InboxView;
  openWaitingFirst: boolean;
  liveUpdates: LiveUpdates;
};

export const defaultPreferences: Preferences = { defaultInboxView: "all", openWaitingFirst: true, liveUpdates: "fast" };

/** Polling intervals in ms: inbox list, open conversation. */
export const liveUpdateIntervals: Record<LiveUpdates, { inbox: number; conversation: number }> = {
  fast: { inbox: 4_000, conversation: 2_000 },
  normal: { inbox: 10_000, conversation: 5_000 },
  slow: { inbox: 30_000, conversation: 15_000 },
};

const storageKey = "agentops-preferences";
const listeners = new Set<() => void>();
let cached: Preferences | null = null;

function parse(raw: string | null): Preferences {
  if (!raw) return defaultPreferences;
  try {
    const value = JSON.parse(raw) as Partial<Preferences>;
    return {
      defaultInboxView: (["all", "open", "queued", "mine", "closed"] as const).includes(value.defaultInboxView as InboxView) ? value.defaultInboxView as InboxView : defaultPreferences.defaultInboxView,
      openWaitingFirst: typeof value.openWaitingFirst === "boolean" ? value.openWaitingFirst : defaultPreferences.openWaitingFirst,
      liveUpdates: value.liveUpdates && value.liveUpdates in liveUpdateIntervals ? value.liveUpdates : defaultPreferences.liveUpdates,
    };
  } catch {
    return defaultPreferences;
  }
}

export function readPreferences(): Preferences {
  if (cached) return cached;
  try {
    cached = parse(window.localStorage.getItem(storageKey));
  } catch {
    cached = defaultPreferences;
  }
  return cached;
}

export function updatePreferences(patch: Partial<Preferences>): void {
  cached = { ...readPreferences(), ...patch };
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(cached));
  } catch {
    // Storage can be blocked; the change still applies until the page reloads.
  }
  listeners.forEach((listener) => listener());
}

export function resetPreferences(): void {
  updatePreferences(defaultPreferences);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usePreferences(): Preferences {
  return useSyncExternalStore(subscribe, readPreferences, () => defaultPreferences);
}

export { parse as parsePreferences };
