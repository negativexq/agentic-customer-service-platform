/** Improve-loop contracts: mirror app/ui/insights_schemas.py. */
export type PerformanceView = {
  summary: {
    window_days: number; conversations: number; ai_resolved: number; handed_off: number;
    ai_resolution_rate: number | null; handoff_rate: number | null;
    csat_average: number | null; csat_responses: number; csat_ai_average: number | null; csat_human_average: number | null;
    ai_runs: number; failed_runs: number;
  };
  daily: { day: string; conversations: number; ai_resolved: number; handed_off: number; csat_average: number | null }[];
  flows: { request_type: string; conversations: number; ai_resolved: number; routed: number; handled_by_team: number }[];
  topics: { intent: string; runs: number; conversations: number; handoffs: number; clarifications: number; failures: number; handoff_rate: number | null; csat_average: number | null }[];
};

export type GapKind = "content" | "clarification" | "action" | "handoff";
export type GapFix = "knowledge" | "guidance" | "actions" | "procedures";
export type Gap = {
  kind: GapKind; key: string; title: string; description: string; intent: string | null; count: number; fix: GapFix;
  examples: { question: string; conversation_pk: number | null; run_id: string | null; at: string }[];
};

export type ErrorLog = {
  window_days: number; total_runs: number; failed_runs: number;
  categories: { category: string; count: number; last_seen: string }[];
  daily: ({ day: string; total: number } & Record<string, number | string>)[];
  errors: { run_id: string; conversation_id: string; customer_id: number; at: string; intent: string; status: string; category: string; failed_tools: string[]; degraded_components: string[]; recovery_action: string | null }[];
};

export type ReviewStatus = "open" | "reviewed" | "dismissed" | "coached";
export type MonitorSender = "customer" | "ai" | "operator" | "any";
export type Monitor = {
  key: string; name: string; description: string; severity: "low" | "medium" | "high"; built_in: boolean; enabled: boolean;
  sender_type: MonitorSender | null; phrases: string[]; open_flags: number; total_flags: number;
};
export type Flag = {
  monitor_key: string; monitor_name: string; severity: string; conversation_pk: number; conversation_id: string; customer_id: number;
  customer_name: string; detail: string; excerpt: string | null; at: string; status: ReviewStatus; note: string; run_id: string | null; intent: string | null;
};
export type QualityView = { window_days: number; conversations: number; monitors: Monitor[]; flags: Flag[] };
export type MonitorInput = { name: string; description: string; sender_type: MonitorSender; phrases: string[]; severity: "low" | "medium" | "high"; enabled: boolean };

export type Scenario = {
  id: string; category: string; customer_id: number; turns: string[]; expected_intent: string | null; completion: string;
  expected_tools: string[]; forbidden_tools: string[]; critical_safety: boolean;
};
export type SimulationResult = {
  scenario_id: string; category: string; passed: boolean; critical_safety: boolean; failure_reasons: string[];
  selected_tools: string[]; executed_tools: string[]; latency_ms: number;
  transcript: { user: string; assistant: string; intent: string | null; tool: string | null; status: string | null }[];
};
export type SimulationRun = {
  id: number; label: string; against: "published" | "draft"; config_version: number | null; started_by: string; started_at: string;
  finished_at: string | null; scenario_count: number; passed_count: number; results: SimulationResult[] | null;
};

export type MonitorPreview = {
  window_days: number; conversations: number; matched: number;
  examples: { conversation_pk: number; conversation_id: string; customer_name: string; detail: string; excerpt: string | null; at: string }[];
};
