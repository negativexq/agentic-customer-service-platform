import type { ReactNode } from "react";

export function Stat({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="min-w-0 border-border px-4 py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 font-mono text-xl font-medium tabular-nums tracking-tight text-foreground">{value}</dd>
      {detail && <dd className="mt-0.5 truncate text-xs text-muted-foreground">{detail}</dd>}
    </div>
  );
}

/** One bordered strip of stats separated by hairlines, instead of a row of identical cards. */
export function StatStrip({ children, label }: { children: ReactNode; label: string }) {
  return (
    <dl
      aria-label={label}
      className="grid grid-cols-2 overflow-hidden rounded-xl border border-border bg-card sm:grid-cols-3 lg:grid-cols-6 max-lg:[&>div]:border-b max-sm:[&>div:nth-child(odd)]:border-r sm:max-lg:[&>div:not(:nth-child(3n))]:border-r lg:[&>div:not(:last-child)]:border-r"
    >
      {children}
    </dl>
  );
}
