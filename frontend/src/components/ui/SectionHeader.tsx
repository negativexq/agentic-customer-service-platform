import type { ReactNode } from "react";

export function SectionHeader({ title, description, eyebrow, action }: { title: string; description?: string; eyebrow?: string; action?: ReactNode }) {
  return <div className="mb-4 flex items-start justify-between gap-4"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h2 className="section-title">{title}</h2>{description && <p className="mt-1 max-w-2xl text-sm leading-6 text-muted">{description}</p>}</div>{action}</div>;
}
