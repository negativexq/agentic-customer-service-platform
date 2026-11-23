import { ArrowDownRight, ArrowUpRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export type KpiDelta = {
  /** Change against the previous window: a fraction for rates and counts, points for "pts". */
  value: number | null;
  unit?: "percent" | "pts";
  /** Whether a rise is good news (resolution) or bad news (failures). */
  goodWhenUp?: boolean;
};

function DeltaChip({ delta, period }: { delta: KpiDelta; period: string }) {
  if (delta.value === null || !Number.isFinite(delta.value)) return null;
  const up = delta.value > 0;
  const flat = Math.abs(delta.value) < (delta.unit === "pts" ? 0.05 : 0.0005);
  const good = flat ? null : up === (delta.goodWhenUp ?? true);
  const text = delta.unit === "pts"
    ? `${up ? "+" : ""}${delta.value.toFixed(1)} pts`
    : `${up ? "+" : ""}${(delta.value * 100).toFixed(1)}%`;
  const Arrow = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className={`inline-flex h-5 items-center gap-0.5 rounded-md px-1.5 font-medium tabular-nums ${good === null ? "bg-muted text-muted-foreground" : good ? "bg-success/12 text-success" : "bg-danger/12 text-danger"}`}>
        {!flat && <Arrow className="size-3" aria-hidden="true" />}{flat ? "No change" : text}
      </span>
      {!flat && period}
    </span>
  );
}

/** A row of thin day ticks under the number; the latest day is drawn in the accent colour. */
function Ticks({ values, tone }: { values: number[]; tone: string }) {
  const max = Math.max(1, ...values);
  return (
    <div className="flex h-6 items-end gap-[2px]" aria-hidden="true">
      {values.map((value, index) => (
        <span
          key={index}
          className="min-w-0 flex-1 rounded-[1px]"
          style={{ height: `${Math.max(12, (value / max) * 100)}%`, background: value > 0 ? tone : "var(--border)", opacity: index === values.length - 1 || value === 0 ? 1 : 0.55 }}
        />
      ))}
    </div>
  );
}

export function KpiCard({ label, icon: Icon, value, unit, delta, period = "vs previous period", detail, spark, sparkTone = "var(--chart-1)", footer }: {
  label: string;
  icon?: LucideIcon;
  value: ReactNode;
  unit?: string;
  delta?: KpiDelta;
  period?: string;
  detail?: ReactNode;
  spark?: number[];
  sparkTone?: string;
  footer?: [string, ReactNode][];
}) {
  return (
    <div className="flex min-w-0 flex-col rounded-xl border border-border bg-card shadow-[0_1px_2px_rgb(0_0_0/0.03)]">
      <div className="grid gap-2 px-4 pt-3.5 pb-3">
        <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {Icon && <Icon className="size-3.5" aria-hidden="true" />}{label}
        </dt>
        <dd className="flex items-baseline gap-1 text-[28px] leading-none font-semibold tracking-[-0.03em] tabular-nums text-foreground">
          {value}{unit && <span className="text-sm font-medium tracking-normal text-muted-foreground">{unit}</span>}
        </dd>
        {(delta || detail) && (
          <dd className="flex min-h-5 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            {delta && <DeltaChip delta={delta} period={period} />}
            {detail && <span className="truncate">{detail}</span>}
          </dd>
        )}
        {spark && spark.length > 1 && <dd><Ticks values={spark} tone={sparkTone} /></dd>}
      </div>
      {footer && footer.length > 0 && (
        <dd className="mt-auto grid gap-1 border-t border-border px-4 py-2.5 text-xs">
          {footer.map(([name, amount]) => (
            <div key={name} className="flex items-center justify-between gap-3">
              <span className="truncate text-muted-foreground">{name}</span>
              <span className="font-medium tabular-nums text-foreground">{amount}</span>
            </div>
          ))}
        </dd>
      )}
    </div>
  );
}

export function KpiGrid({ label, children }: { label: string; children: ReactNode }) {
  return <dl aria-label={label} className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">{children}</dl>;
}

/** Change of a count between two windows, as a fraction; null when there is nothing to compare. */
export function change(current: number, previous: number): number | null {
  return previous > 0 ? (current - previous) / previous : null;
}
