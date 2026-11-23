import { ChevronsUpDown, Keyboard, LogOut, Settings } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuShortcut, DropdownMenuTrigger } from "@/components/primitives/dropdown-menu";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "@/components/primitives/sidebar";
import { initials } from "../auth/demoSession";

export type ConsoleUser = { name: string; email: string; role: string };

function Avatar({ name, className = "size-8" }: { name: string; className?: string }) {
  return (
    <span className={`flex shrink-0 items-center justify-center rounded-lg bg-[linear-gradient(135deg,#4c82ff,#a78bfa_60%,#f472b6)] text-[11px] font-semibold text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.25)] ${className}`} aria-hidden="true">
      {initials(name)}
    </span>
  );
}

/** The signed-in person, bottom left: who they are, their settings, and signing out. */
export function ProfileMenu({ user, onOpenSettings, onOpenShortcuts, onSignOut }: { user: ConsoleUser; onOpenSettings: () => void; onOpenShortcuts: () => void; onSignOut: () => void }) {
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger render={<SidebarMenuButton size="lg" tooltip={user.name} className="data-popup-open:bg-sidebar-accent" />}>
            <Avatar name={user.name} />
            <span className="grid min-w-0 flex-1 text-left leading-tight">
              <span className="truncate text-sm font-medium">{user.name}</span>
              <span className="truncate text-xs text-muted-foreground">{user.email}</span>
            </span>
            <ChevronsUpDown className="ml-auto size-4 text-muted-foreground" aria-hidden="true" />
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="start" sideOffset={8} className="w-(--anchor-width) min-w-60">
            <DropdownMenuGroup>
              <DropdownMenuLabel className="flex items-center gap-2.5 px-2 py-2 font-normal">
                <Avatar name={user.name} className="size-9" />
                <span className="grid min-w-0 leading-tight">
                  <span className="truncate text-sm font-medium text-foreground">{user.name}</span>
                  <span className="truncate text-xs text-muted-foreground">{user.role}</span>
                </span>
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem onClick={onOpenSettings}><Settings aria-hidden="true" />Settings</DropdownMenuItem>
              <DropdownMenuItem onClick={onOpenShortcuts}><Keyboard aria-hidden="true" />Search and shortcuts<DropdownMenuShortcut>⌘K</DropdownMenuShortcut></DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onSignOut}><LogOut aria-hidden="true" />Sign out</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
