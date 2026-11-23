import { useEffect, useRef, useState } from "react";
import { UI_LOCALE } from "@/lib/utils";

export type DotSeries = { key: string; label: string; color: string };
export type DotPoint = { label: string; values: Record<string, number> };

const GAP = 3;
const CELL = 7;

function niceMax(value: number): number {
  if (value <= 4) return Math.max(1, value);
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / step) * step;
}

/** Cells per series for one column: rounded on the running total so the column height stays honest. */
function cellsFor(values: Record<string, number>, series: DotSeries[], max: number, rows: number): string[] {
  const cells: string[] = [];
  let running = 0;
  let filled = 0;
  for (const item of series) {
    running += values[item.key] ?? 0;
    const target = running > 0 ? Math.max(1, Math.round((running / max) * rows)) : 0;
    for (; filled < target && filled < rows; filled += 1) cells.push(item.color);
  }
  return cells;
}

/**
 * A daily series drawn as columns of small cells, empty cells kept as a faint grid.
 * Stacks several series bottom to top; hovering a column shows its numbers.
 */
export function DotMatrixChart({ data, series, rows = 16, label }: { data: DotPoint[]; series: DotSeries[]; rows?: number; label: string }) {
  const [active, setActive] = useState<number | null>(null);
  const plot = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = plot.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  // Each day is as many square dot columns as fit, with one empty column between days.
  const perDay = width ? Math.max(1, Math.floor((width / Math.max(1, data.length) + GAP) / (CELL + GAP)) - 1) : 1;
  const totals = data.map((point) => series.reduce((sum, item) => sum + (point.values[item.key] ?? 0), 0));
  const max = niceMax(Math.max(0, ...totals));
  const height = rows * CELL + (rows - 1) * GAP;
  const ticks = [max, Math.round(max / 2), 0];
  const every = Math.max(1, Math.ceil(data.length / 6));
  const format = (value: number) => value.toLocaleString(UI_LOCALE);

  return (
    <figure className="grid gap-3 px-4 pt-4 pb-3" aria-label={label}>
      <div className="flex gap-3">
        <div className="flex w-6 shrink-0 flex-col justify-between text-right font-mono text-[10px] text-muted-foreground tabular-nums" style={{ height }} aria-hidden="true">
          {ticks.map((tick) => <span key={tick} className="-my-1.5 leading-3">{format(tick)}</span>)}
        </div>
        <div ref={plot} className="relative flex min-w-0 flex-1 items-end justify-between" style={{ height }} onMouseLeave={() => setActive(null)}>
          {data.map((point, column) => {
            const cells = cellsFor(point.values, series, max, rows);
            const dimmed = active !== null && active !== column;
            return (
              <div
                key={`${point.label}-${column}`}
                className="flex h-full min-w-0 flex-1 cursor-default justify-center gap-[3px] transition-opacity duration-150"
                style={{ opacity: dimmed ? 0.45 : 1 }}
                onMouseEnter={() => setActive(column)}
                role="img"
                aria-label={`${point.label}: ${series.map((item) => `${item.label} ${point.values[item.key] ?? 0}`).join(", ")}`}
              >
                {Array.from({ length: perDay }, (_, sub) => (
                  <div key={sub} className="flex h-full flex-col-reverse gap-[3px]">
                    {Array.from({ length: rows }, (_, row) => (
                      <span
                        key={row}
                        className="dot-cell block shrink-0 rounded-[1.5px] bg-foreground/[0.07]"
                        style={{ width: CELL, height: CELL, ...(cells[row] ? { background: cells[row], animationDelay: `${column * 12 + row * 16}ms` } : null) }}
                        data-filled={cells[row] ? "" : undefined}
                      />
                    ))}
                  </div>
                ))}
              </div>
            );
          })}
          {active !== null && data[active] && (
            <div
              className="pointer-events-none absolute -top-2 z-10 min-w-36 -translate-x-1/2 -translate-y-full rounded-lg border border-border bg-popover px-2.5 py-2 text-xs shadow-lg"
              style={{ left: `${((active + 0.5) / data.length) * 100}%` }}
            >
              <div className="mb-1 font-medium text-foreground">{data[active].label}</div>
              {series.map((item) => (
                <div key={item.key} className="flex items-center justify-between gap-4 text-muted-foreground">
                  <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-[2px]" style={{ background: item.color }} />{item.label}</span>
                  <span className="font-mono text-foreground tabular-nums">{format(data[active].values[item.key] ?? 0)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="flex gap-3" aria-hidden="true">
        <div className="w-6 shrink-0" />
        <div className="flex min-w-0 flex-1 font-mono text-[10px] text-muted-foreground">
          {data.map((point, index) => (
            <div key={index} className="relative h-3 min-w-0 flex-1">
              {index % every === 0 && <span className="absolute left-1/2 -translate-x-1/2 whitespace-nowrap">{point.label}</span>}
            </div>
          ))}
        </div>
      </div>
      {series.length > 1 && (
        <figcaption className="flex flex-wrap items-center gap-2 pl-9">
          {series.map((item) => (
            <span key={item.key} className="inline-flex h-6 items-center gap-1.5 rounded-md border border-border px-2 text-xs text-muted-foreground">
              <span className="size-2 rounded-[2px]" style={{ background: item.color }} aria-hidden="true" />{item.label}
            </span>
          ))}
        </figcaption>
      )}
    </figure>
  );
}
