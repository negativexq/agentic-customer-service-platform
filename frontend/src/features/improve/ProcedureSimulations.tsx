import { Play } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/primitives/button";
import { ApiError } from "../../api/client";
import { useAgentConfig } from "../studio/queries";
import { useRunSimulation, useScenarios } from "./queries";
import { ResultRow } from "./SimulationsPage";
import type { Scenario, SimulationResult } from "./types";
import { humanize } from "../runs/runPresentation";

function Outcome({ result }: { result: SimulationResult | undefined }) {
  if (!result) return <span className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground ring-1 ring-border">Not run</span>;
  return result.passed
    ? <span className="rounded bg-success/10 px-1.5 py-0.5 text-[11px] text-success">Pass</span>
    : <span className="rounded bg-danger/10 px-1.5 py-0.5 text-[11px] text-danger">Fail</span>;
}

/** Scripted conversations for one procedure, run in place against the published config or the draft. */
export function ProcedureSimulations({ intent, scenarios: given }: { intent: string; scenarios?: Scenario[] }) {
  const library = useScenarios();
  const config = useAgentConfig();
  const run = useRunSimulation();
  const [results, setResults] = useState<Record<string, SimulationResult>>({});
  const [open, setOpen] = useState<string | null>(null);
  const hasDraft = Boolean(config.data?.draft);
  const [choice, setChoice] = useState<"published" | "draft" | null>(null);
  const against = choice ?? (hasDraft ? "draft" : "published");
  const scenarios = (given ?? library.data ?? []).filter((scenario) => scenario.expected_intent === intent);

  const start = (ids: string[]) => run.mutate(
    { scenarioIds: ids, against, label: `${humanize(intent)} procedure` },
    { onSuccess: (done) => setResults((current) => ({ ...current, ...Object.fromEntries((done.results ?? []).map((result) => [result.scenario_id, result])) })) },
  );
  const failed = scenarios.filter((scenario) => results[scenario.id] && !results[scenario.id].passed).length;
  const error = run.error instanceof ApiError ? (run.error.status === 409 ? "There is no draft to test." : run.error.message) : run.error ? "The simulation could not run." : null;

  return (
    <section className="grid content-start gap-2" aria-label="Simulations">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-foreground">Simulations</h3>
        <div className="flex items-center gap-2">
          <div role="radiogroup" aria-label="Test against" className="inline-flex rounded-md border border-border bg-muted/40 p-0.5">
            {(["published", "draft"] as const).map((value) => (
              <button key={value} type="button" role="radio" aria-checked={against === value} disabled={value === "draft" && !hasDraft} onClick={() => setChoice(value)}
                className={`h-[22px] rounded px-2 text-xs disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${against === value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                {value === "published" ? "Published" : "Draft"}
              </button>
            ))}
          </div>
          <Button size="sm" disabled={scenarios.length === 0 || run.isPending} onClick={() => start(scenarios.map((scenario) => scenario.id))}>
            <Play data-icon="inline-start" aria-hidden />{run.isPending ? "Running…" : "Run all"}
          </Button>
        </div>
      </div>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      {scenarios.length === 0 ? (
        <p className="text-sm text-muted-foreground">{library.isPending && !given ? "Loading scenarios…" : "No scripted conversations target this request type yet."}</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          {Object.keys(results).length > 0 && (
            <p className="border-b border-border px-4 py-2 text-xs text-muted-foreground" aria-live="polite">
              {failed === 0 ? "Every scenario that ran passed." : `${failed} failing against the ${against === "draft" ? "draft" : "published config"}.`}
            </p>
          )}
          <ul className="divide-y divide-border">
            {scenarios.map((scenario) => {
              const result = results[scenario.id];
              return result ? (
                <ResultRow key={scenario.id} result={result} open={open === scenario.id} onToggle={() => setOpen((current) => (current === scenario.id ? null : scenario.id))} />
              ) : (
                <li key={scenario.id} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">“{scenario.turns[0]}”</span>
                  <Outcome result={undefined} />
                  <Button size="icon-xs" variant="ghost" aria-label={`Run ${scenario.id}`} disabled={run.isPending} onClick={() => start([scenario.id])}><Play aria-hidden /></Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}

export { Outcome as SimulationOutcome };
