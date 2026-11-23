/** Operator workspace contracts: mirror app/ui/workspace_schemas.py. */
export type SlaState = "ok" | "at_risk" | "breached";
export type Priority = "urgent" | "high" | "medium" | "low";

export type SlaStatus = { priority: string; target_minutes: number; waiting_since: string; due_at: string; state: SlaState };

export type Feedback = { rating: number; comment: string; resolved_by: string; created_at: string };

export type CustomerContext = {
  customer: { id: number; name: string; email: string; customer_since: string };
  orders: { id: number; status: string; total_amount: string; created_at: string }[];
  tickets: { id: number; category: string; status: string; description: string; order_id: number | null; created_at: string }[];
  other_conversations: { id: number; conversation_id: string; owner: string; updated_at: string; last_message: string | null; rating: number | null }[];
  conversation_count: number;
  average_rating: number | null;
  feedback: Feedback | null;
};

export type Note = { id: number; author_name: string; written_by_me: boolean; content: string; redacted: boolean; created_at: string };

export type SavedReply = { id: number; title: string; content: string; created_by: string; updated_at: string; use_count: number };

export type SlaTarget = { priority: Priority; first_response_minutes: number };
