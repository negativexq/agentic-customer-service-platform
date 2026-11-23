/**
 * The console's sign-in screen is a demo gate: it opens the workspace for the demo account below
 * and remembers that in this browser. API access is still decided by the configured auth provider.
 */
export const DEMO_ACCOUNT = {
  name: "Alex Morgan",
  email: "alex@agentops.demo",
  password: "agentops-demo",
  role: "Support lead",
} as const;

export type DemoSession = { name: string; email: string; role: string; signedInAt: string };

const sessionKey = "agentops-session";

export function readSession(): DemoSession | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(sessionKey) ?? "null");
    return value && typeof value.email === "string" && typeof value.name === "string" ? value : null;
  } catch {
    return null;
  }
}

/** Returns the session for the demo account, or null when the details do not match it. */
export function signIn(email: string, password: string): DemoSession | null {
  if (email.trim().toLowerCase() !== DEMO_ACCOUNT.email || password !== DEMO_ACCOUNT.password) return null;
  const session: DemoSession = { name: DEMO_ACCOUNT.name, email: DEMO_ACCOUNT.email, role: DEMO_ACCOUNT.role, signedInAt: new Date().toISOString() };
  try { window.localStorage.setItem(sessionKey, JSON.stringify(session)); } catch { /* storage may be unavailable */ }
  return session;
}

export function signOut() {
  try { window.localStorage.removeItem(sessionKey); } catch { /* storage may be unavailable */ }
}

export const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]!.toUpperCase()).join("");
