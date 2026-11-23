import { toneDot, toneText, type Tone } from "./runPresentation";

/** A small bordered pill: a coloured dot and the state, the way the rest of the console marks status. */
export function StatusLabel({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span className={`inline-flex h-6 items-center gap-1.5 rounded-full border border-border bg-background px-2 text-xs font-medium whitespace-nowrap ${tone === "neutral" ? "text-muted-foreground" : toneText[tone]}`}>
      <span className={`size-1.5 shrink-0 rounded-full ${toneDot[tone]}`} aria-hidden="true" />
      {label}
    </span>
  );
}
