import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Stars } from "./CustomerContextPanel";
import { personalise } from "./SavedReplyMenu";
import { formatTarget, slaLabel } from "./sla";
import type { SlaStatus } from "./types";

const sla = (dueInMinutes: number, now: number): SlaStatus => ({
  priority: "high", target_minutes: 60, state: "ok",
  waiting_since: new Date(now - 60_000).toISOString(),
  due_at: new Date(now + dueInMinutes * 60_000).toISOString(),
});

describe("operator workspace", () => {
  it("counts down to the first-reply target and reports overdue time", () => {
    const now = Date.parse("2026-11-22T12:00:00Z");
    expect(slaLabel(sla(12, now), now)).toBe("12m left");
    expect(slaLabel(sla(125, now), now)).toBe("2h 5m left");
    expect(slaLabel(sla(-4, now), now)).toBe("Overdue 4m");
    expect(formatTarget(1440)).toBe("1 day");
    expect(formatTarget(240)).toBe("4 hours");
  });

  it("fills customer names into saved replies", () => {
    expect(personalise("Hi {first_name}, thanks {name}!", "Ada Lovelace")).toBe("Hi Ada, thanks Ada Lovelace!");
    expect(personalise("Hi {first_name}", "  ")).toBe("Hi there");
  });

  it("labels ratings for screen readers", () => {
    expect(renderToStaticMarkup(<Stars rating={4} />)).toContain('aria-label="4 out of 5"');
  });
});
