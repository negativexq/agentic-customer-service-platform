import { QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
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
  demoScenarios: ["demo-scenarios"] as const,
  memory: (customerId: number) => ["memory", customerId] as const,
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
    },
  });
}
