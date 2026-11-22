export type ThemePreference = "light" | "dark" | "system";

const storageKey = "agentops-theme";
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

export function readThemePreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(storageKey);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Storage can be unavailable (private mode, blocked site data); fall back to the system.
  }
  return "system";
}

export function resolveTheme(preference: ThemePreference): "light" | "dark" {
  if (preference !== "system") return preference;
  return media().matches ? "dark" : "light";
}

export function applyTheme(preference: ThemePreference): void {
  const resolved = resolveTheme(preference);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.classList.toggle("light", resolved === "light");
}

export function storeThemePreference(preference: ThemePreference): void {
  try {
    window.localStorage.setItem(storageKey, preference);
  } catch {
    // The choice still applies for this page view.
  }
  applyTheme(preference);
}

export function watchSystemTheme(onChange: () => void): () => void {
  const query = media();
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}
