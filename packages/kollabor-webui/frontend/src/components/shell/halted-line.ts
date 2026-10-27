import type { PanelDescription } from "../../api.ts";

/** What the sidebar says while a halt holds this folder, else null. Pure, for tests. */
export function haltedLine(panel: PanelDescription | null): { text: string; link: string } | null {
  const picker = panel as { summary?: { label: string; value: string }[]; rows?: { actions?: string[] }[]; toolbar_actions?: { id: string }[] } | null;
  const state = picker?.summary?.find((item) => item.label === "Network State")?.value ?? "";
  if (!state.startsWith("Halted by ")) return null;
  // Spec: "Network halted by <device> at <time> (their clock)"; the reason stays on the halt screen.
  const text = `Network h${state.slice(1).split("(their clock)")[0]}(their clock)`;
  const rows = picker?.rows ?? [];
  const link = rows.some((row) => row.actions?.includes("confirm_resume"))
    ? "Confirm Resume"
    : rows.some((row) => row.actions?.includes("withdraw"))
      ? "Waiting for a second machine"
      : picker?.toolbar_actions?.some((action) => action.id === "propose_resume")
        ? "Propose Resume"
        : "Halt and Resume";
  return { text, link };
}
