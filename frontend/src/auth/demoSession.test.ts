import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_ACCOUNT, initials, readSession, signIn, signOut } from "./demoSession";

const store = new Map<string, string>();
vi.stubGlobal("window", { localStorage: { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key) } });
afterEach(() => store.clear());

describe("demo sign-in", () => {
  it("opens a session for the demo account only", () => {
    expect(signIn(DEMO_ACCOUNT.email, "wrong")).toBeNull();
    expect(readSession()).toBeNull();
    const session = signIn(` ${DEMO_ACCOUNT.email.toUpperCase()} `, DEMO_ACCOUNT.password);
    expect(session?.name).toBe(DEMO_ACCOUNT.name);
    expect(readSession()?.email).toBe(DEMO_ACCOUNT.email);
    signOut();
    expect(readSession()).toBeNull();
  });

  it("shows two initials", () => {
    expect(initials("Alex Morgan")).toBe("AM");
    expect(initials("cher")).toBe("C");
  });
});
