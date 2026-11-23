import { Moon, Sun } from "lucide-react";
import { useEffect } from "react";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator, CommandShortcut } from "@/components/primitives/command";
import { storeThemePreference } from "@/lib/theme";
import type { AgentRun } from "../types";
import { navigation } from "./navigation";
import type { ConsoleView } from "./routes";

type CommandMenuProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  runs: AgentRun[];
  onNavigate: (view: ConsoleView) => void;
  onOpenRun: (runId: string) => void;
};

export function CommandMenu({ open, onOpenChange, runs, onNavigate, onOpenRun }: CommandMenuProps) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        onOpenChange(!open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  const run = (action: () => void) => { onOpenChange(false); action(); };
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Search the console" description="Go to a page, open a run, or change a setting.">
      <CommandInput placeholder="Search pages and runs…" />
      <CommandList>
        <CommandEmpty>No pages or runs match.</CommandEmpty>
        {navigation.map((group) => (
          <CommandGroup key={group.label} heading={group.label}>
            {group.items.map(({ view, label, icon: Icon, shortcut }) => (
              <CommandItem key={view} value={`page ${label}`} onSelect={() => run(() => onNavigate(view))}>
                <Icon aria-hidden="true" />{label}
                {shortcut && <CommandShortcut>{shortcut}</CommandShortcut>}
              </CommandItem>
            ))}
          </CommandGroup>
        ))}
        {runs.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Recent runs">
              {runs.slice(0, 8).map((item) => (
                <CommandItem key={item.run_id} value={`run ${item.run_id} ${item.intent}`} onSelect={() => run(() => onOpenRun(item.run_id))}>
                  <span className="truncate font-mono text-xs">{item.run_id}</span>
                  <span className="ml-auto truncate text-xs text-muted-foreground">{item.intent.replace(/_/g, " ")}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
        <CommandSeparator />
        <CommandGroup heading="Appearance">
          <CommandItem value="theme light" onSelect={() => run(() => storeThemePreference("light"))}><Sun aria-hidden="true" />Use light theme</CommandItem>
          <CommandItem value="theme dark" onSelect={() => run(() => storeThemePreference("dark"))}><Moon aria-hidden="true" />Use dark theme</CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
