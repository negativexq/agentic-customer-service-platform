import { Send } from "lucide-react";
import { useState, type FormEvent } from "react";

type Props = {
  busy: boolean;
  onSend: (message: string) => Promise<void>;
};

export function ChatInput({ busy, onSend }: Props) {
  const [value, setValue] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const message = value.trim();
    if (!message || busy) return;
    setValue("");
    await onSend(message);
  };

  return (
    <form className="mt-4" onSubmit={submit} data-testid="chat-input-form">
      <label className="field-label" htmlFor="unified-chat-input">Customer message</label>
      <div className="mt-2 flex items-end gap-2 rounded-lg border border-border bg-void/30 p-2 focus-within:border-info/50">
        <textarea
          id="unified-chat-input"
          className="min-h-[56px] flex-1 resize-y border-0 bg-transparent px-2 py-1.5 text-sm text-main outline-none placeholder:text-muted"
          placeholder="Ask about an order, policy, or account request…"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={busy}
          rows={2}
          maxLength={5000}
          data-testid="chat-input"
        />
        <button className="button button-primary shrink-0" type="submit" disabled={busy || !value.trim()} data-testid="chat-send">
          <Send size={14} aria-hidden="true" />
          {busy ? "Processing" : "Send"}
        </button>
      </div>
      <p className="mt-2 text-xs text-muted">Requests are evaluated by the existing agent workflow. This surface does not bypass policy or confirmation.</p>
    </form>
  );
}
