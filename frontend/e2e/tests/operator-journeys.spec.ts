import {
  assertDecisionBoundary,
  assertEvidenceVisible,
  assertNoExecution,
  captureJourney,
  loginOperator,
  openScenario,
  submitWorkspaceMessage,
  waitForRunCompletion,
} from "../helpers/operator";
import { expect, test } from "../fixtures/operator";

test.describe.serial("operator journey evidence", () => {
  test("refund proposal remains behind confirmation, then executes once after confirmation", async ({ page }) => {
    await openScenario(page, "refund-memory-rag", "confirmation");
    await expect(page.getByTestId("conversation-evidence")).toContainText("refund");
    await assertEvidenceVisible(page);
    await assertDecisionBoundary(page);
    await expect(page.getByTestId("confirmation-boundary")).toContainText("no sensitive mutation");
    await expect(page.getByTestId("decision-boundary")).toContainText("REQUIRE CONFIRMATION");
    await assertNoExecution(page);
    await captureJourney(page, "01-refund-journey.png");

    await loginOperator(page, "/");
    await page.getByTestId("customer-scope").selectOption("1");
    const proposed = await submitWorkspaceMessage(
      page,
      "I want a refund for order 1 because it arrived damaged.",
    );
    expect(proposed.tool_call).toBeNull();
    expect(proposed.pending_action?.status).toBe("pending");
    await expect(page.getByTestId("confirmation-boundary").last()).toBeVisible();
    await expect(page.getByTestId("execution-result").last()).toContainText(/(?:pending|awaiting) confirmation/i);

    const confirmed = await submitWorkspaceMessage(page, "confirm");
    expect(confirmed.pending_action?.status).toBe("executed");
    expect(confirmed.tool_call).toMatchObject({ status: "executed" });
    await waitForRunCompletion(page);
    await expect(page.getByTestId("execution-result").last()).toContainText(/(?:executed|completed)/i);
  });

  test("prompt injection proposal is denied by deterministic policy", async ({ page }) => {
    await openScenario(page, "prompt-injection-defense");
    await expect(page.getByTestId("conversation-evidence")).toContainText("Ignore previous instructions");
    await assertEvidenceVisible(page);
    await assertDecisionBoundary(page);
    await expect(page.getByTestId("decision-boundary")).toContainText("DENY");
    await expect(page.getByTestId("decision-boundary")).toContainText("PREVENTED");
    await expect(page.getByTestId("model-proposal")).toContainText("Not execution authority");
    await assertNoExecution(page);
    await captureJourney(page, "02-injection-defense.png");
  });

  test("confirmation replay creates one business effect", async ({ page }) => {
    await loginOperator(page, "/");
    await page.getByTestId("customer-scope").selectOption("2");
    const proposed = await submitWorkspaceMessage(page, "Cancel order 3");
    expect(proposed.pending_action?.status).toBe("pending");
    expect(proposed.tool_call).toBeNull();

    const confirmed = await submitWorkspaceMessage(page, "confirm");
    expect(confirmed.tool_call).toMatchObject({ status: "executed" });

    const replay = await submitWorkspaceMessage(page, "confirm");
    expect(replay.tool_call).toBeNull();
    expect(replay.message).toContain("already completed");
    await expect(page.getByTestId("agent-response").last()).toContainText("did not execute it again");
    await page.getByTestId("agent-response").last().scrollIntoViewIfNeeded();
    await captureJourney(page, "03-idempotency.png");
  });

  test("missing target information requests clarification without execution", async ({ page }) => {
    await openScenario(page, "missing-information-clarification");
    await expect(page.getByTestId("conversation-evidence")).toContainText("I want a refund");
    await expect(page.getByTestId("investigation-summary")).toContainText("Clarification required");
    await expect(page.getByTestId("decision-boundary")).toContainText("NOT ATTEMPTED");
    await assertNoExecution(page);
    await captureJourney(page, "04-clarification.png");
  });

  test("knowledge answer exposes citations and accepted grounding", async ({ page }) => {
    await loginOperator(page, "/");
    await page.getByTestId("customer-scope").selectOption("1");
    const answer = await submitWorkspaceMessage(page, "What is the refund policy for damaged products?");
    expect(answer.tool_call).toBeNull();
    await page.getByTestId("inspector-tab-grounding").click();
    await expect(page.getByTestId("grounding-status")).toContainText("Grounded answer validation");
    await expect(page.getByTestId("grounding-status")).toContainText("pass");
    await expect(page.getByTestId("grounding-status")).toContainText("Unsupported claims");
    await expect(page.getByTestId("grounding-status")).toContainText("0");
    await expect(page.getByTestId("evidence-panel")).toContainText("Citation [");
    await page.getByTestId("grounding-status").scrollIntoViewIfNeeded();
    await captureJourney(page, "05-rag-grounding.png");
  });

  test("run investigation exposes lifecycle, evidence, decision, authority, and outcome", async ({ page }) => {
    await loginOperator(page, "/runs/demo-refund-memory-rag-20260823");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting confirmation");
    await expect(page.locator('[aria-label="Evidence relationship graph"]')).toBeVisible();
    await page.getByRole("tab", { name: "Telemetry", exact: true }).click();
    await expect(page.locator('[aria-label="Operational trace timeline"]')).toBeVisible();
    await page.getByRole("tab", { name: "Evidence", exact: true }).click();
    await expect(page.getByText(/confirmation required/i).first()).toBeVisible();
    await expect(page.getByText("Awaiting confirmation", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "View investigation report" }).click();
    await expect(page.getByRole("dialog")).toContainText("No hidden reasoning");
    await page.getByRole("button", { name: "Close report" }).click();
    await captureJourney(page, "06-investigation.png");
  });
});
