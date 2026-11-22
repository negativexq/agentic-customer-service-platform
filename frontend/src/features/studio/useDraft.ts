import { studioApi, useAgentConfig, useConfigMutation } from "./queries";
import type { AgentConfig } from "./types";

const empty: AgentConfig = { guidance: [], actions: {} };

/** The configuration operators edit: the open draft if there is one, otherwise a copy of what is live. */
export function useDraft() {
  const state = useAgentConfig();
  const save = useConfigMutation((config: AgentConfig) => studioApi.saveDraft(config));
  const working = state.data ? state.data.draft ?? state.data.published : empty;
  return {
    state,
    working,
    save,
    update: (change: (config: AgentConfig) => AgentConfig) => save.mutate(change(structuredClone(working))),
  };
}
