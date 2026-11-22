import { CheckCircle2, Play, ShieldAlert, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/primitives/badge";
import { Button } from "@/components/primitives/button";
import { Input } from "@/components/primitives/input";
import { Skeleton } from "@/components/primitives/skeleton";
import { ApiError } from "../../api/client";
import { PageHeader } from "../../components/layout/PageHeader";
import { formatRelative, humanize } from "../runs/runPresentation";
import { useRunSimulation, useScenarios, useSimulation, useSimulations } from "./queries";
import { EmptyNote, Section } from "./shared";
import type { Scenario, SimulationResult, SimulationRun } from "./types";

export type ResultChange = "regressed" | "fixed" | null;

export function compareRuns(current: SimulationResult[], previous: SimulationResult[] | null | undefined): Record<string, ResultChange> {
  const before = new Map((previous ?? []).map((result) => [result.scenario_id, result.passed]));
  return Object.fromEntries(current.map((result) => {
    const was = before.get(result.scenario_id);
    return [result.scenario_id, was === undefined || was === result.passed ? null : result.passed ? "fixed" : "regressed"];
  }));
}

export function ResultRow({ result, open, onToggle, change = null }: { result: SimulationResult; open: boolean; onToggle: () => void; change?: ResultChange }) {
  return (
    <li>
      <button type="button" onClick={onToggle} aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
        {result.passed ? <CheckCircle2 className="size-4 shrink-0 text-success" aria-label="Passed" /> : <XCircle className="size-4 shrink-0 text-danger" aria-label="Failed" />}
        <span className="grid min-w-0 flex-1">
          <span className="truncate font-mono text-xs text-foreground">{result.scenario_id}</span>
          {result.transcript[0] && <span className="truncate text-xs text-muted-foreground">“{result.transcript[0].user}”</span>}
        </span>
        {change === "regressed" && <span className="rounded bg-danger/10 px-1.5 py-0.5 text-[11px] text-danger">Newly failing</span>}
        {change === "fixed" && <span className="rounded bg-success/10 px-1.5 py-0.5 text-[11px] text-success">Fixed</span>}
        {result.critical_safety && <ShieldAlert className="size-3.5 shrink-0 text-warning" aria-label="Safety-critical" />}
        <span className="shrink-0 text-xs text-muted-foreground">{humanize(result.category)}</span>
      </button>
      {open && (
        <div className="grid gap-3 border-t border-border bg-muted/20 px-4 py-3">
          {!result.passed && result.failure_reasons.length > 0 && (
            <ul className="grid gap-1 text-xs text-danger">{result.failure_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          )}
          <ol className="grid gap-2">
            {result.transcript.map((turn, index) => (
              <li key={index} className="grid gap-1.5 text-sm">
                <p className="ml-auto max-w-[85%] rounded-lg bg-muted px-3 py-1.5 text-foreground">{turn.user}</p>
                <div className="max-w-[85%] rounded-lg border border-border bg-card px-3 py-1.5">
                  <p className="whitespace-pre-wrap text-foreground">{turn.assistant}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {[turn.intent && humanize(turn.intent), turn.tool, turn.status && humanize(turn.status)].filter(Boolean).join(" · ")}
                  </p>
                </div>
              </li>
            ))}
          </ol>
          <p className="text-xs text-muted-foreground">Tools executed: {result.executed_tools.length ? result.executed_tools.join(", ") : "none"}</p>
        </div>
      )}
    </li>
  );
}

function RunResults({ run, previous }: { run: SimulationRun; previous: SimulationRun | null }) {
  const [open, setOpen] = useState<string | null>(null);
  const [failedOnly, setFailedOnly] = useState(run.passed_count < run.scenario_count);
  const changes = compareRuns(run.results ?? [], previous?.results);
  const regressed = Object.values(changes).filter((change) => change === "regressed").length;
  const fixed = Object.values(changes).filter((change) => change === "fixed").length;
  const results = (run.results ?? []).filter((result) => !failedOnly || !result.passed || changes[result.scenario_id]);
  return (
    <Section title={run.label || `Simulation ${run.id}`}
      description={`${run.passed_count} of ${run.scenario_count} passed against ${run.against === "draft" ? "the draft" : `published v${run.config_version ?? "default"}`}`}
      actions={<label className="flex items-center gap-1.5 text-xs text-muted-foreground"><input type="checkbox" checked={failedOnly} onChange={(event) => setFailedOnly(event.target.checked)} className="size-3.5 accent-primary" />Failed only</label>}>
      {previous && (regressed > 0 || fixed > 0) && (
        <p className="border-b border-border px-4 py-2 text-xs text-muted-foreground">
          Compared with {previous.label || `simulation ${previous.id}`}:{" "}
          {regressed > 0 && <span className="text-danger">{regressed} newly failing</span>}
          {regressed > 0 && fixed > 0 && ", "}
          {fixed > 0 && <span className="text-success">{fixed} fixed</span>}
        </p>
      )}
      {results.length === 0 ? <EmptyNote>{failedOnly ? "Every scenario passed." : "No results recorded."}</EmptyNote> : (
        <ul className="divide-y divide-border">
          {results.map((result) => <ResultRow key={result.scenario_id} result={result} change={changes[result.scenario_id]} open={open === result.scenario_id} onToggle={() => setOpen((current) => (current === result.scenario_id ? null : result.scenario_id))} />)}
        </ul>
      )}
    </Section>
  );
}

export function ScenarioPicker({ scenarios, selected, onChange }: { scenarios: Scenario[]; selected: Set<string>; onChange: (next: Set<string>) => void }) {
  const [category, setCategory] = useState("all");
  const categories = useMemo(() => [...new Set(scenarios.map((scenario) => scenario.category))].sort(), [scenarios]);
  const visible = scenarios.filter((scenario) => category === "all" || scenario.category === category);
  const allVisible = visible.length > 0 && visible.every((scenario) => selected.has(scenario.id));
  const toggleAll = () => {
    const next = new Set(selected);
    visible.forEach((scenario) => (allVisible ? next.delete(scenario.id) : next.add(scenario.id)));
    onChange(next);
  };
  return (
    <div className="grid min-h-0">
      <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-2" role="radiogroup" aria-label="Category">
        {["all", ...categories].map((value) => (
          <button key={value} type="button" role="radio" aria-checked={category === value} onClick={() => setCategory(value)}
            className={`h-6 rounded-md border px-2 text-xs focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${category === value ? "border-foreground/20 bg-muted text-foreground" : "border-border text-muted-foreground hover:text-foreground"}`}>
            {value === "all" ? "All" : humanize(value)}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 border-b border-border px-4 py-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={allVisible} onChange={toggleAll} className="size-3.5 accent-primary" />
        Select {visible.length} shown
      </label>
      <ul className="max-h-[28rem] divide-y divide-border overflow-y-auto">
        {visible.map((scenario) => (
          <li key={scenario.id}>
            <label className="flex cursor-pointer gap-3 px-4 py-2.5 hover:bg-muted/40">
              <input type="checkbox" className="mt-0.5 size-3.5 shrink-0 accent-primary" checked={selected.has(scenario.id)}
                onChange={() => { const next = new Set(selected); if (next.has(scenario.id)) next.delete(scenario.id); else next.add(scenario.id); onChange(next); }} />
              <span className="grid min-w-0 gap-0.5">
                <span className="flex items-center gap-2">
                  <span className="truncate font-mono text-xs text-foreground">{scenario.id}</span>
                  {scenario.critical_safety && <Badge variant="outline" className="h-4 px-1 text-[10px]">Safety</Badge>}
                </span>
                <span className="truncate text-sm text-muted-foreground">“{scenario.turns[0]}”{scenario.turns.length > 1 ? ` +${scenario.turns.length - 1} turns` : ""}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SimulationsPage() {
  const scenarios = useScenarios();
  const runs = useSimulations();
  const runSimulation = useRunSimulation();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [against, setAgainst] = useState<"published" | "draft">("published");
  const [label, setLabel] = useState("");
  const [activeId, setActiveId] = useState<number | null>(null);
  const shownId = activeId ?? runs.data?.[0]?.id ?? null;
  const active = useSimulation(shownId);
  const shownIndex = runs.data?.findIndex((item) => item.id === shownId) ?? -1;
  const previousId = shownIndex >= 0 ? runs.data?.[shownIndex + 1]?.id ?? null : null;
  const previous = useSimulation(previousId);
  const error = runSimulation.error instanceof ApiError
    ? runSimulation.error.status === 409 ? "There is no draft to test. Change something in Train first, or run against published." : runSimulation.error.message
    : runSimulation.error ? "The simulation could not run." : null;

  const start = () => runSimulation.mutate({ scenarioIds: [...selected], against, label: label.trim() }, { onSuccess: (run) => setActiveId(run.id) });

  return (
    <div className="min-w-0">
      <PageHeader title="Simulations" description="Replay scripted customer conversations against the published assistant or your unpublished draft. Simulations use a deterministic model, so they cost nothing and give the same answer every time." />
      <div className="grid gap-4 p-4 sm:p-6 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <div className="grid content-start gap-4">
          <Section title="Scenarios" description={scenarios.data ? `${selected.size} of ${scenarios.data.length} selected` : undefined}>
            <div className="grid gap-3 border-b border-border p-4">
              <div role="radiogroup" aria-label="Test against" className="inline-flex w-fit rounded-lg border border-border bg-muted/40 p-0.5">
                {(["published", "draft"] as const).map((value) => (
                  <button key={value} type="button" role="radio" aria-checked={against === value} onClick={() => setAgainst(value)}
                    className={`h-7 rounded-md px-2.5 text-xs focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${against === value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    {value === "published" ? "Published" : "Draft"}
                  </button>
                ))}
              </div>
              <Input value={label} maxLength={120} onChange={(event) => setLabel(event.target.value)} placeholder="Label, for example ‘Refund guidance v2’" aria-label="Simulation label" />
              {error && <p role="alert" className="text-sm text-danger">{error}</p>}
              <Button disabled={selected.size === 0 || runSimulation.isPending} onClick={start}>
                <Play data-icon="inline-start" aria-hidden />{runSimulation.isPending ? `Running ${selected.size} scenarios…` : `Run ${selected.size || ""} scenario${selected.size === 1 ? "" : "s"}`}
              </Button>
            </div>
            {scenarios.isError ? <p role="alert" className="px-4 py-3 text-sm text-danger">The scenario library is unavailable.</p>
              : !scenarios.data ? <Skeleton className="m-4 h-60" />
              : <ScenarioPicker scenarios={scenarios.data} selected={selected} onChange={setSelected} />}
          </Section>
          <Section title="Past simulations">
            {!runs.data ? <Skeleton className="m-4 h-24" /> : runs.data.length === 0 ? <EmptyNote>No simulations yet.</EmptyNote> : (
              <ul className="divide-y divide-border">
                {runs.data.map((run) => {
                  const passed = run.passed_count === run.scenario_count;
                  return (
                    <li key={run.id}>
                      <button type="button" aria-pressed={shownId === run.id} onClick={() => setActiveId(run.id)}
                        className={`flex w-full items-center gap-3 px-4 py-2.5 text-left focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${shownId === run.id ? "bg-muted" : "hover:bg-muted/50"}`}>
                        <span className={`size-1.5 shrink-0 rounded-full ${passed ? "bg-success" : "bg-danger"}`} aria-hidden />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm text-foreground">{run.label || `Simulation ${run.id}`}</span>
                          <span className="text-xs text-muted-foreground">{run.against === "draft" ? "Draft" : `Published v${run.config_version ?? "default"}`} · {formatRelative(run.started_at)}</span>
                        </span>
                        <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">{run.passed_count}/{run.scenario_count}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>
        </div>
        <div className="min-w-0">
          {shownId === null ? <div className="rounded-xl border border-dashed border-border"><EmptyNote>Pick scenarios and run a simulation to see each transcript here.</EmptyNote></div>
            : !active.data ? <Skeleton className="h-80 w-full rounded-xl" />
            : <RunResults key={active.data.id} run={active.data} previous={previous.data ?? null} />}
        </div>
      </div>
    </div>
  );
}
