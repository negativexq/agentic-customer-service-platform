export type ToolEvent = {
  name: string;
  risk_level: number | null;
  status: string;
  duration_ms: number;
  result_fields: string[];
};

export type PolicyEvent = {
  event_id?: string;
  request_id?: string;
  conversation_id?: string;
  action_id: string | null;
  timestamp?: string | null;
  stage?: string;
  confirmation_status?: string | null;
  revalidation?: boolean;
  execution_status?: string | null;
  actor_id?: string;
  actor_type?: string;
  roles?: string[];
  effective_customer_id?: number;
  tool_name: string;
  risk_level: number;
  outcome: string;
  reason_codes: string[];
};

export type RagDocument = {
  citation_id: string;
  title: string;
  section: string;
  source: string;
  score: number;
  document_version?: string | null;
  chunk_id?: string | null;
  grounding_status?: string | null;
  retrieved_at?: string | null;
  citation_preview?: string | null;
};

export type RetrievalMetadata = {
  backend: string;
  embedding_provider: string;
  reranker_enabled: boolean;
  retrieval_count: number;
  latency_seconds: number;
  fallback_status: string;
  hybrid: boolean;
  fusion_strategy: string;
  dense_candidate_count: number;
  sparse_candidate_count: number;
};

export type AnswerGrounding = {
  status: string;
  sources_used: number;
  citation_count: number;
  citation_coverage: number | null;
  unsupported_claim_count: number;
  confidence: number | null;
  accepted: boolean;
};

export type MemoryUsage = {
  item_count: number;
  keys: string[];
  types: string[];
  retrieved?: boolean;
  retrieved_count?: number;
  items_used?: number;
  context_usage?: "context_enrichment" | "not_used";
  purpose?: "context_enrichment" | "not_used";
  decision_influence?: "context_only" | "decision_support" | "not_used";
  authority_influence?: "none" | "blocked" | "not_applicable";
};
export type TraceStage =
  | "user_request"
  | "intent_detection"
  | "context_retrieval"
  | "grounding"
  | "target_validation"
  | "policy_evaluation"
  | "confirmation"
  | "execution_authority"
  | "memory_context"
  | "routing"
  | "response"
  | "internal";
export type TraceEvent = {
  name: string;
  event_key?: string | null;
  stage: TraceStage;
  status: string;
  duration_ms: number;
  timestamp: string;
  metadata?: Record<string, string | number | boolean>;
};

export type AgentExecutionMode = "recorded_replay" | "live_proposal";
export type AgentProposal = {
  intent: string;
  suggested_action: string | null;
  extracted_fields: Record<string, string | number | boolean>;
  evidence_references: string[];
  validation: "pending" | "passed" | "rejected" | "not_recorded";
};

export type DemoConversationMessage = {
  role: "customer" | "agent";
  content: string;
  timestamp?: string | null;
  state?: string | null;
  evidence_tags: string[];
};

export type DemoMemoryEvidence = {
  category: string;
  summary: string;
  source: string;
  authority: "context_only";
  purpose: "context_enrichment";
};
export type ProviderMetadata = {
  provider: string;
  model: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
};

export type DecisionEvidence = {
  decision?: string;
  reason?: string | null;
  validation_stage?: string;
  execution_status?: string;
  authority?: string;
  grounding: { status: string; reference_type: string | null; trusted_source: string | null };
  compiler: { reason_code?: string; missing_fields?: string[]; clarification_fields?: string[]; status: string; selected_tool: string | null; requires_retrieval: boolean; reason: string | null };
  target_validation: { status: string };
  confirmation: { status: string; required: boolean; action_id: string | null; risk_level: number | null };
  write_outcome: { status: string };
};

export type AgentRun = {
  run_id: string;
  request_id: string;
  conversation_id: string;
  action_id: string | null;
  customer_id: number;
  intent: string;
  request_type: string;
  operation_type?: "agent_request" | "memory_summary" | "idempotency_replay";
  status: string;
  started_at: string;
  duration_ms: number;
  trace_id: string | null;
  path: string[];
  failure_category: string | null;
  degraded_components: string[];
  recovery_action: string | null;
  memory: MemoryUsage;
  tools: ToolEvent[];
  policy: PolicyEvent[];
  rag_documents: RagDocument[];
  retrieval_metadata: RetrievalMetadata;
  answer_grounding?: AnswerGrounding;
  trace: TraceEvent[];
  decision_reason: string | null;
  security_signal?: string | null;
  evidence: DecisionEvidence;
  execution_mode?: AgentExecutionMode;
  provider?: string;
  model?: string | null;
  fallback_message?: string | null;
  proposal?: AgentProposal | null;
  provider_metadata?: ProviderMetadata | null;
};

export type ConversationView = {
  conversation_id: string;
  customer_id: number;
  run_count: number;
  runs: AgentRun[];
  messages: { role: string; content_available: boolean; content: string | null }[];
};

export type DemoScenario = {
  scenario_id: string;
  title: string;
  purpose: string;
  expected: string;
  run: AgentRun;
  messages: DemoConversationMessage[];
  memory_evidence: DemoMemoryEvidence[];
  proposal_confidence?: number | null;
};

export type MemoryRecord = {
  id: number;
  customer_id: number;
  memory_type: string;
  normalized_key: string;
  source: string;
  status: string;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
};

export type HealthStatus = "ready" | "not_ready";
export type ComponentHealthStatus =
  | "healthy"
  | "unavailable"
  | "incompatible"
  | "configured"
  | "not_configured"
  | "not_probed";
export type Health = {
  status: HealthStatus;
  components: { name: string; status: ComponentHealthStatus; detail: string }[];
};
export type RuntimeConfig = {
  provider: string;
  model: string;
  environment: string;
  live_proposal_available: boolean;
};

export type AgentResponse = {
  conversation_id: string;
  agent_run_id: string;
  message: string;
  intent: string;
  request_type: string;
  decision_reason?: string | null;
  security_signal?: string | null;
  citations?: {
    citation_id: string;
    document_id: string;
    title: string;
    chunk_id: string;
    source: string;
    relevance_score: number;
    quoted_excerpt: string;
  }[];
  error_category: string | null;
  failure_category: string | null;
  degraded_components: string[];
  recovery_action: string | null;
  write_outcome_unknown?: boolean;
  tool_call?: { name?: string; status: string; result?: Record<string, unknown> | null } | null;
  pending_action?: { action_id?: string; tool_name?: string; status?: string } | null;
  execution_mode: AgentExecutionMode;
  provider: string;
  model: string | null;
  fallback_message: string | null;
  proposal?: AgentProposal | null;
  provider_metadata?: ProviderMetadata | null;
};

export type ConversationTurn = {
  request: string;
  response: AgentResponse;
};

export type PlaygroundRequest = {
  message: string;
  customerId: number;
  orderId: string;
  scenario: string;
  executionMode: AgentExecutionMode;
};

export type PlaygroundExecution = {
  request: PlaygroundRequest;
  requestPayload: Record<string, unknown>;
  response: AgentResponse;
  run: AgentRun;
};

export type PlaygroundHistoryItem = {
  run: AgentRun;
  scenario: string;
  orderId: string;
};
