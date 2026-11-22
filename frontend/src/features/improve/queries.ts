import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api/client";
import type { ErrorLog, Gap, MonitorInput, PerformanceView, QualityView, ReviewStatus, Scenario, SimulationRun } from "./types";

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

export const improveApi = {
  performance: (days: number) => request<PerformanceView>(`/ui/performance?days=${days}`),
  gaps: (days: number) => request<Gap[]>(`/ui/gaps?days=${days}`),
  errors: (days: number) => request<ErrorLog>(`/ui/errors?days=${days}`),
  quality: (days: number) => request<QualityView>(`/ui/quality?days=${days}`),
  createMonitor: (input: MonitorInput) => request<{ key: string }>("/ui/quality/monitors", json("POST", input)),
  updateMonitor: (id: number, input: MonitorInput) => request<{ key: string }>(`/ui/quality/monitors/${id}`, json("PUT", input)),
  deleteMonitor: (id: number) => request<void>(`/ui/quality/monitors/${id}`, json("DELETE")),
  review: (monitorKey: string, conversationPk: number, status: ReviewStatus, note = "") =>
    request<void>("/ui/quality/reviews", json("POST", { monitor_key: monitorKey, conversation_pk: conversationPk, status, note })),
  scenarios: () => request<Scenario[]>("/ui/simulations/scenarios"),
  simulations: () => request<SimulationRun[]>("/ui/simulations"),
  simulation: (id: number) => request<SimulationRun>(`/ui/simulations/${id}`),
  simulate: (scenarioIds: string[], against: "published" | "draft", label: string) =>
    request<SimulationRun>("/ui/simulations", json("POST", { scenario_ids: scenarioIds, against, label })),
};

const opts = { placeholderData: keepPreviousData, staleTime: 30_000 } as const;
export const usePerformance = (days: number) => useQuery({ queryKey: ["improve", "performance", days], queryFn: () => improveApi.performance(days), ...opts });
export const useGaps = (days: number) => useQuery({ queryKey: ["improve", "gaps", days], queryFn: () => improveApi.gaps(days), ...opts });
export const useErrorLog = (days: number) => useQuery({ queryKey: ["improve", "errors", days], queryFn: () => improveApi.errors(days), ...opts });
export const useQuality = (days: number) => useQuery({ queryKey: ["improve", "quality", days], queryFn: () => improveApi.quality(days), ...opts });
export const useScenarios = () => useQuery({ queryKey: ["improve", "scenarios"], queryFn: improveApi.scenarios, staleTime: Infinity });
export const useSimulations = () => useQuery({ queryKey: ["improve", "simulations"], queryFn: improveApi.simulations });
export const useSimulation = (id: number | null) => useQuery({ queryKey: ["improve", "simulation", id], queryFn: () => improveApi.simulation(id!), enabled: id !== null, staleTime: Infinity });

export function useQualityMutation<TInput, TResult>(fn: (input: TInput) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => { void client.invalidateQueries({ queryKey: ["improve", "quality"] }); } });
}

export function useRunSimulation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { scenarioIds: string[]; against: "published" | "draft"; label: string }) => improveApi.simulate(input.scenarioIds, input.against, input.label),
    onSuccess: (run) => {
      client.setQueryData(["improve", "simulation", run.id], run);
      void client.invalidateQueries({ queryKey: ["improve", "simulations"] });
    },
  });
}
