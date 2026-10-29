import { useMemo, useState } from "react";
import { ChevronRight, Search } from "lucide-react";
import type { PanelAction, PanelField, PanelForm, PanelSection } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FieldRenderer, type Draft } from "./FieldRenderer";
import { confirmStep } from "./confirm";
import { titleCase } from "./panel-model";

function matches(field: PanelField, q: string): boolean {
  return [field.label, field.help, field.path].some((text) =>
    (text ?? "").toLowerCase().includes(q),
  );
}

/** Same rule as the terminal's /config search: a title match keeps every field. */
function filterSections(sections: PanelSection[], q: string): PanelSection[] {
  if (!q) return sections;
  return sections.flatMap((section) => {
    if (section.title.toLowerCase().includes(q)) return [section];
    const fields = section.fields.filter((field) => matches(field, q));
    return fields.length ? [{ ...section, fields }] : [];
  });
}

const TARGET_NAMES: Record<string, string> = { local: "Project", global: "Global" };

function buttonLabel(action: PanelAction, target: string | null): string {
  const base = action.label || titleCase(action.id);
  if (!target) return base;
  if (target === "device") return "Save for This Device";
  if (action.id === "save") {
    return target === "local" ? "Save to Project" : "Save Globally";
  }
  return `${base} (${TARGET_NAMES[target] ?? titleCase(target)})`;
}

/** Searchable, collapsible sections of fields with a Project / Global save footer. */
export function FormPanel({
  panel,
  draft,
  errors,
  busy,
  onDraftChange,
  onAction,
}: {
  panel: PanelForm;
  draft: Draft;
  errors: Record<string, string>;
  busy: boolean;
  onDraftChange: (draft: Draft) => void;
  onAction: (action: PanelAction, target: string | null) => void;
}) {
  const [query, setQuery] = useState("");
  // An action with `confirm` asks for a second click, as PickerList's row actions do.
  const [armed, setArmed] = useState<string | null>(null);
  const fire = (action: PanelAction, target: string | null) => {
    const key = `${action.id}:${target ?? ""}`;
    if (confirmStep(armed, key, action.confirm) === "arm") {
      setArmed(key);
      window.setTimeout(() => setArmed((now) => (now === key ? null : now)), 4000);
      return;
    }
    setArmed(null);
    onAction(action, target);
  };
  const q = query.trim().toLowerCase();
  const sections = useMemo(
    () => filterSections(panel.sections ?? [], q),
    [panel.sections, q],
  );
  const total = (panel.sections ?? []).reduce((n, s) => n + s.fields.length, 0);
  const shown = sections.reduce((n, s) => n + s.fields.length, 0);
  const dirtyCount = Object.keys(draft).length;
  // Short forms (an edit form) open flat; the long config list starts collapsed.
  const small = (panel.sections?.length ?? 0) <= 2;
  // An action form (a context, one section: Halt Network) is two fields, not a settings list: no search, no section header.
  const flat = Boolean(panel.context) && panel.sections?.length === 1;
  const actions = panel.actions ?? [];
  const paths = panel.save_targets ?? {};

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-3 sm:px-6">
        {/* Where the dialog sits on a fractional pixel, the scroller's clip edge and this box's edge snap a device
            row apart and a row of scrolled settings showed above the search; the 1px shadow in the box's own
            colour covers that row. */}
        <div className="bg-background sticky top-0 z-10 flex flex-col gap-1 pt-3 pb-3 shadow-[0_-1px_0_var(--color-background)]">
          <div className={flat ? "hidden" : "relative"}>
            <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              type="search"
              aria-label="Search settings"
              placeholder="Search settings"
              className="pl-8"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          {q ? (
            <p className="text-muted-foreground text-xs" aria-live="polite">
              Showing {shown} of {total} settings
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-2">
          {sections.map((section) => {
            const changed = section.fields.filter((f) => f.path in draft).length;
            const failed = section.fields.some((f) => f.path in errors);
            if (flat) {
              return (
                <div key={section.id} className="px-3">
                  {section.fields.map((field) => (
                    <FieldRenderer
                      key={field.path}
                      field={field}
                      draft={draft}
                      error={errors[field.path]}
                      disabled={busy}
                      onChange={onDraftChange}
                    />
                  ))}
                </div>
              );
            }
            return (
              <details
                key={section.id}
                open={q || failed || small || section.open ? true : undefined}
                className="group rounded-lg bg-muted/30"
              >
                <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-sm font-medium select-none [&::-webkit-details-marker]:hidden">
                  <ChevronRight className="size-4 shrink-0 transition-transform group-open:rotate-90" />
                  <span className="min-w-0 flex-1 break-words">{section.title}</span>
                  {changed ? (
                    <Badge variant="secondary">{changed} changed</Badge>
                  ) : null}
                  <span className="text-muted-foreground shrink-0 text-xs font-normal">
                    {section.fields.length}
                  </span>
                </summary>
                <div className="px-3 pb-1">
                  {section.fields.map((field) => (
                    <FieldRenderer
                      key={field.path}
                      field={field}
                      draft={draft}
                      error={errors[field.path]}
                      disabled={busy}
                      onChange={onDraftChange}
                    />
                  ))}
                </div>
              </details>
            );
          })}
          {!sections.length ? (
            <p className="text-muted-foreground rounded-lg bg-muted/30 px-3 py-6 text-center text-sm">
              No settings match your search.
            </p>
          ) : null}
        </div>
      </div>

      <footer className="bg-muted/30 flex shrink-0 flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <p className="text-muted-foreground text-xs sm:pt-2" aria-live="polite">
          {dirtyCount
            ? `${dirtyCount} unsaved change${dirtyCount === 1 ? "" : "s"}`
            : flat
              ? ""
              : "No unsaved changes"}
        </p>
        <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
          {actions.flatMap((action) =>
            (action.targets?.length ? action.targets : [null]).map((target) => (
              <div
                key={`${action.id}:${target ?? ""}`}
                className="flex min-w-0 flex-col gap-1 sm:max-w-[15rem]"
              >
                <Button
                  type="button"
                  variant={
                    armed === `${action.id}:${target ?? ""}`
                      ? "destructive"
                      : target === "global"
                        ? "outline"
                        : "default"
                  }
                  disabled={busy || (dirtyCount === 0 && !panel.context)}
                  onClick={() => fire(action, target)}
                >
                  {busy
                    ? "Saving…"
                    : armed === `${action.id}:${target ?? ""}`
                      ? `Confirm ${buttonLabel(action, target)}`
                      : buttonLabel(action, target)}
                </Button>
                {target && paths[target] ? (
                  <span
                    className="text-muted-foreground text-[0.6875rem] break-all"
                    title={paths[target] ?? undefined}
                  >
                    {paths[target]}
                  </span>
                ) : null}
              </div>
            )),
          )}
        </div>
      </footer>
    </div>
  );
}
