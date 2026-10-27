import type { LucideIcon } from "lucide-react";
import { Card } from "./Card";

export function MetricCard({ label, value, detail, icon: Icon }: { label: string; value: string; detail?: string; icon?: LucideIcon }) {
  return <Card className="metric-card p-4"><div className="flex items-center justify-between text-xs font-medium tracking-[.03em] text-muted"><span>{label}</span>{Icon && <Icon size={15} strokeWidth={1.8} aria-hidden="true" />}</div><div className="mt-2 font-mono text-lg font-semibold text-main">{value}</div>{detail && <div className="mt-1 text-xs text-muted">{detail}</div>}</Card>;
}
