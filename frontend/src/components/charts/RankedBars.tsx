export type RankedItem = { label: string; value: number; display?: string; detail?: string; color?: string; onSelect?: () => void; selected?: boolean };

/**
 * A ranked list: label and number on one line, a bar under it on a hatched track.
 * `max` defaults to the largest value; pass 1 for shares already expressed as fractions.
 */
export function RankedBars({ items, max, label }: { items: RankedItem[]; max?: number; label: string }) {
  const top = max ?? Math.max(1, ...items.map((item) => item.value));
  return (
    <ul className="grid gap-3 p-4" aria-label={label}>
      {items.map((item) => {
        const width = top > 0 ? Math.min(100, Math.max(item.value > 0 ? 2 : 0, (item.value / top) * 100)) : 0;
        const body = (
          <>
            <div className="flex items-baseline justify-between gap-3 text-[13px]">
              <span className="inline-flex min-w-0 items-center gap-2 text-foreground">
                {item.color && <span className="size-2 shrink-0 rounded-[2px]" style={{ background: item.color }} aria-hidden="true" />}
                <span className="truncate">{item.label}</span>
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                <span className="font-medium text-foreground">{item.display ?? item.value}</span>{item.detail ? <span> · {item.detail}</span> : null}
              </span>
            </div>
            <div className="track-hatched mt-1.5 h-1.5 overflow-hidden rounded-full">
              <div className="h-full rounded-full transition-[width] duration-500 ease-out" style={{ width: `${width}%`, background: item.color ?? "var(--chart-1)" }} />
            </div>
          </>
        );
        return (
          <li key={item.label}>
            {item.onSelect ? (
              <button type="button" onClick={item.onSelect} aria-pressed={item.selected}
                className={`-mx-2 block w-[calc(100%+1rem)] rounded-lg px-2 py-1 text-left transition-colors hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${item.selected ? "bg-muted" : ""}`}>
                {body}
              </button>
            ) : body}
          </li>
        );
      })}
    </ul>
  );
}
