import { QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/primitives/resizable";
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
import { MonitoringPage } from "../features/monitoring/MonitoringPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { RunsPage } from "../features/runs/RunsPage";
import { ChatWindow } from "../features/chat/ChatWindow";
import { queryClient, useChatMutation, useConversationAction, useDemoScenarios, useInbox, useOperatorConversation, useOperatorMe, useHealth, useMemory, useRun, useRuns, useRuntimeConfig } from "../lib/queries";
import type { ConversationTurn, OperatorConversation, PlaygroundExecution, PlaygroundRequest } from "../types";
import { AppShell } from "./AppShell";
import { CommandMenu } from "./CommandMenu";
import { viewFromPath, viewPaths, type ConsoleView } from "./routes";

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

function Console({ onUnauthenticated }: { onUnauthenticated: () => void }) {
  const [locationPath, setLocationPath] = useState(() => window.location.pathname);
  const [view, setView] = useState<ConsoleView>(() => viewFromPath(window.location.pathname));
  const [searchOpen, setSearchOpen] = useState(false);
  const [target, setTarget] = useState<ThreadTarget | null>(null);
  const [playgroundConversationId] = useState(() => newConversationId());
  // Conversations this operator started as a test customer may keep sending as that customer.
  const [testConversations, setTestConversations] = useState<Set<string>>(() => new Set());
  const [turnsByConversation, setTurnsByConversation] = useState<Record<string, ConversationTurn[]>>({});
  const [inspectRunId, setInspectRunId] = useState<string | null>(null);
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
  const operatorMe = useOperatorMe(true);
  const openConversation = useOperatorConversation(target?.kind === "conversation" ? target.id : null);
  const conversationAction = useConversationAction();
  const threadDetail = target?.kind === "conversation" ? openConversation.data : undefined;
  const customerId = target?.kind === "draft" ? target.customerId : threadDetail?.customer_id ?? 1;
  const conversationId = target?.kind === "draft" ? target.conversationId : threadDetail?.conversation_id ?? null;
  const memoryCustomer = routeRun.data?.customer_id ?? customerId;
  const memory = useMemory(memoryCustomer, true);
  const chat = useChatMutation();

  // Any 401 from a query or mutation sends the operator back to the auth boundary.
  useEffect(() => {
    const failures = [health.error, runsQuery.error, routeRun.error, memory.error, chat.error];
    if (failures.some((error) => error instanceof ApiError && error.status === 401)) onUnauthenticated();
  }, [health.error, runsQuery.error, routeRun.error, memory.error, chat.error, onUnauthenticated]);

  useEffect(() => {
    const onPopState = () => { setLocationPath(window.location.pathname); setView(viewFromPath(window.location.pathname)); };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((path: string) => {
    window.history.pushState({}, "", path);
    setLocationPath(window.location.pathname);
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
      const first = list.find((item) => item.owner === "queued") ?? list.find((item) => item.owner !== "closed") ?? list[0];
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

  const selectConversation = (conversation: OperatorConversation) => { setTarget({ kind: "conversation", id: conversation.id }); chat.reset(); conversationAction.reset(); };
  const startTestConversation = () => { setTarget({ kind: "draft", conversationId: newConversationId(), customerId: 1 }); chat.reset(); conversationAction.reset(); };
  const chatError = chat.error ? errorMessage(chat.error, "The operator API is unavailable.") : null;

  const detailLabel = routeRunId ?? null;
  const activeView: ConsoleView = routeRunId ? "traces" : view;

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
        onOpenRun={openRun}
        onOpenConversations={() => navigateToView("workspace")}
        onOpenRuns={() => navigateToView("traces")}
        onExploreDemo={() => { setPlaygroundExecution(null); navigate("/playground?demo=refund-memory-rag"); }}
      />
    );
  } else if (view === "traces") {
    content = <RunsPage runs={allRuns} loading={runsQuery.isPending} error={runsQuery.error ? errorMessage(runsQuery.error, "Runs are unavailable.") : null} onOpenRun={openRun} />;
  } else if (view === "monitoring") {
    content = <MonitoringPage runs={liveRuns} health={health.data} loading={runsQuery.isPending} healthUnavailable={health.isError} onOpenRun={openRun} />;
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
      />
    ) : <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading inbox…</div>;
    content = wide ? (
      <ResizablePanelGroup orientation="horizontal" className="workspace-panels h-[calc(100svh-3.5rem)] min-h-0" id="conversations-layout">
        <ResizablePanel defaultSize="26%" minSize="18%" maxSize="40%" className="min-w-0 border-r-0 bg-sidebar/40">{rail}</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel defaultSize="74%" minSize="50%" className="min-w-0">{thread}</ResizablePanel>
      </ResizablePanelGroup>
    ) : (
      <div className="grid divide-y divide-border">{thread}<div className="max-h-96">{rail}</div></div>
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
        health={health.data}
        healthUnavailable={health.isError}
        runtimeConfig={runtimeConfig.data}
        onNavigate={navigateToView}
        onOpenSearch={() => setSearchOpen(true)}
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
      />
      <CommandMenu open={searchOpen} onOpenChange={setSearchOpen} runs={liveRuns} onNavigate={navigateToView} onOpenRun={openRun} />
    </>
  );
}

export function App() {
  const [auth, setAuth] = useState<AuthSnapshot>(() => getApiAuthSnapshot());
  useEffect(() => { initializeApiAuth().then(setAuth); }, []);
  const onUnauthenticated = useCallback(() => { queryClient.clear(); setAuth(getApiAuthSnapshot()); }, []);
  if (auth.status !== "authenticated") return <AuthBoundary snapshot={auth} />;
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delay={300}>
        <Console onUnauthenticated={onUnauthenticated} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
