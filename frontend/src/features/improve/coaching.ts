import type { GuidanceSeed } from "../studio/GuidancePage";
import { intents, type Intent } from "../studio/types";

const asIntent = (value: string | null | undefined): Intent[] =>
  value && (intents as readonly string[]).includes(value) ? [value as Intent] : [];

/** Turn something an operator saw into a draft guidance rule they finish writing. */
export function coachingSeed(input: { title: string; context: string; intent?: string | null; runId?: string | null }): GuidanceSeed {
  return {
    title: input.title.slice(0, 120),
    instruction: `When this comes up: ${input.context.trim().slice(0, 300)}\n\nDo this instead: `,
    intents: asIntent(input.intent),
    source_run_id: input.runId ?? null,
  };
}
