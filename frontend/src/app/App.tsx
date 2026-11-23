import { QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/primitives/resizable";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/primitives/sheet";
import { TooltipProvider } from "@/components/primitives/tooltip";
import { ApiError, getApiAuthSnapshot, initializeApiAuth } from "../api/client";
import type { AuthSnapshot } from "../auth/provider";
import { AgentPlayground } from "../components/AgentPlayground";
import { ArchitectureOverview } from "../components/ArchitectureOverview";
import { DemoShowcase } from "../components/DemoShowcase";
import { RunInvestigationPage } from "../components/RunInvestigationPage";
import { SafetyDashboard } from "../components/SafetyDashboard";
import { ConversationList } from "../features/conversations/ConversationList";
import { ConversationThread, type ThreadTarget } from "../features/conversations/ConversationThread";
import { RunInspectorSheet } from "../features/conversations/RunInspectorSheet";
import { ConversationInspectorSheet } from "../features/conversations/ConversationInspectorSheet";
import { MonitoringPage } from "../features/monitoring/MonitoringPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { RunsPage } from "../features/runs/RunsPage";
import { ChatWindow } from "../features/chat/ChatWindow";
import { useErrorLog, useQuality } from "../features/improve/queries";
import type { NavBadge } from "./AppShell";
import { buildNotifications } from "./notifications";
import type { ConsoleUser } from "./ProfileMenu";
import { readSession, signOut, type DemoSession } from "../auth/demoSession";
import { SignInPage } from "../auth/SignInPage";
import { queryClient, useChatMutation, useConversationAction, useConversationRuns, useDemoScenarios, useInbox, useOperatorConversation, useOperatorMe, useHealth, useMemory, useRun, useRuns, useRuntimeConfig } from "../lib/queries";
import type { ConversationTurn, OperatorConversation, PlaygroundExecution, PlaygroundRequest } from "../types";
import { AppShell } from "./AppShell";
import { CommandMenu } from "./CommandMenu";
import { viewFromPath, viewPaths, type ConsoleView } from "./routes";
import { readPreferences } from "@/lib/preferences";
import { SettingsPage } from "../features/settings/SettingsPage";
import { CustomerContextPanel } from "../features/workspace/CustomerContextPanel";
import { ActionsPage } from "../features/studio/ActionsPage";
import { GuidancePage, type GuidanceSeed } from "../features/studio/GuidancePage";
import { coachingSeed } from "../features/improve/coaching";
import { ErrorsPage } from "../features/improve/ErrorsPage";
import { AuditPage } from "../features/audit/AuditPage";
import { OptimizePage } from "../features/improve/OptimizePage";
import { PerformancePage } from "../features/improve/PerformancePage";
import { QualityPage } from "../features/improve/QualityPage";
import { SimulationsPage } from "../features/improve/SimulationsPage";
import { humanize } from "../features/runs/runPresentation";
import { KnowledgePage } from "../features/studio/KnowledgePage";
import { ProceduresPage } from "../features/studio/ProceduresPage";

function AuthBoundary({ snapshot }: { snapshot: AuthSnapshot }) {
  const loading = snapshot.status === "loading";
  const message = loading
    ? "Establishing the operator session…"
    : snapshot.status === "misconfigured"
      ? "Production authentication is not configured. Connect the console to an external identity or session provider before use."
      : snapshot.status === "unauthenticated"
        ? "Your operator session is missing or expired. Sign in through the configured identity provider."
        : "Authentication failed. Check the identity provider configuration.";
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6">
      <section className="w-full max-w-md rounded-xl border border-border bg-card p-8 text-center">
        <h1 className="text-lg font-semibold">{loading ? "Connecting" : "Sign in required"}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{message}</p>
      </section>
    </main>
  );
}

