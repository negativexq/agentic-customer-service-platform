/** Plain sentences for what failed in a run and what the assistant did about it. */

const failures: Record<string, string> = {
  llm_timeout: "The language model took too long to answer",
  llm_unavailable: "The language model could not be reached",
  llm_malformed_output: "The language model's answer could not be read",
  database_transient: "The database briefly refused a request",
  database_unavailable: "The database could not be reached",
  tool_timeout: "An action took too long to finish",
  tool_transient_failure: "An action failed and may work on a retry",
  tool_permanent_failure: "An action failed and will not work on a retry",
  retrieval_timeout: "The knowledge search took too long",
  retrieval_unavailable: "The knowledge search could not be reached",
  retrieval_empty: "The knowledge search found nothing",
  reranker_failure: "Ranking the knowledge results failed",
  embedding_failure: "Preparing the search query failed",
  policy_failure: "The policy check could not run",
  memory_failure: "Reading or saving customer memory failed",
  unknown_dependency_failure: "A connected service failed",
  circuit_open: "Model calls were paused after repeated failures",
  bulkhead_rejected: "Too many requests were running at once",
  rate_limited: "The model provider limited the request rate",
  retry_budget_exhausted: "Retries ran out before the request succeeded",
  runtime_error: "The run stopped on an unexpected error",
  tool_failure: "An action the assistant called failed",
};

const recoveries: Record<string, string> = {
  clarify: "Asked the customer to rephrase",
  degraded: "Answered without the knowledge search",
  continue_without_memory: "Continued without customer memory",
  fail_safely: "Stopped without changing anything",
  deny: "Stopped without changing anything",
  no_replay: "Did not retry, to avoid doing it twice",
};

export function failureText(category: string, details: string[] = []): string {
  if (category.startsWith("degraded_")) return `Ran with ${category.slice(9).replace(/_/g, " ")} unavailable`;
  const text = failures[category] ?? "The run did not finish";
  return details.length ? `${text} (${details.join(", ")})` : text;
}

export function recoveryText(action: string | null): string {
  if (!action) return "—";
  return recoveries[action] ?? action.replace(/_/g, " ");
}
