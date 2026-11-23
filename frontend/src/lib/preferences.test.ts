import { describe, expect, it } from "vitest";
import { defaultPreferences, liveUpdateIntervals, parsePreferences } from "./preferences";

describe("preferences", () => {
  it("falls back to defaults for missing or malformed storage", () => {
    expect(parsePreferences(null)).toEqual(defaultPreferences);
    expect(parsePreferences("{not json")).toEqual(defaultPreferences);
    expect(parsePreferences(JSON.stringify({ defaultInboxView: "everything", liveUpdates: "turbo", openWaitingFirst: "yes" }))).toEqual(defaultPreferences);
  });

  it("keeps valid stored choices", () => {
    expect(parsePreferences(JSON.stringify({ defaultInboxView: "queued", liveUpdates: "slow", openWaitingFirst: false })))
      .toEqual({ defaultInboxView: "queued", liveUpdates: "slow", openWaitingFirst: false });
    expect(liveUpdateIntervals.slow.conversation).toBeGreaterThan(liveUpdateIntervals.fast.conversation);
  });
});
