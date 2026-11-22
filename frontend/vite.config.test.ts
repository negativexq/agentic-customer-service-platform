import { describe, expect, it } from "vitest";

import { backendPathPattern } from "./vite.config";

describe("development backend proxy", () => {
  const proxiedPaths = [
    "/agent/chat",
    "/ui/system-health",
    "/customers/1",
    "/orders/1",
    "/tickets/1",
    "/memories/1",
    "/escalations",
    "/operator/conversations",
    "/conversations/abc/messages",
    "/health",
    "/ready",
  ];

  it.each(proxiedPaths)("matches %s", (path) => {
    expect(new RegExp(backendPathPattern).test(path)).toBe(true);
  });

  it("does not proxy frontend assets", () => {
    expect(new RegExp(backendPathPattern).test("/assets/index.js")).toBe(false);
  });
});
