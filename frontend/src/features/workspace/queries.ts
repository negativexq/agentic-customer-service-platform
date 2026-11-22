import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api/client";
import { liveUpdateIntervals, usePreferences } from "@/lib/preferences";
import type { CustomerContext, Feedback, Note, SavedReply, SlaTarget } from "./types";

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

const remove = (path: string) => request<void>(path, { method: "DELETE" });

export const workspaceApi = {
  context: (id: number) => request<CustomerContext>(`/operator/conversations/${id}/context`),
  notes: (id: number) => request<Note[]>(`/operator/conversations/${id}/notes`),
  addNote: (id: number, content: string) => request<Note>(`/operator/conversations/${id}/notes`, json("POST", { content })),
  deleteNote: (id: number, noteId: number) => remove(`/operator/conversations/${id}/notes/${noteId}`),
  savedReplies: () => request<SavedReply[]>("/operator/saved-replies"),
  createSavedReply: (title: string, content: string) => request<SavedReply>("/operator/saved-replies", json("POST", { title, content })),
  updateSavedReply: (id: number, title: string, content: string) => request<SavedReply>(`/operator/saved-replies/${id}`, json("PUT", { title, content })),
  deleteSavedReply: (id: number) => remove(`/operator/saved-replies/${id}`),
  savedReplyUsed: (id: number) => request<SavedReply>(`/operator/saved-replies/${id}/used`, json("POST")),
  slaTargets: () => request<SlaTarget[]>("/operator/sla-targets"),
  saveSlaTargets: (targets: SlaTarget[]) => request<SlaTarget[]>("/operator/sla-targets", json("PUT", { targets })),
  feedback: (conversationId: string, customerId: number, rating: number, comment: string) =>
    request<Feedback>(`/conversations/${encodeURIComponent(conversationId)}/feedback?customer_id=${customerId}`, json("POST", { rating, comment })),
};

export const workspaceKeys = {
  context: (id: number) => ["workspace", "context", id] as const,
  notes: (id: number) => ["workspace", "notes", id] as const,
  savedReplies: ["workspace", "saved-replies"] as const,
  sla: ["workspace", "sla-targets"] as const,
};

export function useCustomerContext(id: number | null) {
  return useQuery({ queryKey: workspaceKeys.context(id ?? 0), queryFn: () => workspaceApi.context(id!), enabled: id !== null, staleTime: 10_000 });
}

export function useNotes(id: number | null) {
  const { liveUpdates } = usePreferences();
  const interval = liveUpdateIntervals[liveUpdates].conversation * 2;
  return useQuery({ queryKey: workspaceKeys.notes(id ?? 0), queryFn: () => workspaceApi.notes(id!), enabled: id !== null, refetchInterval: interval });
}

export function useAddNote(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (content: string) => workspaceApi.addNote(id, content),
    onSuccess: (note) => client.setQueryData<Note[]>(workspaceKeys.notes(id), (current) => [...(current ?? []), note]),
  });
}

export function useDeleteNote(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (noteId: number) => workspaceApi.deleteNote(id, noteId),
    onSuccess: (_result, noteId) => client.setQueryData<Note[]>(workspaceKeys.notes(id), (current) => (current ?? []).filter((note) => note.id !== noteId)),
  });
}

export const useSavedReplies = (enabled = true) => useQuery({ queryKey: workspaceKeys.savedReplies, queryFn: workspaceApi.savedReplies, enabled });
export const useSlaTargets = () => useQuery({ queryKey: workspaceKeys.sla, queryFn: workspaceApi.slaTargets });

export function useSavedReplyMutation<TInput, TResult>(fn: (input: TInput) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => { void client.invalidateQueries({ queryKey: workspaceKeys.savedReplies }); } });
}

export function useSaveSlaTargets() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: workspaceApi.saveSlaTargets,
    onSuccess: (targets) => { client.setQueryData(workspaceKeys.sla, targets); void client.invalidateQueries({ queryKey: ["operator-conversations"] }); },
  });
}

export function useSubmitFeedback() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { pk: number; conversationId: string; customerId: number; rating: number; comment: string }) =>
      workspaceApi.feedback(input.conversationId, input.customerId, input.rating, input.comment),
    onSuccess: (_feedback, input) => {
      void client.invalidateQueries({ queryKey: workspaceKeys.context(input.pk) });
      void client.invalidateQueries({ queryKey: ["operator-conversations"] });
    },
  });
}
