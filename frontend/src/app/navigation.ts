import { Activity, Beaker, BookOpen, Gauge, LayoutDashboard, MessagesSquare, Network, ShieldCheck, Sparkles, type LucideIcon } from "lucide-react";
import type { ConsoleView } from "./routes";

export type NavItem = { view: ConsoleView; label: string; icon: LucideIcon; shortcut?: string };
export type NavGroup = { label: string; items: NavItem[] };

export const navigation: NavGroup[] = [
  {
    label: "Operate",
    items: [
      { view: "overview", label: "Overview", icon: LayoutDashboard, shortcut: "G O" },
      { view: "workspace", label: "Conversations", icon: MessagesSquare, shortcut: "G C" },
      { view: "traces", label: "Runs", icon: Activity, shortcut: "G R" },
      { view: "monitoring", label: "Monitoring", icon: Gauge, shortcut: "G M" },
    ],
  },
  {
    label: "Govern",
    items: [
      { view: "evidence", label: "Safety", icon: ShieldCheck },
      { view: "playground", label: "Playground", icon: Beaker },
    ],
  },
  {
    label: "Reference",
    items: [
      { view: "showcase", label: "Recorded showcase", icon: Sparkles },
      { view: "chat", label: "Chat and activity", icon: BookOpen },
      { view: "architecture", label: "Architecture", icon: Network },
    ],
  },
];
