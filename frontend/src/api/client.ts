import type { AgentExecutionMode, AgentResponse, AgentRun, ConversationView, DemoScenario, Health, MemoryRecord, MemoryUsage, RuntimeConfig, OperatorConversation, OperatorConversationDetail, OperatorIdentity } from "../types";
import {
  createConfiguredAuthProvider,
  type AuthProvider,
  type AuthSnapshot,
} from "../auth/provider";

const base = import.meta.env.VITE_API_BASE ?? "";
let authProvider: AuthProvider = createConfiguredAuthProvider();

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly reason?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function setApiAuthProvider(provider: AuthProvider): void {
  authProvider = provider;
}

export function getApiAuthProvider(): AuthProvider {
  return authProvider;
}

export function getApiAuthSnapshot(): AuthSnapshot {
  return authProvider.getSnapshot();
}

export async function initializeApiAuth(): Promise<AuthSnapshot> {
  return authProvider.initialize();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const auth = authProvider.getSnapshot();
  if (auth.status !== "authenticated") {
    const message =
      auth.status === "misconfigured"
        ? "Production authentication is not configured."
        : "Authentication is required to use the Operator Console.";
    throw new ApiError(message, auth.status === "unauthenticated" ? 401 : 0);
  }
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const credential = authProvider.getAccessCredential();
  if (credential) headers.set("Authorization", `Bearer ${credential}`);
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers,
    credentials: authProvider.usesCookieSession() ? "include" : init?.credentials,
  });
  if (!response.ok) {
    if (response.status === 401) authProvider.clearCredential();
    let detail: { message?: string; reason?: string } | null = null;
    try {
      const payload: unknown = await response.json();
      if (typeof payload === "object" && payload !== null && "detail" in payload) {
        const raw = (payload as { detail?: unknown }).detail;
        if (typeof raw === "object" && raw !== null) detail = raw as { message?: string; reason?: string };
      }
    } catch {
      detail = null;
    }
    const message = detail?.message ?? (
      response.status === 401
        ? "Authentication session is missing or expired."
        : response.status === 403
          ? "The authenticated operator is not permitted to perform this request."
          : `Request failed (${response.status}).`);
    throw new ApiError(message, response.status, detail?.reason);
  }
  return response.json() as Promise<T>;
}

async function requestProjection<T>(path: string): Promise<T> {
  // Projection persistence is observational and may become visible just after
  // the agent response. Keep this bounded: three short reads, never polling.
  for (const delayMs of [0, 100, 250]) {
    if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
    try {
      return await request<T>(path);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404 || delayMs === 250) throw error;
    }
  }
  throw new ApiError("Agent run projection is unavailable.", 404);
}

export const api = {
  runs: (limit = 25) => request<AgentRun[]>(`/ui/agent-runs?limit=${limit}`),
  demoScenarios: () => request<DemoScenario[]>("/ui/demo-scenarios"),
  chat: (conversationId: string, customerId: number, message: string, executionMode: AgentExecutionMode = "recorded_replay") =>
    request<AgentResponse>("/agent/chat", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, customer_id: customerId, message, execution_mode: executionMode }),
    }),
  runMemory: (runId: string) => request<MemoryUsage>(`/ui/agent-runs/${runId}/memory`),
  run: (runId: string) => requestProjection<AgentRun>(`/ui/agent-runs/${runId}`),
  conversation: (conversationId: string) => request<ConversationView>(`/ui/conversations/${conversationId}`),
  memory: (customerId: number) => request<MemoryRecord[]>(`/ui/memory/${customerId}`),
  health: () => request<Health>("/ui/system-health"),
  runtimeConfig: () => request<RuntimeConfig>("/ui/runtime-config"),
  operatorMe: () => request<OperatorIdentity>("/operator/me"),
  operatorConversations: () => request<OperatorConversation[]>("/operator/conversations?limit=200"),
  operatorConversation: (id: number) => request<OperatorConversationDetail>(`/operator/conversations/${id}`),
  operatorTransition: (id: number, action: "claim" | "release" | "resolve", expectedVersion: number) =>
    request<OperatorConversationDetail>(`/operator/conversations/${id}/${action}`, {
      method: "POST",
      body: JSON.stringify({ expected_version: expectedVersion }),
    }),
  operatorReply: (id: number, content: string, expectedVersion: number, clientMessageId: string) =>
    request<OperatorConversationDetail>(`/operator/conversations/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, expected_version: expectedVersion, client_message_id: clientMessageId }),
    }),
};
