import type { ReactNode } from "react";

/** Renders the small markdown subset knowledge articles use: headings, bullet lists and paragraphs. */
export function Markdown({ source }: { source: string }) {
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push(<p key={blocks.length} className="text-sm leading-relaxed text-foreground">{paragraph.join(" ")}</p>);
    if (list.length) blocks.push(<ul key={blocks.length} className="grid list-disc gap-1 pl-5 text-sm leading-relaxed text-foreground">{list.map((item, index) => <li key={index}>{item}</li>)}</ul>);
    paragraph = [];
    list = [];
  };
  for (const raw of source.split("\n")) {
    const line = raw.trim();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    if (!line) { flush(); continue; }
    if (heading) {
      flush();
      const level = heading[1].length;
      blocks.push(level <= 1
        ? <h2 key={blocks.length} className="text-base font-semibold text-foreground">{heading[2]}</h2>
        : <h3 key={blocks.length} className="pt-2 text-sm font-semibold text-foreground">{heading[2]}</h3>);
      continue;
    }
    if (bullet) { if (paragraph.length) flush(); list.push(bullet[1]); continue; }
    if (list.length) flush();
    paragraph.push(line);
  }
  flush();
  return <div className="grid max-w-prose gap-3">{blocks}</div>;
}
