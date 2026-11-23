import { Search } from "lucide-react";
import type { MouseEvent, ReactNode } from "react";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/primitives/breadcrumb";
import { Button } from "@/components/primitives/button";
import { Kbd, KbdGroup } from "@/components/primitives/kbd";
import { Separator } from "@/components/primitives/separator";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader,
  SidebarInset, SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarRail, SidebarTrigger, useSidebar,
} from "@/components/primitives/sidebar";
import type { Health, RuntimeConfig } from "../types";
import { navigation } from "./navigation";
import { viewPaths, viewTitles, type ConsoleView } from "./routes";
import { ThemeToggle } from "./ThemeToggle";

type AppShellProps = {
  activeView: ConsoleView;
  detailLabel?: string | null;
  health: Health | null | undefined;
  healthUnavailable: boolean;
  runtimeConfig: RuntimeConfig | undefined;
  onNavigate: (view: ConsoleView) => void;
  onOpenSearch: () => void;
  /** Counts that need someone's attention, shown beside their page in the sidebar. */
  badges?: Partial<Record<ConsoleView, NavBadge>>;
  children: ReactNode;
};

export type NavBadge = { count: number; tone: "warning" | "danger" | "neutral"; label: string };

const badgeTone: Record<NavBadge["tone"], string> = {
  warning: "bg-warning/14 text-warning!",
  danger: "bg-danger/14 text-danger!",
  neutral: "bg-foreground/[0.07] text-sidebar-foreground/75!",
};

function isPlainClick(event: MouseEvent<HTMLAnchorElement>) {
  return !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0);
}

function NavigationLinks({ activeView, onNavigate, badges = {} }: Pick<AppShellProps, "activeView" | "onNavigate" | "badges">) {
  const { isMobile, setOpenMobile } = useSidebar();
  return (
    <nav aria-label="Console views">
      {navigation.map((group) => (
        <SidebarGroup key={group.label}>
          <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {group.items.map(({ view, label, icon: Icon }) => (
                <SidebarMenuItem key={view}>
                  <SidebarMenuButton
                    isActive={activeView === view}
                    tooltip={label}
                    render={
                      <a
                        href={viewPaths[view]}
                        aria-current={activeView === view ? "page" : undefined}
                        onClick={(event) => {
                          if (!isPlainClick(event)) return;
                          event.preventDefault();
                          onNavigate(view);
                          if (isMobile) setOpenMobile(false);
                        }}
                      />
                    }
                  >
                    <Icon aria-hidden="true" />
                    <span>{label}</span>
                    {badges[view] && badges[view].count > 0 && <span className="sr-only">, {badges[view].count} {badges[view].label}</span>}
                  </SidebarMenuButton>
                  {badges[view] && badges[view].count > 0 && (
                    <SidebarMenuBadge aria-hidden="true" title={`${badges[view].count} ${badges[view].label}`}
                      className={`top-1.5! h-5 rounded-full px-1.5 text-[11px] ${badgeTone[badges[view].tone]}`}>
                      {badges[view].count > 99 ? "99+" : badges[view].count}
                    </SidebarMenuBadge>
                  )}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      ))}
    </nav>
  );
}

function HealthIndicator({ health, unavailable }: { health: Health | null | undefined; unavailable: boolean }) {
  const ready = health?.status === "ready";
  const label = unavailable ? "API unreachable" : !health ? "Checking" : ready ? "All systems ready" : "Degraded";
  const tone = unavailable || (health && !ready) ? "bg-danger" : ready ? "bg-success" : "bg-muted-foreground";
  return (
    <span className="hidden items-center gap-2 text-xs text-muted-foreground md:inline-flex" role="status">
      <span className={`size-1.5 rounded-full ${tone}`} aria-hidden="true" />
      {label}
    </span>
  );
}

export function AppShell({ activeView, detailLabel, health, healthUnavailable, runtimeConfig, onNavigate, onOpenSearch, badges, children }: AppShellProps) {
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon" variant="inset">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" render={<a href={viewPaths.overview} onClick={(event) => { if (!isPlainClick(event)) return; event.preventDefault(); onNavigate("overview"); }} />}>
                <span className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground" aria-hidden="true">
                  <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.6">
                    <path d="M2.5 11.5 6 4.5l3 5 1.5-2.5 3 4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
                <span className="grid flex-1 text-left leading-tight">
                  <span className="truncate text-sm font-semibold">AgentOps</span>
                  <span className="truncate text-xs text-muted-foreground">Customer service</span>
                </span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <NavigationLinks activeView={activeView} onNavigate={onNavigate} badges={badges} />
        </SidebarContent>
        <SidebarFooter>
          <div className="grid gap-0.5 rounded-lg px-2 py-1.5 text-xs group-data-[collapsible=icon]:hidden">
            <span className="text-muted-foreground">Decision model</span>
            <span className="truncate font-mono text-[11px] text-sidebar-foreground">
              {runtimeConfig ? `${runtimeConfig.model}` : "Loading…"}
            </span>
            <span className="text-muted-foreground">
              {runtimeConfig ? (runtimeConfig.live_proposal_available ? "Live proposals available" : "Recorded replay only") : " "}
            </span>
          </div>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/85 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/70 sm:px-6">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mx-1 h-4 data-vertical:self-center" />
          <Breadcrumb className="min-w-0">
            <BreadcrumbList className="flex-nowrap text-[15px]">
              <BreadcrumbItem className="hidden sm:inline-flex">
                {detailLabel ? (
                  <BreadcrumbLink render={<a href={viewPaths[activeView]} onClick={(event) => { if (!isPlainClick(event)) return; event.preventDefault(); onNavigate(activeView); }} />}>
                    {viewTitles[activeView]}
                  </BreadcrumbLink>
                ) : (
                  <BreadcrumbPage className="font-medium">{viewTitles[activeView]}</BreadcrumbPage>
                )}
              </BreadcrumbItem>
              {detailLabel && (
                <>
                  <BreadcrumbSeparator className="hidden sm:inline-flex" />
                  <BreadcrumbItem className="min-w-0"><BreadcrumbPage className="truncate font-mono text-xs">{detailLabel}</BreadcrumbPage></BreadcrumbItem>
                </>
              )}
            </BreadcrumbList>
          </Breadcrumb>
          <div className="ml-auto flex items-center gap-2.5">
            <Button variant="outline" size="sm" className="hidden h-9 w-64 justify-start gap-2 text-muted-foreground lg:inline-flex xl:w-80" onClick={onOpenSearch}>
              <Search aria-hidden="true" />
              <span className="flex-1 text-left">Search…</span>
              <KbdGroup><Kbd>⌘</Kbd><Kbd>K</Kbd></KbdGroup>
            </Button>
            <Button variant="ghost" size="icon-sm" className="lg:hidden" aria-label="Search" onClick={onOpenSearch}><Search aria-hidden="true" /></Button>
            <HealthIndicator health={health} unavailable={healthUnavailable} />
            <ThemeToggle />
            <span className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground">
              <span className="size-1.5 rounded-full bg-success" aria-hidden="true" />
              <span>Authenticated</span>
            </span>
          </div>
        </header>
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  );
}
