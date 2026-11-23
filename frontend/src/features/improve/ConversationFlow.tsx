import { ResponsiveContainer, Sankey } from "recharts";
import type { LinkProps, NodeProps } from "recharts/types/chart/Sankey";
import { humanize } from "../runs/runPresentation";
import type { PerformanceView } from "./types";

export const outcomes = [
  { key: "ai_resolved", label: "Resolved by AI", color: "var(--success)" },
  { key: "routed", label: "Routed to a person", color: "var(--warning)" },
  { key: "handled_by_team", label: "Handled by a person", color: "var(--muted-foreground)" },
] as const;

const requestLabels: Record<string, string> = {
  knowledge_only: "Questions", read_action: "Lookups", write_action: "Changes", escalation: "Asked for a person",
  memory_action: "Preferences", informational: "About the assistant", other: "Unrecognised", not_seen_by_ai: "Not seen by AI",
};
export const requestLabel = (kind: string) => requestLabels[kind] ?? humanize(kind);

export function flowData(flows: PerformanceView["flows"]) {
  const sources = flows.filter((row) => outcomes.some((outcome) => row[outcome.key] > 0));
  const reached = outcomes.filter((outcome) => sources.some((row) => row[outcome.key] > 0));
  const nodes = [...sources.map((row) => ({ name: requestLabel(row.request_type), color: "var(--chart-1)" })), ...reached.map((outcome) => ({ name: outcome.label, color: outcome.color }))];
  const links = sources.flatMap((row, index) => reached.map((outcome, offset) => ({ source: index, target: sources.length + offset, value: row[outcome.key] })).filter((link) => link.value > 0));
  return { nodes, links };
}

function Node({ x, y, width, height, payload }: NodeProps) {
  const color = (payload as unknown as { color: string }).color;
  const right = payload.depth > 0;
  return (
    <g>
      <rect x={x} y={y} width={width} height={Math.max(height, 2)} rx={2} fill={color} />
      <text x={right ? x - 8 : x + width + 8} y={y + height / 2} dy="0.35em" textAnchor={right ? "end" : "start"} className="fill-foreground text-[11px]">
        {payload.name} <tspan className="fill-muted-foreground font-mono">{payload.value}</tspan>
      </text>
    </g>
  );
}

function Link({ sourceX, targetX, sourceY, targetY, sourceControlX, targetControlX, linkWidth, payload }: LinkProps) {
  const color = (payload.target as unknown as { color: string }).color;
  return (
    <path d={`M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`}
      fill="none" stroke={color} strokeOpacity={0.4} strokeWidth={Math.max(linkWidth, 1)} />
  );
}

/** Narrow screens: one stacked bar per request type instead of a flow diagram. */
function FlowList({ flows }: { flows: PerformanceView["flows"] }) {
  return (
    <ul className="grid gap-3 py-3 sm:hidden" aria-hidden>
      {flows.filter((row) => row.conversations > 0).map((row) => (
        <li key={row.request_type} className="grid gap-1.5">
          <div className="flex items-baseline justify-between gap-3 text-xs">
            <span className="text-foreground">{requestLabel(row.request_type)}</span>
            <span className="font-mono tabular-nums text-muted-foreground">{row.conversations}</span>
          </div>
          <div className="flex h-2 overflow-hidden rounded-full bg-muted">
            {outcomes.map((outcome) => row[outcome.key] > 0 && (
              <span key={outcome.key} className="h-full" style={{ width: `${(row[outcome.key] / row.conversations) * 100}%`, background: outcome.color }} />
            ))}
          </div>
        </li>
      ))}
      <li className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {outcomes.map((outcome) => <span key={outcome.key} className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ background: outcome.color }} />{outcome.label}</span>)}
      </li>
    </ul>
  );
}

/** Where conversations of each request type ended: resolved by AI, routed to a person, or handled by one. */
export function ConversationFlow({ flows }: { flows: PerformanceView["flows"] }) {
  const data = flowData(flows);
  if (data.links.length === 0) return null;
  return (
    <>
      <div className="hidden sm:block" aria-hidden>
        <ResponsiveContainer width="100%" height={240}>
          <Sankey data={data} node={Node} link={Link} nodeWidth={8} nodePadding={18} iterations={32} margin={{ top: 12, bottom: 12, left: 8, right: 8 }} />
        </ResponsiveContainer>
      </div>
      <FlowList flows={flows} />
      <table className="sr-only">
        <caption>Where conversations ended, by request type</caption>
        <thead><tr><th>Request type</th>{outcomes.map((outcome) => <th key={outcome.key}>{outcome.label}</th>)}</tr></thead>
        <tbody>{flows.map((row) => <tr key={row.request_type}><td>{requestLabel(row.request_type)}</td>{outcomes.map((outcome) => <td key={outcome.key}>{row[outcome.key]}</td>)}</tr>)}</tbody>
      </table>
    </>
  );
}
