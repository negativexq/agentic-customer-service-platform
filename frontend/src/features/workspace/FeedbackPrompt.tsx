import { Star } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/primitives/button";
import { useSubmitFeedback } from "./queries";

/** The customer's view of the satisfaction survey, available in test conversations once resolved. */
export function FeedbackPrompt({ pk, conversationId, customerId, customerName, current }: { pk: number; conversationId: string; customerId: number; customerName: string; current: number | null }) {
  const [rating, setRating] = useState(current ?? 0);
  const [hover, setHover] = useState(0);
  const [comment, setComment] = useState("");
  const submit = useSubmitFeedback();
  const sent = submit.isSuccess || current !== null;
  return (
    <div className="mx-auto grid max-w-3xl gap-2 rounded-lg border border-dashed border-border px-3 py-2.5" data-testid="csat-prompt">
      <span className="text-xs text-muted-foreground">{sent ? `${customerName} rated this conversation. Rating again replaces it.` : `As ${customerName}: how would you rate this conversation?`}</span>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex" role="radiogroup" aria-label="Rating" onMouseLeave={() => setHover(0)}>
          {[1, 2, 3, 4, 5].map((value) => (
            <button key={value} type="button" role="radio" aria-checked={rating === value} aria-label={`${value} star${value === 1 ? "" : "s"}`}
              onMouseEnter={() => setHover(value)} onClick={() => setRating(value)} className="p-0.5">
              <Star className={`size-5 ${value <= (hover || rating) ? "fill-warning text-warning" : "text-muted-foreground/50"}`} aria-hidden="true" />
            </button>
          ))}
        </span>
        <input value={comment} onChange={(event) => setComment(event.target.value)} maxLength={1000} placeholder="Anything to add? (optional)" aria-label="Feedback comment"
          className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:outline-none" />
        <Button size="sm" disabled={rating === 0 || submit.isPending} onClick={() => submit.mutate({ pk, conversationId, customerId, rating, comment })}>Send rating</Button>
      </div>
      {submit.error && <p role="alert" className="text-xs text-danger">{submit.error.message}</p>}
    </div>
  );
}
