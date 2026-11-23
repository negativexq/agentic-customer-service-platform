/** The AI assistant's mark: a sparkle inside a gradient ring that turns while the assistant is thinking. */
export function AgentOrb({ thinking = false, className = "size-7" }: { thinking?: boolean; className?: string }) {
  return (
    <span className={`agent-orb shrink-0 ${className}`} data-thinking={thinking ? "" : undefined} aria-hidden="true">
      <span className="agent-orb-core">
        <svg viewBox="0 0 16 16" className="size-[55%]" fill="url(#agent-orb-fill)">
          <defs>
            <linearGradient id="agent-orb-fill" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#4c82ff" />
              <stop offset="0.55" stopColor="#a78bfa" />
              <stop offset="1" stopColor="#f472b6" />
            </linearGradient>
          </defs>
          <path d="M8 1.2c.4 3.3 2.3 5.4 5.8 6.8-3.5 1.4-5.4 3.5-5.8 6.8-.4-3.3-2.3-5.4-5.8-6.8C5.7 6.6 7.6 4.5 8 1.2Z" />
        </svg>
      </span>
    </span>
  );
}
