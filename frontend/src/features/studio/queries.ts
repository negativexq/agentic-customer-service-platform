import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api/client";
import type {
  ActionView, AgentConfig, AgentConfigState, KnowledgeDocument, KnowledgeDocumentDetail, KnowledgeIndexStatus,
  KnowledgeSearchResult, ProcedureView,
} from "./types";

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

export const studioApi = {
  config: () => request<AgentConfigState>("/ui/agent-config"),
  saveDraft: (config: AgentConfig, note = "") => request<AgentConfigState>("/ui/agent-config/draft", json("PUT", { config, note })),
  discardDraft: () => request<AgentConfigState>("/ui/agent-config/draft", json("DELETE")),
  publish: (note = "") => request<AgentConfigState>("/ui/agent-config/publish", json("POST", { note })),
  restore: (version: number) => request<AgentConfigState>(`/ui/agent-config/versions/${version}/restore`, json("POST")),
  procedures: () => request<ProcedureView[]>("/ui/procedures"),
  actions: () => request<ActionView[]>("/ui/actions"),
  knowledge: () => request<KnowledgeDocument[]>("/ui/knowledge"),
  knowledgeStatus: () => request<KnowledgeIndexStatus>("/ui/knowledge/status"),
  knowledgeDocument: (id: string) => request<KnowledgeDocumentDetail>(`/ui/knowledge/${encodeURIComponent(id)}`),
  searchKnowledge: (query: string) => request<KnowledgeSearchResult>("/ui/knowledge/search", json("POST", { query })),
  publishKnowledge: () => request<{ snapshot: string; chunk_count: number; action: string }>("/ui/knowledge/publish", json("POST")),
  createArticle: (title: string, content: string) => request<KnowledgeDocumentDetail>("/ui/knowledge", json("POST", { title, content })),
  saveArticle: (id: string, title: string, content: string) =>
    request<KnowledgeDocumentDetail>(`/ui/knowledge/${encodeURIComponent(id)}`, json("PUT", { title, content })),
  articleAction: (id: string, action: "archive" | "unarchive" | "revert") =>
    request<KnowledgeDocumentDetail>(`/ui/knowledge/${encodeURIComponent(id)}/${action}`, json("POST")),
};

export const studioKeys = {
  config: ["studio", "agent-config"] as const,
  procedures: ["studio", "procedures"] as const,
  actions: ["studio", "actions"] as const,
  knowledge: ["studio", "knowledge"] as const,
  knowledgeStatus: ["studio", "knowledge-status"] as const,
  document: (id: string) => ["studio", "knowledge", id] as const,
};

export const useAgentConfig = () => useQuery({ queryKey: studioKeys.config, queryFn: studioApi.config });
export const useProcedures = () => useQuery({ queryKey: studioKeys.procedures, queryFn: studioApi.procedures });
export const useActions = () => useQuery({ queryKey: studioKeys.actions, queryFn: studioApi.actions });
export const useKnowledge = () => useQuery({ queryKey: studioKeys.knowledge, queryFn: studioApi.knowledge });
export const useKnowledgeStatus = () => useQuery({ queryKey: studioKeys.knowledgeStatus, queryFn: studioApi.knowledgeStatus });
export const useKnowledgeDocument = (id: string | null) =>
  useQuery({ queryKey: studioKeys.document(id ?? ""), queryFn: () => studioApi.knowledgeDocument(id!), enabled: Boolean(id) });

/** Every change to the agent's configuration refreshes the views that derive from it. */
export function useConfigMutation<TInput>(fn: (input: TInput) => Promise<AgentConfigState>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (state) => {
      client.setQueryData(studioKeys.config, state);
      void client.invalidateQueries({ queryKey: studioKeys.actions });
      void client.invalidateQueries({ queryKey: studioKeys.procedures });
    },
  });
}

export function useKnowledgeMutation<TInput, TResult>(fn: (input: TInput) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["studio", "knowledge"] }); void client.invalidateQueries({ queryKey: studioKeys.knowledgeStatus }); },
  });
}
