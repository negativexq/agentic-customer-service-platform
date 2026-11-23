export const viewPaths = {
  overview: "/overview", workspace: "/workspace", traces: "/traces", monitoring: "/monitoring", audit: "/audit",
  knowledge: "/knowledge", guidance: "/guidance", procedures: "/procedures", actions: "/actions",
  performance: "/performance", optimize: "/optimize", quality: "/quality", simulations: "/simulations", errors: "/errors",
  evidence: "/safety", playground: "/playground", chat: "/chat", showcase: "/showcase", architecture: "/architecture", settings: "/settings",
} as const;
export type ConsoleView = keyof typeof viewPaths;
export function viewFromPath(pathname: string): ConsoleView {
  if (pathname === "/" || pathname === "/workspace") return "workspace";
  if (pathname === "/evidence") return "evidence";
  if (pathname.startsWith("/runs/")) return "traces";
  return (Object.keys(viewPaths) as ConsoleView[]).find((view) => viewPaths[view] === pathname) ?? "overview";
}

export const viewTitles: Record<ConsoleView, string> = {
  overview: "Overview", workspace: "Conversations", traces: "Runs", monitoring: "Monitoring", audit: "Audit log",
  knowledge: "Knowledge", guidance: "Guidance", procedures: "Procedures", actions: "Actions",
  performance: "Performance", optimize: "Optimize", quality: "Quality", simulations: "Simulations", errors: "Errors",
  evidence: "Safety", playground: "Playground", chat: "Chat and activity", showcase: "Recorded showcase", architecture: "Architecture", settings: "Settings",
};
