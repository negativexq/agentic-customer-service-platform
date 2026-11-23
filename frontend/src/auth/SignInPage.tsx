import { ArrowRight, Check, Eye, EyeOff, Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { AgentOrb } from "../components/AgentOrb";
import { DEMO_ACCOUNT, signIn, type DemoSession } from "./demoSession";

function LogoMark({ className = "size-9" }: { className?: string }) {
  return (
    <span className={`flex aspect-square items-center justify-center rounded-xl bg-primary text-primary-foreground ${className}`} aria-hidden="true">
      <svg viewBox="0 0 16 16" className="size-[55%]" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M2.5 11.5 6 4.5l3 5 1.5-2.5 3 4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

const steps = [
  { label: "Understood the request", detail: "Duplicate charge · order 4821" },
  { label: "Checked the refund policy", detail: "Eligible · within 30 days" },
  { label: "Asked the customer to confirm", detail: "Confirmed in chat" },
  { label: "Refund issued", detail: "€49.00 back to the card" },
];

/** A looping, silent replay of one conversation moving through the assistant's decision path. */
function DecisionReplay() {
  return (
    <div className="signin-stage relative flex h-full flex-col justify-center overflow-hidden rounded-[1.75rem] border border-white/[0.06] bg-[#0b0b0d] px-8 py-10 text-[#ededef] xl:px-14" aria-hidden="true">
      <div className="signin-dots absolute inset-0" />
      <div className="signin-sweep absolute inset-0" />
      <div className="signin-glow absolute -top-40 -right-32 size-[34rem] rounded-full" />

      <div className="relative mx-auto w-full max-w-[26rem]">
        <div className="mb-6 flex items-center gap-2 font-mono text-[10.5px] tracking-[0.12em] text-white/45 uppercase">
          <span className="signin-live size-1.5 rounded-full bg-[#3ccf7a]" />Live conversation
        </div>

        <div className="signin-card rounded-2xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_30px_80px_-30px_rgb(0_0_0/0.8)] backdrop-blur-sm">
          <div className="signin-msg flex items-end gap-2.5">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-white/10 text-[10px] font-medium">JL</span>
            <p className="rounded-2xl rounded-bl-md bg-white/[0.07] px-3.5 py-2.5 text-[13px] leading-5 text-white/90">
              I was charged twice for my last order. Can you sort it out?
            </p>
          </div>

          <div className="signin-think mt-4 flex items-center gap-2.5 text-xs text-white/55">
            <AgentOrb thinking className="size-6" />
            <span>Assistant is working on it</span>
          </div>

          <ol className="mt-4 grid gap-1.5">
            {steps.map((step, index) => (
              <li key={step.label} className="signin-step flex items-center gap-3 rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2.5" style={{ "--step": index } as React.CSSProperties}>
                <span className="signin-tick flex size-5 shrink-0 items-center justify-center rounded-full border border-white/15">
                  <Check className="size-3" strokeWidth={3} />
                </span>
                <span className="grid min-w-0 flex-1">
                  <span className="text-[13px] text-white/90">{step.label}</span>
                  <span className="truncate text-[11px] text-white/45">{step.detail}</span>
                </span>
                <span className="signin-bar h-1 w-10 overflow-hidden rounded-full bg-white/[0.06]"><span className="block h-full rounded-full bg-[#4c82ff]" /></span>
              </li>
            ))}
          </ol>

          <div className="signin-done mt-4 flex items-center justify-between rounded-xl bg-[#3ccf7a]/10 px-3 py-2 text-xs text-[#5fe09a]">
            <span className="inline-flex items-center gap-1.5"><Check className="size-3.5" strokeWidth={2.5} />Resolved by the assistant</span>
            <span className="font-mono text-[11px] text-[#5fe09a]/70">no person needed</span>
          </div>
        </div>

        <p className="mt-8 max-w-sm text-[15px] leading-6 text-white/60">
          Every decision is proposed, checked against your policies, and recorded with its evidence. You step in only when a customer needs a person.
        </p>
      </div>
    </div>
  );
}

export function SignInPage({ onSignedIn }: { onSignedIn: (session: DemoSession) => void }) {
  const [email, setEmail] = useState<string>(DEMO_ACCOUNT.email);
  const [password, setPassword] = useState<string>(DEMO_ACCOUNT.password);
  const [reveal, setReveal] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setPending(true);
    // A short beat so the button's state change is visible; there is no network call here.
    window.setTimeout(() => {
      const session = signIn(email, password);
      setPending(false);
      if (session) onSignedIn(session);
      else setError("Those details don't match the demo account. Use the email and password filled in above.");
    }, 450);
  };

  return (
    <main className="grid min-h-svh bg-background text-foreground lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
      <section className="flex flex-col px-6 py-8 sm:px-12 lg:px-16 xl:px-24">
        <div className="signin-rise flex items-center gap-2.5" style={{ "--rise": 0 } as React.CSSProperties}>
          <LogoMark className="size-8" />
          <span className="text-[15px] font-semibold tracking-tight">AgentOps</span>
        </div>

        <div className="mx-auto flex w-full max-w-[22rem] flex-1 flex-col justify-center py-12">
          <div className="signin-rise" style={{ "--rise": 1 } as React.CSSProperties}>
            <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.025em]">Sign in to your workspace</h1>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">Watch the assistant handle customers, and take over when someone needs a person.</p>
          </div>

          <div className="signin-rise mt-6 flex items-start gap-2.5 rounded-xl border border-border bg-muted/50 px-3 py-2.5 text-xs leading-5 text-muted-foreground" style={{ "--rise": 2 } as React.CSSProperties}>
            <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-info" aria-hidden="true" />
            <span><span className="font-medium text-foreground">Demo workspace.</span> The demo account's details are already filled in, so just sign in.</span>
          </div>

          <form className="mt-6 grid gap-4" onSubmit={submit} noValidate>
            <label className="signin-rise grid gap-1.5" style={{ "--rise": 3 } as React.CSSProperties}>
              <span className="text-[13px] font-medium">Work email</span>
              <input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required
                className="h-10 rounded-lg border border-input bg-background px-3 text-sm shadow-[0_1px_2px_rgb(0_0_0/0.04)] transition-[border-color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/25" />
            </label>
            <label className="signin-rise grid gap-1.5" style={{ "--rise": 4 } as React.CSSProperties}>
              <span className="text-[13px] font-medium">Password</span>
              <span className="relative">
                <input type={reveal ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required
                  aria-invalid={error ? true : undefined} aria-describedby={error ? "signin-error" : undefined}
                  className="h-10 w-full rounded-lg border border-input bg-background pr-10 pl-3 text-sm shadow-[0_1px_2px_rgb(0_0_0/0.04)] transition-[border-color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/25 aria-invalid:border-danger" />
                <button type="button" onClick={() => setReveal((value) => !value)} aria-label={reveal ? "Hide password" : "Show password"}
                  className="absolute top-1/2 right-1.5 flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
                  {reveal ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
                </button>
              </span>
            </label>

            {error && <p id="signin-error" role="alert" className="text-xs leading-5 text-danger">{error}</p>}

            <button type="submit" disabled={pending}
              className="signin-rise group mt-1 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary text-sm font-medium text-primary-foreground shadow-[0_1px_2px_rgb(0_0_0/0.1),inset_0_1px_0_rgb(255_255_255/0.08)] transition-[transform,opacity] duration-150 hover:opacity-90 focus-visible:ring-3 focus-visible:ring-ring/40 focus-visible:outline-none active:scale-[0.98] disabled:opacity-70 motion-reduce:active:scale-100"
              style={{ "--rise": 5 } as React.CSSProperties}>
              {pending ? <><Loader2 className="size-4 animate-spin" aria-hidden="true" />Signing in…</> : <>Sign in<ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" /></>}
            </button>
          </form>

          <p className="signin-rise mt-6 text-xs leading-5 text-muted-foreground" style={{ "--rise": 6 } as React.CSSProperties}>
            You will work as <span className="text-foreground">{DEMO_ACCOUNT.name}</span>, {DEMO_ACCOUNT.role.toLowerCase()}. Everything you do in the console is recorded under that name.
          </p>
        </div>

        <p className="text-xs text-muted-foreground">AgentOps · Customer service</p>
      </section>

      <section className="hidden p-3 lg:block">
        <DecisionReplay />
      </section>
    </main>
  );
}
