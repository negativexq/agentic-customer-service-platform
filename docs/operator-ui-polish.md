# Operator Console presentation

The console retains its React/Tailwind stack and dark theme. Its primary navigation is Overview, Playground, Runs, and Safety. Chat, Workspace, recorded Showcase, and Architecture remain in the More menu.

Each destination has its own URL. `/` continues to open the legacy Workspace; `/workspace` is its canonical navigation URL. `/evidence` remains a Safety alias. Navigation supports reload, browser history, and opening links in a separate tab. Run detail returns to `/traces`, and its selected detail tab is retained in the `tab` query parameter.

Playground starts with an editable damaged-product refund request. Featured presets populate the form without executing a request; selecting another preset clears the preceding result. Scenario settings, session tools, the full scenario library, and runtime configuration are disclosed on demand. A recorded fixture preview is labeled separately from a submitted backend replay or live proposal. Neither mode bypasses server authorization.

Run investigation leads with the scenario/intent and its observed outcome. The run ID is secondary, copyable metadata. A compact decision flow stays above Evidence, Policy, Execution, and Telemetry tabs. Tabs support arrow keys, Home, and End. Bounded evidence, comparisons, exports, and the report dialog remain accessible.

Overview gives the refund workflow one primary entry point and summarizes three control boundaries. Recorded release evaluation results are visually separated from current runtime projections; demo fixture IDs are excluded from runtime counters. Historical evaluation values are not new production measurements.

Shared metadata now uses at least 12px text, panel headings use 18px, and primary content uses 14px. Mobile showcase widths are fluid. The responsive browser regression checks the actual bounding boxes of headings, paragraphs, textareas, and panels at 320, 375, and 768px, so hidden page overflow cannot mask clipped content.

## Reproduce browser verification and screenshots

```sh
npm ci --prefix frontend
npm run lint --prefix frontend
npm run test --prefix frontend
npm run build --prefix frontend
UI_POLISH_SCREENSHOTS=1 E2E_CAPTURE_SCREENSHOTS=0 \
  OPERATOR_E2E_PROJECT_NAME=operator-ui-polish \
  bash scripts/run_operator_e2e.sh
```

This starts an isolated PostgreSQL/Qdrant/backend/frontend deployment and a local semantic proposal provider. Existing refund, injection, duplicate-confirmation, clarification, grounding, and investigation journeys run alongside navigation, scenario-input, tab/keyboard, clipboard, and responsive checks. No external model endpoint is used. The script removes its containers and volumes on exit.

`frontend/e2e/tests/ui-polish.spec.ts` writes actual browser captures to `docs/assets/screenshots` when `UI_POLISH_SCREENSHOTS=1`. The Playground desktop image shows the recorded refund fixture preview, opened from Overview without submitting a runtime request. Investigation shows a labeled fixture; Architecture shows the implemented reference architecture view. These are reference integration screenshots, not production telemetry.
