import type { ReactNode } from "react";

export const windows = [7, 30, 90] as const;

export function WindowPicker({ value, onChange }: { value: number; onChange: (days: number) => void }) {
  return (
    <div role="radiogroup" aria-label="Time window" className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5">
      {windows.map((days) => (
        <button key={days} type="button" role="radio" aria-checked={value === days} onClick={() => onChange(days)}
          className={`h-7 rounded-md px-2.5 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${value === days ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
          {days} days
        </button>
      ))}
    </div>
  );
}

export function Section({ title, description, actions, children, className = "" }: { title: string; description?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-border bg-card ${className}`}>
      <div className="flex min-h-12 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border px-4 py-2">
        <div className="grid min-w-0 gap-0.5">
          <h2 className="text-sm font-medium text-foreground">{title}</h2>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-1.5">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

export function rating(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toFixed(1);
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="px-4 py-8 text-center text-sm text-muted-foreground">{children}</p>;
}
