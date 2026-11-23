import { QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { liveUpdateIntervals, usePreferences } from "./preferences";
import type { AgentExecutionMode, AgentRun, DemoScenario } from "../types";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      // Authentication and permission failures are terminal; retrying only delays the boundary.
      retry: (count, error) => !(error instanceof ApiError && [401, 403, 404].includes(error.status)) && count < 1,
    },
  },
});

export const queryKeys = {
  health: ["system-health"] as const,
  runtimeConfig: ["runtime-config"] as const,
  runs: ["agent-runs"] as const,
  run: (runId: string) => ["agent-run", runId] as const,
  runMemory: (runId: string) => ["agent-run-memory", runId] as const,
  conversationRuns: (conversationId: string) => ["conversation-runs", conversationId] as const,
  demoScenarios: ["demo-scenarios"] as const,
  memory: (customerId: number) => ["memory", customerId] as const,
  operatorMe: ["operator-me"] as const,
  inbox: ["operator-conversations"] as const,
  conversation: (id: number) => ["operator-conversation", id] as const,
};

export function useHealth(enabled: boolean) {
  return useQuery({ queryKey: queryKeys.health, queryFn: api.health, enabled, refetchInterval: 30_000 });
}

export function useRuntimeConfig(enabled: boolean) {
  return useQuery({ queryKey: queryKeys.runtimeConfig, queryFn: api.runtimeConfig, enabled, staleTime: Infinity });
}

export function useDemoScenarios(enabled: boolean) {
  return useQuery({ queryKey: queryKeys.demoScenarios, queryFn: api.demoScenarios, enabled, staleTime: Infinity });
}

export function useRuns(enabled: boolean) {
  return useQuery({ queryKey: queryKeys.runs, queryFn: () => api.runs(100), enabled, refetchInterval: 30_000 });
}

export function useMemory(customerId: number, enabled: boolean) {
  return useQuery({ queryKey: queryKeys.memory(customerId), queryFn: () => api.memory(customerId), enabled });
}

/** Recorded demo runs resolve locally; persisted runs come from the projection API. */
export function useRun(runId: string | null, scenarios: DemoScenario[] | undefined, enabled: boolean) {
  const recorded = runId ? scenarios?.find((scenario) => scenario.run.run_id === runId)?.run : undefined;
  const waitingForScenarios = Boolean(runId?.startsWith("demo-")) && scenarios === undefined;
  return useQuery({
    queryKey: queryKeys.run(runId ?? ""),
    queryFn: () => (recorded ? Promise.resolve(recorded) : api.run(runId!)),
    enabled: enabled && Boolean(runId) && !waitingForScenarios,
  });
}

export function useChatMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { conversationId: string; customerId: number; message: string; executionMode?: AgentExecutionMode }) => {
      const response = await api.chat(input.conversationId, input.customerId, input.message, input.executionMode);
      const run = await api.run(response.agent_run_id);
      return { response, run };
    },
    onSuccess: ({ run }, input) => {
      client.setQueryData(queryKeys.run(run.run_id), run);
      client.setQueryData<AgentRun[]>(queryKeys.runs, (current) => [run, ...(current ?? []).filter((item) => item.run_id !== run.run_id)]);
      void client.invalidateQueries({ queryKey: queryKeys.memory(input.customerId) });
      void client.invalidateQueries({ queryKey: queryKeys.health });
      void client.invalidateQueries({ queryKey: queryKeys.inbox });
      void client.invalidateQueries({ queryKey: ["operator-conversation"] });
    },
  });
}

export function useOperatorMe(enabled: boolean) {
  return useQuery({ queryKey: queryKeys.operatorMe, queryFn: api.operatorMe, enabled, staleTime: Infinity });
}

/** The inbox and the open conversation poll so operators watch conversations live. */
export function useInbox(enabled: boolean) {
  const { liveUpdates } = usePreferences();
  const interval = liveUpdateIntervals[liveUpdates].inbox;
  return useQuery({ queryKey: queryKeys.inbox, queryFn: api.operatorConversations, enabled, refetchInterval: interval, staleTime: interval / 2 });
}

export function useOperatorConversation(id: number | null) {
  const { liveUpdates } = usePreferences();
  const interval = liveUpdateIntervals[liveUpdates].conversation;
  return useQuery({
    queryKey: queryKeys.conversation(id ?? 0),
    queryFn: () => api.operatorConversation(id!),
    enabled: id !== null,
    refetchInterval: interval,
    staleTime: interval / 2,
  });
}

export function useConversationAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: number; version: number } & ({ action: "claim" | "release" | "resolve" } | { action: "reply"; content: string; clientMessageId: string })) =>
      input.action === "reply"
        ? api.operatorReply(input.id, input.content, input.version, input.clientMessageId)
        : api.operatorTransition(input.id, input.action, input.version),
    onSuccess: (detail) => {
      client.setQueryData(queryKeys.conversation(detail.id), detail);
      void client.invalidateQueries({ queryKey: queryKeys.inbox });
    },
    onSettled: (_data, _error, input) => { void client.invalidateQueries({ queryKey: queryKeys.conversation(input.id) }); },
  });
}

/** Memory content is resolved live, so deleted or expired memories are never shown from old runs. */
export function useRunMemory(runId: string | null) {
  return useQuery({
    queryKey: queryKeys.runMemory(runId ?? ""),
    queryFn: () => api.runMemory(runId as string),
    enabled: Boolean(runId),
    staleTime: 10_000,
  });
}

/** Every agent run in one conversation, for the conversation debug view. Polls while it is open. */
export function useConversationRuns(conversationId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.conversationRuns(conversationId ?? ""),
    queryFn: () => api.conversation(conversationId as string),
    enabled: enabled && Boolean(conversationId),
    refetchInterval: 5_000,
    retry: false,
  });
}

export function useOperatorSettings(enabled: boolean) {
  return useQuery({ queryKey: ["operator-settings"], queryFn: api.operatorSettings, enabled, staleTime: 60_000 });
}
