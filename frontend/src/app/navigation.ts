import { Activity, AlertTriangle, Beaker, BarChart3, FlaskConical, Lightbulb, ScanEye, BookOpen, Compass, Gauge, Library, Workflow, Zap, LayoutDashboard, MessagesSquare, Network, Settings, ShieldCheck, Sparkles, type LucideIcon } from "lucide-react";
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
    label: "Train",
    items: [
      { view: "knowledge", label: "Knowledge", icon: Library },
      { view: "guidance", label: "Guidance", icon: Compass },
      { view: "procedures", label: "Procedures", icon: Workflow },
      { view: "actions", label: "Actions", icon: Zap },
    ],
  },
  {
    label: "Test",
    items: [
      { view: "simulations", label: "Simulations", icon: FlaskConical },
      { view: "evidence", label: "Safety", icon: ShieldCheck },
      { view: "playground", label: "Playground", icon: Beaker },
    ],
  },
  {
    label: "Improve",
    items: [
      { view: "performance", label: "Performance", icon: BarChart3 },
      { view: "optimize", label: "Optimize", icon: Lightbulb },
      { view: "quality", label: "Quality", icon: ScanEye },
      { view: "errors", label: "Errors", icon: AlertTriangle },
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
  {
    label: "Workspace",
    items: [{ view: "settings", label: "Settings", icon: Settings }],
  },
];
