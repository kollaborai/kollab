import { createRoot } from "react-dom/client";
import { useState } from "react";
import type { PanelAction, PanelPicker, PanelRow } from "../../src/api";
import { PickerList } from "../../src/components/panels/PickerList";
import "../../src/styles.css";

declare global {
  interface Window {
    llmProviderRemovalCalls: number;
  }
}

window.llmProviderRemovalCalls = 0;
document.documentElement.classList.add("dark");

const initialRows: PanelRow[] = [
  {
    id: "claude-model",
    label: "claude-model",
    detail: "anthropic-cloud · claude-model",
    group: "Models — Anthropic",
    actions: ["activate", "edit", "set_default", "remove_provider"],
    action_labels: { remove_provider: "Remove Anthropic provider" },
    action_confirmations: {
      remove_provider:
        "Press Remove Anthropic provider again to remove the Anthropic provider (profiles anthropic-cloud and their saved keys); any other action cancels. Saved loadouts using it: saved-anthropic.",
    },
  },
  {
    id: "gpt-model",
    label: "gpt-model",
    detail: "openai · gpt-model",
    group: "Models — OpenAI",
    actions: ["activate", "edit", "set_default", "remove_provider"],
    action_labels: { remove_provider: "Remove OpenAI provider" },
    action_confirmations: {
      remove_provider:
        "Press Remove OpenAI provider again to remove the OpenAI provider (profiles openai and their saved keys); any other action cancels.",
    },
  },
];

const actions: PanelAction[] = [
  { id: "activate", label: "Use" },
  { id: "edit", label: "Edit" },
  { id: "set_default", label: "Set As Default" },
  { id: "remove_provider", label: "Remove Provider", confirm: true },
];

function Fixture() {
  const [rows, setRows] = useState(initialRows);
  const [emptyGroups, setEmptyGroups] = useState([
    {
      group: "Models — Gemini",
      reason: "no models — catalog unavailable",
      id: "gemini-provider",
      actions: ["remove_provider"],
      action_labels: { remove_provider: "Remove Gemini provider" },
      action_confirmations: {
        remove_provider:
          "Press Remove Gemini provider again to remove the Gemini provider (profiles gemini-provider and their saved keys); any other action cancels.",
      },
    },
  ]);
  const panel: PanelPicker = {
    panel: "llm",
    kind: "picker",
    title: "Loadouts",
    rows,
    row_actions: actions,
    empty_groups: emptyGroups,
  };
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-4 p-4 sm:p-6">
      <h1 className="text-lg font-semibold">Settings</h1>
      <section aria-label="Loadouts" className="bg-card rounded-xl p-4 shadow-sm sm:p-6">
        <h2 className="mb-4 text-base font-semibold">Loadouts</h2>
        <PickerList
          panel={panel}
          busy={false}
          draft={{}}
          onDraftChange={() => {}}
          onControlChange={() => {}}
          errors={{}}
          onToolbarAction={() => {}}
          onRowAction={(action, row) => {
            if (action.id === "remove_provider" && row.group === "Models — Anthropic") {
              window.llmProviderRemovalCalls += 1;
              setRows((current) => current.filter((item) => item.group !== "Models — Anthropic"));
            } else if (action.id === "remove_provider" && row.group === "Models — Gemini") {
              window.llmProviderRemovalCalls += 1;
              setEmptyGroups([]);
            }
          }}
        />
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
