import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { request } from "../../api/client";
import type { AuditKind, AuditLog } from "./types";

export const auditApi = {
  log: (days: number, kind: AuditKind | null, search: string) => {
    const params = new URLSearchParams({ days: String(days) });
    if (kind) params.set("kind", kind);
    if (search) params.set("q", search);
    return request<AuditLog>(`/ui/audit?${params.toString()}`);
  },
  trail: (conversationPk: number) => request<AuditLog>(`/ui/audit?days=90&conversation=${conversationPk}`),
};

export const useAuditLog = (days: number, kind: AuditKind | null = null, search = "") =>
  useQuery({ queryKey: ["audit", days, kind, search], queryFn: () => auditApi.log(days, kind, search), placeholderData: keepPreviousData, staleTime: 15_000 });

export const useConversationTrail = (conversationPk: number | null) =>
  useQuery({ queryKey: ["audit", "trail", conversationPk], queryFn: () => auditApi.trail(conversationPk!), enabled: conversationPk !== null, staleTime: 15_000 });