function useWideLayout() {
  const query = "(min-width: 1024px)";
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = () => setWide(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return wide;
}

function newConversationId() {
  return `operator-${crypto.randomUUID().slice(0, 8)}`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function Console({ onUnauthenticated, user, onSignOut }: { onUnauthenticated: () => void; user: ConsoleUser; onSignOut: () => void }) {
  const [locationPath, setLocationPath] = useState(() => window.location.pathname);
  const [locationSearch, setLocationSearch] = useState(() => window.location.search);
  const [view, setView] = useState<ConsoleView>(() => viewFromPath(window.location.pathname));
  const [searchOpen, setSearchOpen] = useState(false);
  const [target, setTarget] = useState<ThreadTarget | null>(null);
  const [playgroundConversationId] = useState(() => newConversationId());
  // Conversations this operator started as a test customer may keep sending as that customer.
  const [testConversations, setTestConversations] = useState<Set<string>>(() => new Set());
  const [turnsByConversation, setTurnsByConversation] = useState<Record<string, ConversationTurn[]>>({});
  const [inspectRunId, setInspectRunId] = useState<string | null>(null);
  const [conversationDebugOpen, setConversationDebugOpen] = useState(false);
  // Customer details start closed so the thread has room; the operator opens them on demand.
  const [contextOpen, setContextOpen] = useState(false);
  // Narrow screens show one pane at a time (list or thread) and customer details in a sheet.
  const [mobilePane, setMobilePane] = useState<"list" | "thread">("list");
  const [contextSheetOpen, setContextSheetOpen] = useState(false);
  const [monitoringConversation, setMonitoringConversation] = useState("all");
  const [guidanceSeed, setGuidanceSeed] = useState<GuidanceSeed | null>(null);
  const [playgroundExecution, setPlaygroundExecution] = useState<PlaygroundExecution | null>(null);
  const wide = useWideLayout();

  const routeRunId = locationPath.startsWith("/runs/") ? decodeURIComponent(locationPath.slice("/runs/".length).split("/")[0]) : null;
  const health = useHealth(true);
  const runtimeConfig = useRuntimeConfig(true);
  const scenarios = useDemoScenarios(true);
  const runsQuery = useRuns(true);
  const routeRun = useRun(routeRunId, scenarios.data, true);
  const inspectRun = useRun(inspectRunId, scenarios.data, true);
  const inbox = useInbox(true);
  const quality = useQuality(30);
  const errorLog = useErrorLog(7);
  const badges = useMemo(() => {
    const waiting = (inbox.data ?? []).filter((item) => item.owner === "queued").length;
    const flags = (quality.data?.monitors ?? []).reduce((sum, monitor) => sum + monitor.open_flags, 0);
    const daily = errorLog.data?.daily ?? [];
    const today = daily[daily.length - 1];
    const failedToday = today && today.day === new Date().toLocaleDateString("en-CA") ? today.total : 0;
    return {
      workspace: { count: waiting, tone: "warning", label: "waiting for a person" },
      quality: { count: flags, tone: "neutral", label: "open flags" },
      errors: { count: failedToday, tone: "danger", label: "failed today" },
    } satisfies Partial<Record<ConsoleView, NavBadge>>;
  }, [inbox.data, quality.data, errorLog.data]);
  const notifications = useMemo(
    () => buildNotifications({ health: health.data, healthUnavailable: health.isError, inbox: inbox.data ?? [], errors: errorLog.data, quality: quality.data }),
    [health.data, health.isError, inbox.data, errorLog.data, quality.data],
  );
  const operatorMe = useOperatorMe(true);
  const openConversation = useOperatorConversation(target?.kind === "conversation" ? target.id : null);
  const conversationAction = useConversationAction();
  const threadDetail = target?.kind === "conversation" ? openConversation.data : undefined;
  const customerId = target?.kind === "draft" ? target.customerId : threadDetail?.customer_id ?? 1;
  const conversationId = target?.kind === "draft" ? target.conversationId : threadDetail?.conversation_id ?? null;
  const memoryCustomer = routeRun.data?.customer_id ?? customerId;
  const memory = useMemory(memoryCustomer, true);
  const chat = useChatMutation();
  const conversationRuns = useConversationRuns(conversationId, conversationDebugOpen);

  // Any 401 from a query or mutation sends the operator back to the auth boundary.
  useEffect(() => {
    const failures = [health.error, runsQuery.error, routeRun.error, memory.error, chat.error];
    if (failures.some((error) => error instanceof ApiError && error.status === 401)) onUnauthenticated();
  }, [health.error, runsQuery.error, routeRun.error, memory.error, chat.error, onUnauthenticated]);

  useEffect(() => {
    const onPopState = () => { setLocationPath(window.location.pathname); setLocationSearch(window.location.search); setView(viewFromPath(window.location.pathname)); };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((path: string) => {
    window.history.pushState({}, "", path);
    setLocationPath(window.location.pathname);
    setLocationSearch(window.location.search);
    setView(viewFromPath(window.location.pathname));
    window.scrollTo({ top: 0 });
  }, []);
  const navigateToView = useCallback((next: ConsoleView) => navigate(viewPaths[next]), [navigate]);
  const openRun = useCallback((runId: string) => navigate(`/runs/${encodeURIComponent(runId)}`), [navigate]);

  const scenarioRuns = useMemo(() => (scenarios.data ?? []).map((scenario) => scenario.run), [scenarios.data]);
  const liveRuns = runsQuery.data ?? [];
  const allRuns = useMemo(
    () => [...scenarioRuns, ...liveRuns.filter((run) => !scenarioRuns.some((recorded) => recorded.run_id === run.run_id))],
    [scenarioRuns, liveRuns],
  );
  const conversationKey = conversationId ? `${customerId}:${conversationId}` : "";
  const turns = turnsByConversation[conversationKey] ?? [];

  // Open the most relevant conversation first; a sent test draft becomes its real conversation.
  useEffect(() => {
    const list = inbox.data;
    if (!list) return;
    if (target === null) {
      const byRecent = [...list].sort((x, y) => y.updated_at.localeCompare(x.updated_at));
      const waiting = readPreferences().openWaitingFirst ? byRecent.find((item) => item.owner === "queued") : undefined;
      const first = waiting ?? byRecent.find((item) => item.owner !== "closed") ?? byRecent[0];
      setTarget(first ? { kind: "conversation", id: first.id } : { kind: "draft", conversationId: newConversationId(), customerId: 1 });
      return;
    }
    if (target.kind === "draft" && turnsByConversation[`${target.customerId}:${target.conversationId}`]?.length) {
      const created = list.find((item) => item.conversation_id === target.conversationId && item.customer_id === target.customerId);
      if (created) setTarget({ kind: "conversation", id: created.id });
    }
  }, [inbox.data, target, turnsByConversation]);

  const sendAsCustomer = async (message: string) => {
    if (!conversationId) return;
    const key = conversationKey;
    try {
      const result = await chat.mutateAsync({ conversationId, customerId, message });
      setTurnsByConversation((current) => ({ ...current, [key]: [...(current[key] ?? []), { request: message, response: result.response }] }));
      setTestConversations((current) => new Set(current).add(key));
    } catch {
      // The mutation error is rendered by the workspace.
    }
  };

  const runAction = (action: "claim" | "release" | "resolve" | "reply", content?: string) => {
    if (!threadDetail) return Promise.resolve();
    const base = { id: threadDetail.id, version: threadDetail.ownership_version };
    const input = action === "reply"
      ? { ...base, action, content: content ?? "", clientMessageId: crypto.randomUUID() }
      : { ...base, action };
    return conversationAction.mutateAsync(input).then(() => undefined, () => undefined);
  };

  const runPlayground = async (request: PlaygroundRequest) => {
    try {
      const result = await chat.mutateAsync({ conversationId: playgroundConversationId, customerId: request.customerId, message: request.message, executionMode: request.executionMode });
      setPlaygroundExecution({
        request,
        requestPayload: { conversation_id: playgroundConversationId, customer_id: request.customerId, message: "[redacted from operator projection]", execution_mode: request.executionMode },
        response: result.response,
        run: result.run,
      });
    } catch {
      // Rendered through the shared error below.
    }
  };

  const selectConversation = (conversation: OperatorConversation) => { setTarget({ kind: "conversation", id: conversation.id }); setMobilePane("thread"); chat.reset(); conversationAction.reset(); };
  const startTestConversation = () => { setTarget({ kind: "draft", conversationId: newConversationId(), customerId: 1 }); setMobilePane("thread"); chat.reset(); conversationAction.reset(); };
  const chatError = chat.error ? errorMessage(chat.error, "The operator API is unavailable.") : null;

  const detailLabel = routeRunId ?? null;
  const activeView: ConsoleView = routeRunId ? "traces" : view;

  const searchParam = (name: string) => new URLSearchParams(locationSearch).get(name);
  const coach = (seed: GuidanceSeed) => { setInspectRunId(null); setGuidanceSeed(seed); navigateToView("guidance"); };
  const showConversation = (pk: number) => { setTarget({ kind: "conversation", id: pk }); setMobilePane("thread"); setContextSheetOpen(false); chat.reset(); conversationAction.reset(); navigateToView("workspace"); };
  const openProcedure = (intent: string) => navigate(`/procedures?intent=${encodeURIComponent(intent)}`);
  let content: React.ReactNode;
  if (routeRunId) {
    content = (
      <div className="p-4 sm:p-6">
        <RunInvestigationPage
          key={routeRunId}
          run={routeRun.data ?? null}
          demoScenario={scenarios.data?.find((item) => item.run.run_id === routeRunId)}
          memoryRecords={routeRun.data?.run_id.startsWith("demo-") ? [] : memory.data ?? []}
          availableRuns={allRuns}
          loading={routeRun.isPending && routeRun.fetchStatus !== "idle" || (routeRunId.startsWith("demo-") && scenarios.isPending)}
          error={routeRun.error ? errorMessage(routeRun.error, "Investigation evidence unavailable") : null}
          onBack={() => navigateToView("traces")}
        />
      </div>
    );
  } else if (view === "overview") {
    content = (
      <OverviewPage
        runs={liveRuns}
        inbox={inbox.data ?? []}
        inboxLoading={inbox.isPending}
        onOpenRun={openRun}
        onOpenConversation={showConversation}
        onOpenConversations={() => navigateToView("workspace")}
        onNavigate={navigateToView}
        onExploreDemo={() => { setPlaygroundExecution(null); navigate("/playground?demo=refund-memory-rag"); }}
      />
    );
  } else if (view === "traces") {
    content = <RunsPage runs={allRuns} loading={runsQuery.isPending} error={runsQuery.error ? errorMessage(runsQuery.error, "Runs are unavailable.") : null} onOpenRun={openRun} />;
  } else if (view === "knowledge") {
    content = <KnowledgePage key={locationSearch} initialDocument={searchParam("document")} onOpenProcedure={(intent) => navigate(`/procedures?intent=${encodeURIComponent(intent)}`)} />;
  } else if (view === "guidance") {
    content = <GuidancePage key={guidanceSeed ? "seeded" : "plain"} seed={guidanceSeed} onSeedUsed={() => setGuidanceSeed(null)} />;
  } else if (view === "procedures") {
    content = <ProceduresPage key={locationSearch} initialIntent={searchParam("intent")}
      onOpenAction={(name) => navigate(`/actions?action=${encodeURIComponent(name)}`)}
      onOpenGuidance={() => navigateToView("guidance")}
      onOpenKnowledge={(id) => navigate(`/knowledge?document=${encodeURIComponent(id)}`)} />;
  } else if (view === "actions") {
    content = <ActionsPage key={locationSearch} initialAction={searchParam("action")} onOpenProcedure={(intent) => navigate(`/procedures?intent=${encodeURIComponent(intent)}`)} />;
  } else if (view === "performance") {
    content = <PerformancePage onOpenProcedure={openProcedure} />;
  } else if (view === "optimize") {
    content = (
      <OptimizePage onCoach={coach} onOpenConversation={showConversation} onOpenRun={openRun}
        onFix={(gap) => {
          if (gap.fix === "guidance") coach(coachingSeed({ title: gap.title, context: gap.examples[0]?.question ? `a customer asks “${gap.examples[0].question}”` : gap.description, intent: gap.intent, runId: gap.examples[0]?.run_id }));
          else if (gap.fix === "actions") navigate(gap.key.startsWith("action:disabled:") ? `/actions?action=${encodeURIComponent(gap.key.slice(16))}` : "/actions");
          else if (gap.fix === "procedures" && gap.intent) openProcedure(gap.intent);
          else navigateToView(gap.fix);
        }} />
    );
  } else if (view === "quality") {
    content = <QualityPage onCoach={coach} onOpenConversation={showConversation} onOpenRun={openRun} />;
  } else if (view === "simulations") {
    content = <SimulationsPage />;
  } else if (view === "audit") {
    content = <AuditPage you={operatorMe.data ? { actorId: operatorMe.data.actor_id, name: user.name } : null} onOpenConversation={showConversation} onOpenRun={openRun} />;
  } else if (view === "errors") {
    content = <ErrorsPage onOpenRun={openRun} />;
  } else if (view === "settings") {
    content = <SettingsPage runtimeConfig={runtimeConfig.data} health={health.data} />;
  } else if (view === "monitoring") {
    content = <MonitoringPage key={monitoringConversation} initialConversation={monitoringConversation} runs={liveRuns} health={health.data} loading={runsQuery.isPending} healthUnavailable={health.isError} onOpenRun={openRun} />;
  } else if (view === "workspace") {
    const rail = (
      <ConversationList
        conversations={inbox.data ?? []}
        loading={inbox.isPending}
        selectedId={target?.kind === "conversation" ? target.id : null}
        draftActive={target?.kind === "draft"}
        onSelect={selectConversation}
        onNewTest={startTestConversation}
      />
    );
    const thread = target ? (
      <ConversationThread
        key={target.kind === "draft" ? target.conversationId : target.id}
        target={target}
        detail={threadDetail}
        loading={openConversation.isPending && target.kind === "conversation"}
        me={operatorMe.data}
        localTurns={turns}
        canSimulateCustomer={target.kind === "draft" || testConversations.has(conversationKey)}
        busy={chat.isPending}
        error={chatError}
        actionPending={conversationAction.isPending}
        actionError={conversationAction.error ? errorMessage(conversationAction.error, "The conversation could not be updated.") : null}
        onCustomerChange={(id) => setTarget((current) => (current?.kind === "draft" ? { ...current, customerId: id } : current))}
        onSendAsCustomer={sendAsCustomer}
        onReply={(content) => runAction("reply", content)}
        onAction={(action) => { void runAction(action); }}
        onInspect={setInspectRunId}
        onInspectConversation={() => setConversationDebugOpen(true)}
        contextOpen={wide ? contextOpen : contextSheetOpen}
        onToggleContext={wide
          ? () => setContextOpen((open) => !open)
          : () => setContextSheetOpen((open) => !open)}
        onBack={wide ? undefined : () => setMobilePane("list")}
      />
    ) : <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading inbox…</div>;
    content = wide ? (
      // The fixed-height wrapper keeps the inbox to the viewport: the panel library sets an
      // inline height on the group that would otherwise let a long list stretch the page.
      <><h1 className="sr-only">Conversations</h1><div className="h-[calc(100svh-3.5rem)] min-h-0 overflow-hidden md:h-[calc(100svh-4.5rem-2px)]"><ResizablePanelGroup orientation="horizontal" className="workspace-panels h-full min-h-0" id="conversations-layout">
        <ResizablePanel defaultSize="380px" minSize="300px" maxSize="40%" className="min-w-0 border-r-0 bg-sidebar/40">{rail}</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel minSize="40%" className="min-w-0">{thread}</ResizablePanel>
        {contextOpen && target?.kind === "conversation" && threadDetail && (
          <>
            <ResizableHandle />
            <ResizablePanel id="customer-context" defaultSize="24%" minSize="18%" maxSize="36%" className="min-w-0 overflow-y-auto border-l-0 bg-sidebar/30">
              <CustomerContextPanel key={threadDetail.id} conversationPk={threadDetail.id} customerId={threadDetail.customer_id} onOpenConversation={(id) => setTarget({ kind: "conversation", id })} />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup></div></>
    ) : (
      <div className="flex min-h-[calc(100svh-3.5rem)] flex-col md:min-h-[calc(100svh-4.5rem-2px)]">
        <h1 className="sr-only">Conversations</h1>
        {mobilePane === "list" ? <div className="min-h-0 flex-1">{rail}</div> : thread}
        <Sheet open={contextSheetOpen && mobilePane === "thread" && target?.kind === "conversation" && Boolean(threadDetail)} onOpenChange={setContextSheetOpen}>
          <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-md">
            <SheetHeader className="border-b border-border px-4 py-3 pr-12"><SheetTitle>Customer details</SheetTitle></SheetHeader>
            {threadDetail && <CustomerContextPanel key={threadDetail.id} conversationPk={threadDetail.id} customerId={threadDetail.customer_id} onOpenConversation={(id) => { setTarget({ kind: "conversation", id }); setContextSheetOpen(false); }} />}
          </SheetContent>
        </Sheet>
      </div>
    );
  } else if (view === "playground") {
    content = (
      <div className="p-4 sm:p-6">
        <AgentPlayground
          runtimeConfig={runtimeConfig.data ?? null}
          busy={chat.isPending}
          error={chatError}
          execution={playgroundExecution}
          demoScenarios={scenarios.data ?? []}
          onRun={runPlayground}
          onClear={() => { setPlaygroundExecution(null); chat.reset(); }}
          onOpenLatestTrace={() => { if (playgroundExecution) openRun(playgroundExecution.run.run_id); }}
        />
      </div>
    );
  } else if (view === "chat") {
    content = <div className="p-4 sm:p-6"><ChatWindow /></div>;
  } else if (view === "architecture") {
    content = <div className="p-4 sm:p-6"><ArchitectureOverview /></div>;
  } else if (view === "showcase") {
    content = <div className="p-4 sm:p-6"><DemoShowcase scenarios={scenarios.data ?? []} /></div>;
  } else {
    content = <div className="p-4 sm:p-6"><SafetyDashboard onOpenRuns={() => navigateToView("traces")} /></div>;
  }

  return (
    <>
      <AppShell
        activeView={activeView}
        detailLabel={detailLabel}
        user={user}
        notifications={notifications}
        onSelectNotification={(item) => (item.target.kind === "conversation" ? showConversation(item.target.id) : navigateToView(item.target.view))}
        onSignOut={onSignOut}
        onNavigate={navigateToView}
        onOpenSearch={() => setSearchOpen(true)}
        badges={badges}
      >
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">{content}</main>
      </AppShell>
      <RunInspectorSheet
        runId={inspectRunId}
        run={inspectRun.data ?? null}
        loading={inspectRun.isPending && inspectRun.fetchStatus !== "idle"}
        memoryRecords={inspectRun.data?.customer_id === memoryCustomer ? memory.data ?? [] : []}
        onClose={() => setInspectRunId(null)}
        onOpenInvestigation={(runId) => { setInspectRunId(null); openRun(runId); }}
        onCoach={(run) => coach(coachingSeed({ title: `Coaching: ${humanize(run.intent)}`, context: `the assistant handles a ${humanize(run.intent).toLowerCase()} request like run ${run.run_id}`, intent: run.intent, runId: run.run_id }))}
      />
      <ConversationInspectorSheet
        open={conversationDebugOpen && target?.kind === "conversation"}
        conversationId={conversationId}
        customerName={threadDetail?.customer_name ?? "Customer"}
        runs={conversationRuns.data?.runs ?? (conversationRuns.isError ? [] : undefined)}
        loading={conversationRuns.isPending}
        onClose={() => setConversationDebugOpen(false)}
        onOpenRun={setInspectRunId}
        onOpenMonitoring={(id) => { setConversationDebugOpen(false); setMonitoringConversation(id); navigateToView("monitoring"); }}
      />
      <CommandMenu open={searchOpen} onOpenChange={setSearchOpen} runs={liveRuns} onNavigate={navigateToView} onOpenRun={openRun} />
    </>
  );
}

export function App() {
  const [auth, setAuth] = useState<AuthSnapshot>(() => getApiAuthSnapshot());
  useEffect(() => { initializeApiAuth().then(setAuth); }, []);
  const onUnauthenticated = useCallback(() => { queryClient.clear(); setAuth(getApiAuthSnapshot()); }, []);
  const [session, setSession] = useState<DemoSession | null>(readSession);
  if (!session) return <SignInPage onSignedIn={setSession} />;
  if (auth.status !== "authenticated") return <AuthBoundary snapshot={auth} />;
  const leave = () => { signOut(); queryClient.clear(); setSession(null); };
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delay={300}>
        <Console onUnauthenticated={onUnauthenticated} user={session} onSignOut={leave} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
