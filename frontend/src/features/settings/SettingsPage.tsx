import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/primitives/button";
import { Skeleton } from "@/components/primitives/skeleton";
import { defaultPreferences, resetPreferences, updatePreferences, usePreferences, type InboxView, type LiveUpdates } from "@/lib/preferences";
import { useOperatorSettings } from "@/lib/queries";
import { readThemePreference, storeThemePreference, themeChangeEvent, type ThemePreference } from "@/lib/theme";
import { PageHeader } from "../../components/layout/PageHeader";
import type { Health, RuntimeConfig } from "../../types";
import { humanize } from "../runs/runPresentation";
import { SavedRepliesSettings, ServiceLevelSettings } from "../workspace/WorkspaceSettings";
import { Group, Row } from "./layout";

function Value({ children, mono = false }: { children: ReactNode; mono?: boolean }) {
  return <span className={`truncate text-muted-foreground ${mono ? "font-mono text-xs" : ""}`}>{children}</span>;
}

function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string; icon?: ReactNode }[]; onChange: (value: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button key={option.value} type="button" role="radio" aria-checked={active} onClick={() => onChange(option.value)}
            className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
            {option.icon}{option.label}
          </button>
        );
      })}
    </div>
  );
}

function Switch({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors before:absolute before:-inset-x-1.5 before:-inset-y-2.5 before:content-[''] focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none ${checked ? "bg-primary" : "bg-muted-foreground/30"}`}>
      <span className={`inline-block size-4 rounded-full bg-background shadow-sm transition-transform ${checked ? "translate-x-4.5" : "translate-x-0.5"}`} />
    </button>
  );
}

const signInLabels: Record<string, string> = { local_demo: "Local demo session", static: "Static token", oidc: "Single sign-on (OIDC)", internal: "Internal service" };

const inboxOptions: { value: InboxView; label: string }[] = [
  { value: "all", label: "All" }, { value: "open", label: "Open" }, { value: "queued", label: "Needs human" }, { value: "mine", label: "Mine" },
];
const liveOptions: { value: LiveUpdates; label: string }[] = [
  { value: "fast", label: "Real time" }, { value: "normal", label: "Every 5 s" }, { value: "slow", label: "Every 15 s" },
];
const themeOptions: { value: ThemePreference; label: string; icon: ReactNode }[] = [
  { value: "light", label: "Light", icon: <Sun className="size-3.5" aria-hidden="true" /> },
  { value: "dark", label: "Dark", icon: <Moon className="size-3.5" aria-hidden="true" /> },
  { value: "system", label: "System", icon: <Monitor className="size-3.5" aria-hidden="true" /> },
];

function days(value: number): string {
  if (value === 0) return "Kept until deleted";
  if (value % 365 === 0) return `${value / 365} year${value === 365 ? "" : "s"}`;
  return `${value} days`;
}

export function SettingsPage({ runtimeConfig, health }: { runtimeConfig: RuntimeConfig | undefined; health: Health | null | undefined }) {
  const settings = useOperatorSettings(true);
  const preferences = usePreferences();
  const [theme, setTheme] = useState<ThemePreference>(readThemePreference);
  useEffect(() => {
    const sync = () => setTheme(readThemePreference());
    window.addEventListener(themeChangeEvent, sync);
    return () => window.removeEventListener(themeChangeEvent, sync);
  }, []);
  const profile = settings.data;
  const changed = JSON.stringify(preferences) !== JSON.stringify(defaultPreferences);

  return (
    <>
      <PageHeader title="Settings" description="Your profile, how this console behaves for you, and the workspace rules set by your administrator." />
      <div className="mx-auto w-full max-w-5xl px-4 sm:px-6">
        <Group title="Profile" description="Comes from your identity provider. Customers see your display name when you reply.">
          {settings.isPending ? <div className="grid gap-2 p-4"><Skeleton className="h-5" /><Skeleton className="h-5" /></div>
            : settings.isError || !profile ? <p className="px-4 py-3 text-sm text-muted-foreground">Profile could not be loaded.</p>
            : (
              <>
                <Row label="Display name"><span className="font-medium text-foreground">{profile.display_name}</span></Row>
                <Row label="Operator ID"><Value mono>{profile.actor_id}</Value></Row>
                <Row label="Sign-in method"><Value>{signInLabels[profile.sign_in_method] ?? humanize(profile.sign_in_method)}</Value></Row>
                <Row label="Customer access" hint="Which customers' conversations you can read and take over.">
                  <Value>{profile.customer_scope === "tenant" ? "Every customer in this workspace" : `${profile.scoped_customer_count ?? 0} assigned customers`}</Value>
                </Row>
                {profile.roles.length > 0 && <Row label="Roles"><Value>{profile.roles.join(", ")}</Value></Row>}
              </>
            )}
        </Group>

        <Group title="Appearance" description="Saved in this browser.">
          <Row label="Theme">
            <Segmented label="Theme" value={theme} options={themeOptions} onChange={(next) => { setTheme(next); storeThemePreference(next); }} />
          </Row>
        </Group>

        <Group title="Inbox" description="How Conversations opens and stays up to date. Saved in this browser.">
          <Row label="Default view" hint="The list shown when you open Conversations.">
            <Segmented label="Default inbox view" value={preferences.defaultInboxView === "closed" ? "all" : preferences.defaultInboxView} options={inboxOptions} onChange={(defaultInboxView) => updatePreferences({ defaultInboxView })} />
          </Row>
          <Row label="Waiting customers first" hint="Put conversations that need a person at the top and open them first.">
            <Switch label="Waiting customers first" checked={preferences.openWaitingFirst} onChange={(openWaitingFirst) => updatePreferences({ openWaitingFirst })} />
          </Row>
          <Row label="Live updates" hint="How often the inbox and the open conversation refresh.">
            <Segmented label="Live updates" value={preferences.liveUpdates} options={liveOptions} onChange={(liveUpdates) => updatePreferences({ liveUpdates })} />
          </Row>
          <div className="flex justify-end px-4 py-2.5">
            <Button variant="ghost" size="sm" disabled={!changed} onClick={resetPreferences}>Restore defaults</Button>
          </div>
        </Group>

        <ServiceLevelSettings />
        <SavedRepliesSettings />

        <Group title="Data and privacy" description="Set by your administrator for the whole workspace. Sensitive values are masked before anything is stored.">
          {profile ? (
            <>
              <Row label="Conversation messages" hint="Recorded messages are deleted after this period."><Value>{days(profile.message_retention_days)}</Value></Row>
              <Row label="Customer memory" hint="Preferences and facts the AI assistant remembers."><Value>{days(profile.memory_retention_days)}</Value></Row>
              <Row label="Support context memory"><Value>{days(profile.support_context_retention_days)}</Value></Row>
              <Row label="Workspace"><Value mono>{profile.tenant_id}</Value></Row>
            </>
          ) : <div className="grid gap-2 p-4"><Skeleton className="h-5" /><Skeleton className="h-5" /></div>}
        </Group>

        <Group title="AI assistant" description="The model that answers customers. Changed through deployment configuration.">
          {runtimeConfig ? (
            <>
              <Row label="Model"><Value mono>{runtimeConfig.model}</Value></Row>
              <Row label="Provider"><Value>{humanize(runtimeConfig.provider)}</Value></Row>
              <Row label="Environment"><Value>{humanize(runtimeConfig.environment)}</Value></Row>
              <Row label="Live answers" hint="When unavailable, only recorded runs can be replayed.">
                <Value>{runtimeConfig.live_proposal_available ? "Available" : "Unavailable"}</Value>
              </Row>
              {health && <Row label="System status"><Value>{health.status === "ready" ? "All systems ready" : humanize(health.status)}</Value></Row>}
            </>
          ) : <div className="grid gap-2 p-4"><Skeleton className="h-5" /><Skeleton className="h-5" /></div>}
        </Group>
      </div>
    </>
  );
}
