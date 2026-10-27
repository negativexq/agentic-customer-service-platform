import { describe, expect, it } from "vitest";
import { viewFromPath, viewPaths } from "./routes";

describe("console routes", () => {
  it("round-trips every navigable view through its own reloadable URL", () => {
    expect(new Set(Object.values(viewPaths)).size).toBe(Object.keys(viewPaths).length);
    for (const [view, path] of Object.entries(viewPaths)) expect(viewFromPath(path)).toBe(view);
  });
  it("preserves workspace and evidence bookmarks", () => {
    expect(viewFromPath("/")).toBe("workspace");
    expect(viewFromPath("/evidence")).toBe("evidence");
  });
});
