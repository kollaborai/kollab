import { Fragment, useMemo, useState } from "react";
import { Check, Search } from "lucide-react";
import type { PanelAction, PanelField, PanelPicker, PanelRow } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FieldRenderer } from "./FieldRenderer";
import { summaryRows, titleCase } from "./panel-model";

const SEARCH_FROM_ROWS = 8;

function actionLabel(action: PanelAction): string {
  return action.label || titleCase(action.id);
}

/**
 * A grouped, searchable list with row and toolbar actions. It also draws the
 * generic extras a picker may carry: `summary` rows, a read-only `notice`,
 * `controls` (fields that act on change) and `empty_groups`.
 */
export function PickerList({
  panel,
  busy,
  onRowAction,
  onToolbarAction,
  onControlChange,
}: {
  panel: PanelPicker;
  busy: boolean;
  onRowAction: (action: PanelAction, row: PanelRow) => void;
  onToolbarAction: (action: PanelAction) => void;
  onControlChange: (field: PanelField, value: unknown) => void;
}) {
  const [query, setQuery] = useState("");
  // The row action waiting for a second click (confirm: true).
  const [armed, setArmed] = useState<string | null>(null);
  const q = query.trim().toLowerCase();
  const rows = panel.rows ?? [];
  const summary = summaryRows(panel.summary);

  const groups = useMemo(() => {
    const byGroup = new Map<string, PanelRow[]>();
    for (const row of rows) {
      const haystack = [row.label, row.detail, row.group, row.id, ...(row.badges ?? [])]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (q && !haystack.includes(q)) continue;
      const key = row.group ?? "";
      byGroup.set(key, [...(byGroup.get(key) ?? []), row]);
    }
    return [...byGroup.entries()];
  }, [rows, q]);

  const emptyGroups = q ? [] : (panel.empty_groups ?? []);
  const rowActions = panel.row_actions ?? [];
  const toolbar = panel.toolbar_actions ?? [];

  const fire = (action: PanelAction, row: PanelRow) => {
    const key = `${row.id}:${action.id}`;
    if (action.confirm && armed !== key) {
      setArmed(key);
      window.setTimeout(() => setArmed((now) => (now === key ? null : now)), 4000);
      return;
    }
    setArmed(null);
    onRowAction(action, row);
  };

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {panel.notice ? (
        <p className="bg-muted/40 rounded-md border px-3 py-2 text-sm break-words">
          {panel.notice}
        </p>
      ) : null}

      {summary.length ? (
        <dl className="bg-muted/30 grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] gap-x-3 gap-y-1.5 rounded-lg border p-3 text-sm">
          {summary.map((row, index) => (
            <Fragment key={`${row.label}-${index}`}>
              <dt className="text-muted-foreground break-words">{row.label}</dt>
              <dd className="font-medium break-words">{row.value || "—"}</dd>
            </Fragment>
          ))}
        </dl>
      ) : null}

      {panel.controls?.length ? (
        <div className="divide-y rounded-lg border px-3">
          {panel.controls.map((control) => (
            <FieldRenderer
              key={control.path}
              field={control}
              draft={{}}
              disabled={busy}
              onChange={(next) => {
                if (control.path in next) {
                  onControlChange(control, next[control.path]);
                }
              }}
            />
          ))}
        </div>
      ) : null}

      {toolbar.length || rows.length >= SEARCH_FROM_ROWS ? (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {rows.length >= SEARCH_FROM_ROWS ? (
            <div className="relative min-w-0 flex-1 basis-48">
              <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
              <Input
                type="search"
                aria-label="Search"
                placeholder="Search"
                className="pl-8"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          ) : null}
          {toolbar.map((action) => (
            <Button
              key={action.id}
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => onToolbarAction(action)}
            >
              {actionLabel(action)}
            </Button>
          ))}
        </div>
      ) : null}

      {groups.map(([group, groupRows]) => (
        <section key={group || "rows"} className="flex min-w-0 flex-col gap-2">
          {group ? (
            <h3 className="text-muted-foreground text-xs font-semibold tracking-wide capitalize">
              {group}
            </h3>
          ) : null}
          <ul className="flex min-w-0 flex-col gap-2">
            {groupRows.map((row) => (
              <li
                key={row.id}
                data-current={row.current ? "true" : undefined}
                className="flex min-w-0 flex-col gap-2 rounded-lg border p-3 data-[current=true]:border-primary/50 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                    <span className="min-w-0 text-sm font-medium break-words">
                      {row.label}
                    </span>
                    {row.current ? (
                      <Badge>
                        <Check />
                        Current
                      </Badge>
                    ) : null}
                    {(row.badges ?? []).map((badge) => (
                      <Badge key={badge} variant="secondary" className="capitalize">
                        {badge}
                      </Badge>
                    ))}
                  </div>
                  {row.detail ? (
                    <span className="text-muted-foreground font-mono text-xs break-words">
                      {row.detail}
                    </span>
                  ) : null}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {rowActions
                    .filter((action) => !row.actions || row.actions.includes(action.id))
                    .map((action) => {
                      const isArmed = armed === `${row.id}:${action.id}`;
                      return (
                        <Button
                          key={action.id}
                          type="button"
                          size="xs"
                          variant={isArmed ? "destructive" : "outline"}
                          disabled={busy}
                          aria-label={`${actionLabel(action)} ${row.label}`}
                          onClick={() => fire(action, row)}
                        >
                          {isArmed ? "Confirm" : actionLabel(action)}
                        </Button>
                      );
                    })}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {emptyGroups.map((entry) => (
        <section key={`empty-${entry.group}`} className="flex flex-col gap-1">
          <h3 className="text-muted-foreground text-xs font-semibold tracking-wide capitalize">
            {entry.group}
          </h3>
          <p className="text-muted-foreground rounded-lg border border-dashed px-3 py-2 text-sm break-words">
            {entry.reason}
          </p>
        </section>
      ))}

      {!groups.length && !emptyGroups.length ? (
        <p className="text-muted-foreground rounded-lg border border-dashed px-3 py-6 text-center text-sm">
          {q ? "Nothing matches your search." : "Nothing to show here yet."}
        </p>
      ) : null}
    </div>
  );
}
