/** Mirrors app/ui/audit_schemas.py. */
export type AuditKind = "conversation" | "handoff" | "operator" | "assistant" | "action" | "safety";
export type AuditActorType = "ai" | "operator" | "customer" | "system";
export type AuditActor = { type: AuditActorType; id: string | null; name: string };

export type AuditEvent = {
  id: string;
  at: string;
  kind: AuditKind;
  action: string;
  actor: AuditActor;
  conversation_pk: number;
  conversation_id: string;
  customer_id: number;
  customer_name: string;
  summary: string;
  detail: string | null;
  run_id: string | null;
};

export type AuditLog = { total: number; counts: Partial<Record<AuditKind, number>>; events: AuditEvent[] };
