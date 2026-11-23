import { createColumnHelper, createSortedRowModel, rowSortingFeature, sortFn_alphanumeric, tableFeatures, useTable } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, Inbox, Search } from "lucide-react";
import { useMemo, useState, type MouseEvent } from "react";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/primitives/empty";
import { Input } from "@/components/primitives/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/primitives/select";
import { Skeleton } from "@/components/primitives/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/primitives/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/primitives/toggle-group";
import { PageHeader } from "../../components/layout/PageHeader";
import type { AgentRun } from "../../types";
import { DecisionPath } from "./DecisionPath";
import { formatDuration, formatRelative, formatTokens, humanize, isRecorded, runLatencyMs, runStatus } from "./runPresentation";
import { StatusLabel } from "./StatusLabel";

type Source = "all" | "live" | "recorded";

const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric },
});
const helper = createColumnHelper<typeof features, AgentRun>();
const columns = helper.columns([
  helper.accessor((run) => runStatus(run).label, {
    id: "status",
    header: "Status",
    cell: ({ row }) => <StatusLabel {...runStatus(row.original)} />,
  }),
  helper.accessor("intent", {
    header: "Run",
    cell: ({ row }) => (
      <div className="grid min-w-0 gap-0.5">
        <span className="truncate font-medium text-foreground">{humanize(row.original.intent)}</span>
        <a
          href={`/runs/${encodeURIComponent(row.original.run_id)}`}
          className="truncate rounded font-mono text-xs text-muted-foreground hover:text-foreground focus-visible:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none pointer-coarse:min-h-8 pointer-coarse:leading-8"
          data-run-link
        >
          {row.original.run_id}
        </a>
      </div>
    ),
  }),
  helper.display({ id: "path", header: "Decision path", cell: ({ row }) => <DecisionPath run={row.original} /> }),
  helper.accessor((run) => run.provider_metadata?.model ?? run.model ?? (isRecorded(run) ? "Recorded" : "—"), {
    id: "model",
    header: "Model",
    cell: ({ getValue }) => <span className="font-mono text-xs text-muted-foreground">{getValue()}</span>,
  }),
  helper.accessor((run) => runLatencyMs(run), {
    id: "latency",
    header: "Latency",
    cell: ({ getValue }) => <span className="font-mono text-xs tabular-nums">{formatDuration(getValue())}</span>,
  }),
  helper.accessor((run) => (run.provider_metadata?.input_tokens ?? 0) + (run.provider_metadata?.output_tokens ?? 0), {
    id: "tokens",
    header: "Tokens",
    cell: ({ row }) => <span className="font-mono text-xs tabular-nums text-muted-foreground">{formatTokens(row.original)}</span>,
  }),
  helper.accessor("started_at", {
    header: "Started",
    cell: ({ getValue }) => <span className="whitespace-nowrap text-xs text-muted-foreground">{formatRelative(getValue())}</span>,
  }),
]);

type RunsPageProps = {
  runs: AgentRun[];
  loading?: boolean;
  error?: string | null;
  onOpenRun: (runId: string) => void;
};

export function RunsPage({ runs, loading = false, error = null, onOpenRun }: RunsPageProps) {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<Source>("all");
  const [status, setStatus] = useState("all");

  const statuses = useMemo(() => Array.from(new Set(runs.map((run) => runStatus(run).label))).sort(), [runs]);
  const data = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return runs.filter((run) => {
      if (source === "live" && isRecorded(run)) return false;
      if (source === "recorded" && !isRecorded(run)) return false;
      if (status !== "all" && runStatus(run).label !== status) return false;
      if (!needle) return true;
      return [run.run_id, run.intent, run.conversation_id, run.request_type].some((value) => value?.toLowerCase().includes(needle));
    });
  }, [runs, query, source, status]);

  const table = useTable({ features, columns, data, initialState: { sorting: [{ id: "started_at", desc: true }] } });
  const liveCount = runs.filter((run) => !isRecorded(run)).length;

  const openFromRow = (event: MouseEvent<HTMLTableRowElement>, runId: string) => {
    const link = (event.target as HTMLElement).closest("a[data-run-link]");
    if (link && (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0)) return;
    event.preventDefault();
    onOpenRun(runId);
  };

  return (
    <>
      <PageHeader
        title="Runs"
        description="Every agent decision with its proposal, policy outcome, granted authority, and result."
        actions={<span className="text-xs text-muted-foreground">{liveCount} live · {runs.length - liveCount} recorded</span>}
      >
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter by run, intent, or conversation" className="h-8 pl-8" aria-label="Filter runs" />
          </div>
          <ToggleGroup value={[source]} onValueChange={(value) => setSource((value[0] as Source) ?? "all")} variant="outline" size="sm" aria-label="Run source">
            <ToggleGroupItem value="all">All</ToggleGroupItem>
            <ToggleGroupItem value="live">Live</ToggleGroupItem>
            <ToggleGroupItem value="recorded">Recorded</ToggleGroupItem>
          </ToggleGroup>
          <Select value={status} onValueChange={(value) => setStatus(value ?? "all")}>
            <SelectTrigger size="sm" className="w-48" aria-label="Status"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {statuses.map((label) => <SelectItem key={label} value={label}>{label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </PageHeader>
      <div className="min-w-0 flex-1 overflow-x-auto">
        {error ? (
          <Empty className="py-16"><EmptyHeader><EmptyTitle>Runs could not be loaded</EmptyTitle><EmptyDescription>{error}</EmptyDescription></EmptyHeader></Empty>
        ) : loading && runs.length === 0 ? (
          <div className="grid gap-2 p-4 sm:p-6">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-10 w-full" />)}</div>
        ) : data.length === 0 ? (
          <Empty className="py-16">
            <EmptyHeader>
              <EmptyMedia variant="icon"><Inbox aria-hidden="true" /></EmptyMedia>
              <EmptyTitle>{runs.length === 0 ? "No runs yet" : "No runs match these filters"}</EmptyTitle>
              <EmptyDescription>{runs.length === 0 ? "Send a message from Conversations or Playground to record the first run." : "Clear the search or pick another status."}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <Table className="min-w-[860px]">
            <TableHeader>
              {table.getHeaderGroups().map((group) => (
                <TableRow key={group.id} className="hover:bg-transparent">
                  {group.headers.map((header) => {
                    const sorted = header.column.getIsSorted();
                    const sortable = header.column.getCanSort();
                    return (
                      <TableHead key={header.id} className="h-9 px-4 first:pl-4 sm:first:pl-6" aria-sort={sorted ? (sorted === "desc" ? "descending" : "ascending") : undefined}>
                        {sortable ? (
                          <button type="button" className="-mx-1 -my-1 inline-flex min-h-7 min-w-7 items-center gap-1 rounded px-1 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none pointer-coarse:min-h-9" onClick={header.column.getToggleSortingHandler()}>
                            <table.FlexRender header={header} />
                            {sorted === "asc" ? <ArrowUp className="size-3" aria-hidden="true" /> : sorted === "desc" ? <ArrowDown className="size-3" aria-hidden="true" /> : null}
                          </button>
                        ) : <table.FlexRender header={header} />}
                      </TableHead>
                    );
                  })}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} className="cursor-pointer" onClick={(event) => openFromRow(event, row.original.run_id)}>
                  {row.getAllCells().map((cell) => (
                    <TableCell key={cell.id} className="max-w-[320px] px-4 py-2.5 first:pl-4 sm:first:pl-6"><table.FlexRender cell={cell} /></TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </>
  );
}
