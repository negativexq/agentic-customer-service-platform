import { useLayoutEffect, useRef, useState } from "react";

/** A segmented choice whose raised indicator slides to the selected option. */
export function Segmented<T extends string | number>({ options, value, onChange, label }: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  const group = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    const node = group.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]');
    if (node) setIndicator({ left: node.offsetLeft, width: node.offsetWidth });
  }, [value, options.length]);

  return (
    <div ref={group} role="radiogroup" aria-label={label} className="relative inline-flex rounded-lg border border-border bg-muted/50 p-0.5">
      {indicator && (
        <span aria-hidden="true"
          className="absolute top-0.5 bottom-0.5 rounded-md bg-background shadow-[0_1px_2px_rgb(0_0_0/0.08),0_0_0_1px_var(--border)] transition-[left,width] duration-200 ease-[cubic-bezier(0.3,0.8,0.3,1)] motion-reduce:transition-none"
          style={{ left: indicator.left, width: indicator.width }} />
      )}
      {options.map((option) => (
        <button key={String(option.value)} type="button" role="radio" aria-checked={value === option.value} onClick={() => onChange(option.value)}
          className={`relative h-[26px] rounded-md px-2.5 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${value === option.value ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
          {option.label}
        </button>
      ))}
    </div>
  );
}
