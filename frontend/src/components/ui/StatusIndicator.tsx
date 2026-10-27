import { CircleAlert, CircleCheck, CircleDot, CircleX } from "lucide-react";
import type { BadgeTone } from "./Badge";

const iconByTone = { success: CircleCheck, warning: CircleAlert, danger: CircleX, info: CircleDot, neutral: CircleDot } as const;
export function StatusIndicator({ label, tone = "neutral", compact = false }: { label: string; tone?: BadgeTone; compact?: boolean }) {
  const normalizedTone = tone === "mint" ? "success" : tone === "amber" ? "warning" : tone === "red" ? "danger" : tone === "slate" ? "neutral" : tone;
  const Icon = iconByTone[normalizedTone];
  return <span className={`status-indicator status-${normalizedTone} text-xs`}><Icon size={compact ? 13 : 15} strokeWidth={2} aria-hidden="true" />{label}</span>;
}
