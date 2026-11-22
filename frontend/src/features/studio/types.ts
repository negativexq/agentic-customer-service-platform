/** Agent studio contracts: mirror app/ui/studio_schemas.py and app/agent_config/schemas.py. */
export type ActionMode = "enabled" | "confirm" | "disabled";

export const intents = [
  "customer_lookup", "order_lookup", "order_list", "ticket_lookup", "ticket_list", "ticket_create",
  "order_cancel", "refund_request", "human_escalation", "capability_question", "refund_policy",
  "cancellation_policy", "shipping_policy", "support_faq", "refund_eligibility", "cancellation_explanation",
  "memory_remember", "memory_forget", "unknown",
] as const;
export type Intent = (typeof intents)[number];

export type GuidanceRule = {
  id: string;
  title: string;
  instruction: string;
  intents: Intent[];
  enabled: boolean;
  source: "manual" | "coaching";
  source_run_id?: string | null;
};

/** offer_first: offer to help once before connecting a person, unless the matter is sensitive, the customer is upset, or they ask again. */
export type HandoffMode = "offer_first" | "immediate";
export type AgentConfig = { guidance: GuidanceRule[]; actions: Record<string, ActionMode>; handoff?: HandoffMode };

export type ConfigVersion = {
  version: number;
  status: "draft" | "published" | "archived";
  note: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  published_by: string | null;
  published_at: string | null;
};

export type AgentConfigState = {
  published: AgentConfig;
  published_version: number | null;
  draft: AgentConfig | null;
  draft_version: number | null;
  versions: ConfigVersion[];
};

export type KnowledgeDocument = {
  document_id: string;
  title: string;
  category: string;
  origin: "built_in" | "console";
  edited_in_console: boolean;
  archived: boolean;
  section_count: number;
  chunk_count: number;
  word_count: number;
  updated_at: string | null;
  updated_by: string | null;
  runs_citing: number;
  last_cited_at: string | null;
};

export type KnowledgeDocumentDetail = {
  document_id: string;
  title: string;
  category: string;
  origin: "built_in" | "console";
  archived: boolean;
  content: string;
  sections: { section: string; chunks: { chunk_id: string; content: string; runs_citing: number }[] }[];
};

export type KnowledgeSearchResult = {
  query: string;
  index: string;
  hits: { chunk_id: string; document_id: string; title: string; section: string; score: number; content: string }[];
};

export type KnowledgeIndexStatus = {
  backend: string;
  embedding_provider: string;
  publish_allowed: boolean;
  publish_blocked_reason: string | null;
  pending_changes: boolean;
  active_snapshot: string | null;
  active_chunk_count: number | null;
  corpus_chunk_count: number;
};

export type ActionUsage = {
  calls: number;
  succeeded: number;
  failed: number;
  awaiting_confirmation: number;
  denied: number;
  last_used_at: string | null;
};

export type ActionView = {
  name: string;
  description: string;
  operation: "read" | "write";
  risk_level: number;
  risk_label: string;
  default_policy: string;
  allowed_modes: ActionMode[];
  locked: boolean;
  published_mode: ActionMode;
  draft_mode: ActionMode | null;
  safeguards: string[];
  inputs: { name: string; type: string; required: boolean; description: string | null }[];
  procedures: string[];
  usage: ActionUsage;
};

export type ProcedureStepKind = "understand" | "collect" | "lookup" | "knowledge" | "policy" | "confirm" | "act" | "respond" | "handoff";

export type ProcedureView = {
  intent: Intent;
  name: string;
  summary: string;
  request_type: string;
  steps: { kind: ProcedureStepKind; title: string; detail: string; action: string | null; operator_note: string | null }[];
  actions: string[];
  knowledge: string[];
  guidance_count: number;
  usage_runs: number;
  handoff_runs: number;
  clarification_runs: number;
  failed_runs: number;
};
