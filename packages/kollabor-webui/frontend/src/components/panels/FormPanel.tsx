import { useMemo, useState } from "react";
import { ChevronRight, Search } from "lucide-react";
import type { PanelAction, PanelField, PanelForm, PanelSection } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FieldRenderer, type Draft } from "./FieldRenderer";
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
  const actions = panel.actions ?? [];
  const paths = panel.save_targets ?? {};

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 sm:px-6">
        <div className="bg-background sticky top-0 z-10 flex flex-col gap-1 pb-3">
          <div className="relative">
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
            return (
              <details
                key={section.id}
                open={q || failed || small ? true : undefined}
                className="group rounded-lg border"
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
                <div className="divide-y border-t px-3">
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
            <p className="text-muted-foreground rounded-lg border border-dashed px-3 py-6 text-center text-sm">
              No settings match your search.
            </p>
          ) : null}
        </div>
      </div>

      <footer className="flex shrink-0 flex-col gap-2 border-t px-4 py-3 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <p className="text-muted-foreground text-xs sm:pt-2" aria-live="polite">
          {dirtyCount
            ? `${dirtyCount} unsaved change${dirtyCount === 1 ? "" : "s"}`
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
                  variant={target === "global" ? "outline" : "default"}
                  disabled={busy || dirtyCount === 0}
                  onClick={() => onAction(action, target)}
                >
                  {busy ? "Saving…" : buttonLabel(action, target)}
                </Button>
                {target && paths[target as "local" | "global"] ? (
                  <span
                    className="text-muted-foreground text-[11px] break-all"
                    title={paths[target as "local" | "global"] ?? undefined}
                  >
                    {paths[target as "local" | "global"]}
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
