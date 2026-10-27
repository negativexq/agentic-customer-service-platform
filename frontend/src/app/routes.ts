export const viewPaths = {
  overview: "/overview", playground: "/playground", traces: "/traces", evidence: "/safety",
  chat: "/chat", workspace: "/workspace", architecture: "/architecture", showcase: "/showcase",
} as const;
export type ConsoleView = keyof typeof viewPaths;
export function viewFromPath(pathname: string): ConsoleView {
  if (pathname === "/" || pathname === "/workspace") return "workspace";
  if (pathname === "/evidence") return "evidence";
  return (Object.keys(viewPaths) as ConsoleView[]).find((view) => viewPaths[view] === pathname) ?? "overview";
}
