import { toneDot, type Tone } from "./runPresentation";

export function StatusLabel({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm">
      <span className={`size-1.5 shrink-0 rounded-full ${toneDot[tone]}`} aria-hidden="true" />
      {label}
    </span>
  );
}
