import type { ReactNode } from "react";

export function DataRow({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) { return <div className="data-row"><span className="text-muted">{label}</span><span className={`max-w-[65%] truncate text-right text-main ${mono ? "font-mono text-xs" : "text-xs"}`}>{value}</span></div>; }
