import { ArrowUpRight, Compass, Library, Workflow, Zap, type LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";

/**
 * The things the assistant is built from, each with one icon and one colour everywhere they
 * appear: the same icon as their page in the sidebar, so a chip reads as "this is an action"
 * before its name is read, and always opens that thing.
 */
export type EntityKind = "action" | "procedure" | "knowledge" | "guidance";

const kinds: Record<EntityKind, { icon: LucideIcon; color: string; noun: string }> = {
  action: { icon: Zap, color: "var(--chart-1)", noun: "action" },
  procedure: { icon: Workflow, color: "var(--chart-3)", noun: "procedure" },
  knowledge: { icon: Library, color: "var(--chart-2)", noun: "article" },
  guidance: { icon: Compass, color: "var(--chart-4)", noun: "guidance" },
};

export function entityColor(kind: EntityKind): string {
  return kinds[kind].color;
}

/** The kind's icon on a tinted square, for list rows and step markers. */
export function EntityMark({ kind, className = "size-7" }: { kind: EntityKind; className?: string }) {
  const { icon: Icon, color } = kinds[kind];
  return (
    <span aria-hidden="true" style={{ "--entity": color } as CSSProperties}
      className={`flex shrink-0 items-center justify-center rounded-lg bg-[color-mix(in_oklab,var(--entity)_12%,transparent)] text-(--entity) ring-1 ring-[color-mix(in_oklab,var(--entity)_22%,transparent)] ring-inset ${className}`}>
      <Icon className="size-[55%]" />
    </span>
  );
}

export function EntityChip({ kind, label, code, onOpen }: { kind: EntityKind; label: string; code?: string; onOpen?: () => void }) {
  const { icon: Icon, color, noun } = kinds[kind];
  const body = (
    <>
      <Icon className="size-3.5 shrink-0 text-(--entity)" aria-hidden="true" />
      <span className="truncate">{label}</span>
      {code && <span className="truncate font-mono text-[10.5px] font-normal text-muted-foreground">{code}</span>}
      {onOpen && <ArrowUpRight className="size-3 shrink-0 text-muted-foreground transition-transform duration-150 group-hover/chip:translate-x-px group-hover/chip:-translate-y-px group-hover/chip:text-foreground motion-reduce:transition-none" aria-hidden="true" />}
    </>
  );
  const className = "group/chip inline-flex h-7 max-w-full items-center gap-1.5 rounded-md border border-[color-mix(in_oklab,var(--entity)_28%,var(--border))] bg-[color-mix(in_oklab,var(--entity)_7%,transparent)] px-2 text-xs font-medium text-foreground pointer-coarse:h-9";
  const style = { "--entity": color } as CSSProperties;
  if (!onOpen) return <span className={className} style={style}>{body}</span>;
  return (
    <button type="button" onClick={onOpen} style={style} aria-label={`Open ${noun} ${label}`}
      className={`${className} cursor-pointer transition-colors hover:bg-[color-mix(in_oklab,var(--entity)_14%,transparent)] focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none active:scale-[0.98] motion-reduce:active:scale-100`}>
      {body}
    </button>
  );
}
