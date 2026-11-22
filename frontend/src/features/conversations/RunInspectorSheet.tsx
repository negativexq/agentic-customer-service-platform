import { ExternalLink } from "lucide-react";
import { Button } from "@/components/primitives/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { Skeleton } from "@/components/primitives/skeleton";
import { Inspector } from "../../components/Inspector";
import type { AgentRun, MemoryRecord } from "../../types";
import { humanize, runStatus } from "../runs/runPresentation";
import { StatusLabel } from "../runs/StatusLabel";

type Props = {
  runId: string | null;
  run: AgentRun | null;
  loading: boolean;
  memoryRecords: MemoryRecord[];
  onClose: () => void;
  onOpenInvestigation: (runId: string) => void;
};

/** Debug view for one turn. Opened on demand so the conversation itself stays a plain chat. */
export function RunInspectorSheet({ runId, run, loading, memoryRecords, onClose, onOpenInvestigation }: Props) {
  return (
    <Sheet open={runId !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent side="right" className="w-full gap-0 p-0 data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b border-border px-5 py-4 pr-12">
          <SheetTitle className="flex items-center gap-3">
            {run ? humanize(run.intent) : "Run"}
            {run && <StatusLabel {...runStatus(run)} />}
          </SheetTitle>
          <SheetDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono text-xs">{runId}</span>
            {runId && (
              <Button variant="link" size="xs" className="h-auto p-0" onClick={() => onOpenInvestigation(runId)}>
                Open full investigation<ExternalLink aria-hidden="true" />
              </Button>
            )}
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading && !run ? (
            <div className="grid gap-3 p-5"><Skeleton className="h-8" /><Skeleton className="h-48" /></div>
          ) : (
            <Inspector run={run} memoryRecords={memoryRecords} bare />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
