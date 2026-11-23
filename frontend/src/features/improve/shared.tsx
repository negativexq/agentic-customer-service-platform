import { Segmented } from "../../components/layout/Segmented";
import type { ReactNode } from "react";

export const windows = [7, 30, 90] as const;

export function WindowPicker({ value, onChange }: { value: number; onChange: (days: number) => void }) {
  return <Segmented label="Time window" value={value} onChange={onChange} options={windows.map((days) => ({ value: days as number, label: `${days} days` }))} />;
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

/** Longest window the insights API serves; comparisons need twice the chosen window. */
export const MAX_WINDOW_DAYS = 90;
export const compareWindow = (days: number) => (days * 2 <= MAX_WINDOW_DAYS ? days * 2 : null);

/**
 * Splits daily rows from a doubled window into the chosen window and the one before it,
 * summing the numeric fields of each half.
 */
export function windowHalves<T extends { day: string }>(daily: T[], days: number, keys: (keyof T & string)[]): [Record<string, number>, Record<string, number>] {
  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - days + 1);
  const edge = `${cutoff.getFullYear()}-${String(cutoff.getMonth() + 1).padStart(2, "0")}-${String(cutoff.getDate()).padStart(2, "0")}`;
  const current: Record<string, number> = Object.fromEntries(keys.map((key) => [key, 0]));
  const previous: Record<string, number> = Object.fromEntries(keys.map((key) => [key, 0]));
  for (const row of daily) {
    const half = row.day >= edge ? current : previous;
    for (const key of keys) half[key] += Number(row[key] ?? 0);
  }
  return [current, previous];
}
