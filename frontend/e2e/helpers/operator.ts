import { expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDirectory = dirname(fileURLToPath(import.meta.url));
const screenshotDirectory = resolve(currentDirectory, "../../../screenshots/operator-e2e");

export type AgentChatProjection = {
  agent_run_id: string;
  message: string;
  request_type: string;
  tool_call: { name?: string; status: string } | null;
  pending_action: { action_id?: string; status: string } | null;
};

export async function loginOperator(page: Page, path = "/overview"): Promise<void> {
  await page.goto(path);
  await expect(page.getByText("AgentOps", { exact: true }).first()).toBeAttached();
  await expect(page.getByText("Authenticated", { exact: true })).toBeVisible();
}

export async function startTestConversation(page: Page, customerId = "1"): Promise<void> {
  await page.getByRole("button", { name: "New test conversation" }).click();
  await page.getByTestId("customer-scope").selectOption(customerId);
}

export async function openScenario(page: Page, scenario: string, focus?: string): Promise<void> {
  const query = new URLSearchParams({ scenario, compact: "1" });
  if (focus) query.set("focus", focus);
  await loginOperator(page, `/showcase?${query.toString()}`);
  await expect(page.getByTestId("investigation-summary")).toBeVisible();
}

export async function submitWorkspaceMessage(
  page: Page,
  message: string,
): Promise<AgentChatProjection> {
  const responsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/agent/chat") && response.request().method() === "POST",
  );
  await page.getByTestId("agent-message").fill(message);
  await page.getByTestId("send-agent-message").click();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const projection = (await response.json()) as AgentChatProjection;
  await expect(page.getByTestId("agent-response").last()).toContainText(projection.message);
  return projection;
}

export async function waitForRunCompletion(page: Page): Promise<void> {
  await expect(page.getByText("Generating bounded response…")).toHaveCount(0);
  await expect(page.getByTestId("agent-message")).toBeEnabled();
}

export async function assertEvidenceVisible(page: Page): Promise<void> {
  await expect(page.getByTestId("evidence-panel")).toBeVisible();
  await expect(page.getByTestId("model-proposal")).toContainText("LLM proposal");
}

export async function assertNoExecution(page: Page): Promise<void> {
  await expect(page.getByTestId("decision-boundary")).not.toContainText("EXECUTED");
}

export async function assertDecisionBoundary(page: Page): Promise<void> {
  await expect(page.getByTestId("decision-boundary")).toBeVisible();
  await expect(page.getByTestId("decision-boundary")).toContainText("Deterministic decision");
}

export async function captureJourney(page: Page, filename: string): Promise<void> {
  if (process.env.E2E_CAPTURE_SCREENSHOTS !== "1") return;
  await mkdir(screenshotDirectory, { recursive: true });
  await page.screenshot({ path: resolve(screenshotDirectory, filename), fullPage: false });
}
