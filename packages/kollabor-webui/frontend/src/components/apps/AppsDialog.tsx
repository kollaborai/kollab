import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Search,
  X,
} from "lucide-react";
import type {
  AppRow,
  AppsPicker,
  AppsRegistryResult,
  EngineApi,
  PanelAction,
  PanelActionResult,
  PanelForm,
} from "@/api";
import { serializeDraft, type Draft } from "@/components/panels/FieldRenderer";
import { FormPanel } from "@/components/panels/FormPanel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { AppIcon } from "./AppIcon";
import { AppRowItem } from "./AppRow";

type Notice = { ok: boolean; text: string };
type RegistryHits = { rows: AppRow[]; note: string };

/** The MCP Registry is asked once typing pauses, for queries this long or longer. */
const REGISTRY_MIN_CHARS = 2;
const REGISTRY_DEBOUNCE_MS = 400;
/** A search still running after this long says the registry is slow. It can take up to 30 s. */
const REGISTRY_SLOW_MS = 5000;

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

/** For You and Featured fold after 4 rows. Kind sections fold after 6. */
const foldAfter = (sectionId: string) =>
  sectionId === "for-you" || sectionId === "featured" ? 4 : 6;

/** Search covers label, detail, publisher and tags. `q` is trimmed and lowercased. */
const matchesQuery = (row: AppRow, q: string) =>
  !q ||
  [row.label, row.detail, row.publisher, ...(row.tags ?? [])]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .includes(q);

/** PanelHost's formChanges: an action form (it has `context`) sends every editable, non-secret field. */
function formChanges(panel: PanelForm, draft: Draft): Record<string, unknown> {
  const edits = serializeDraft(draft);
  if (!panel.context) return edits;
  const values: Record<string, unknown> = {};
  for (const section of panel.sections) {
    for (const field of section.fields) {
      if (field.type === "label" || !field.editable || field.secret) continue;
      if (field.value !== null && field.value !== undefined) {
        values[field.path] = field.value;
      }
    }
  }
  return { ...values, ...edits };
}

/**
 * The Apps directory (spec docs/specs/apps-marketplace.md, "web UI"). Rows,
 * sections and the apps-install form all come from the daemon's `apps` panel.
 */
export function AppsDialog({
  api,
  sessionId,
  open,
  onOpenChange,
  onChanged,
}: {
  api: EngineApi;
  sessionId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** An action changed something other views show (MCP servers, skills). */
  onChanged?: () => void;
}) {
  const [picker, setPicker] = useState<AppsPicker | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  // Row id -> the action running on it. Other rows stay usable meanwhile.
  const [pending, setPending] = useState<Record<string, string>>({});
  // The apps-install form, open while an Add needs input.
  const [form, setForm] = useState<PanelForm | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [formBusy, setFormBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [installedOnly, setInstalledOnly] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // MCP Registry hits for the query. The registry answers in seconds, so the
  // directory's own matches show first and these arrive under them.
  const [registry, setRegistry] = useState<RegistryHits | null>(null);
  const [registryBusy, setRegistryBusy] = useState(false);
  const [registrySlow, setRegistrySlow] = useState(false);
  const [registryTick, setRegistryTick] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  // Bumped on close: answers that arrive for a closed window are dropped.
  const epoch = useRef(0);

  const load = useCallback(() => {
    const mine = epoch.current;
    setLoadError(null);
    api.getPanel(sessionId, "apps").then(
      (panel) => {
        if (mine !== epoch.current) return;
        if (panel.kind === "picker") setPicker(panel as AppsPicker);
        else setLoadError("Apps did not come back as a list.");
      },
      (error) => {
        if (mine === epoch.current) setLoadError(errorText(error));
      },
    );
  }, [api, sessionId]);

  useEffect(() => {
    if (open) {
      load();
      return;
    }
    // Closing drops everything: drafts, notices and answers still in flight.
    epoch.current += 1;
    setPicker(null);
    setLoadError(null);
    setNotice(null);
    setPending({});
    setForm(null);
    setDraft({});
    setFormErrors({});
    setFormBusy(false);
    setQuery("");
    setInstalledOnly(false);
    setExpanded({});
    setRegistry(null);
    setRegistryBusy(false);
  }, [open, load]);

  const rows = useMemo(() => picker?.rows ?? [], [picker]);
  const byId = useMemo(
    () => new Map(rows.map((row): [string, AppRow] => [row.id, row])),
    [rows],
  );
  // Same rule as the panel's "Installed" summary: added, except the skills that ship with Kollab.
  const added = useMemo(
    () => rows.filter((row) => row.state === "added" && !(row.badges ?? []).includes("Built-in")),
    [rows],
  );
  const q = query.trim().toLowerCase();
  const sections = useMemo(() => {
    if (installedOnly) {
      return [
        {
          id: "installed",
          title: "Installed",
          rows: added.filter((row) => matchesQuery(row, q)),
        },
      ];
    }
    const layout = picker?.sections?.length
      ? picker.sections
      : [{ id: "all", title: "Apps", ids: rows.map((row) => row.id) }];
    // A search lists each match once, under its kind; For You and Featured repeat rows.
    const shown = q
      ? layout.filter((section) => section.id !== "for-you" && section.id !== "featured")
      : layout;
    return shown.map((section) => ({
      id: section.id,
      title: section.title,
      rows: section.ids
        .map((id) => byId.get(id))
        .filter((row): row is AppRow => row !== undefined && matchesQuery(row, q)),
    }));
  }, [picker, rows, byId, added, installedOnly, q]);
  const visible = sections.filter((section) => section.rows.length > 0);
  const showRegistry = !installedOnly && q.length >= REGISTRY_MIN_CHARS;

  useEffect(() => {
    if (!open || form || !showRegistry) {
      setRegistry(null);
      setRegistryBusy(false);
      return;
    }
    const mine = epoch.current;
    const controller = new AbortController();
    setRegistry(null); // hits for the last query are not hits for this one
    setRegistryBusy(true);
    setRegistrySlow(false);
    const slow = window.setTimeout(() => setRegistrySlow(true), REGISTRY_DEBOUNCE_MS + REGISTRY_SLOW_MS);
    const timer = window.setTimeout(() => {
      api.getPanel(sessionId, "apps-registry", { query: q }, controller.signal).then(
        (panel) => {
          if (mine !== epoch.current || controller.signal.aborted) return;
          const hits = panel as AppsRegistryResult;
          setRegistry({ rows: hits.rows ?? [], note: hits.note ?? "" });
          setRegistryBusy(false);
        },
        (error) => {
          if (mine !== epoch.current || controller.signal.aborted) return;
          setRegistry({ rows: [], note: errorText(error) });
          setRegistryBusy(false);
        },
      );
    }, REGISTRY_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      window.clearTimeout(slow);
      controller.abort();
    };
  }, [api, sessionId, open, form, showRegistry, q, registryTick]);

  const clearPending = (id: string) =>
    setPending((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });

  const leaveForm = () => {
    setForm(null);
    setDraft({});
    setFormErrors({});
    setNotice(null);
  };

  /** One result, one notice, and the screen it names: a fresh picker or the install form. */
  const showResult = (result: PanelActionResult) => {
    setNotice(
      result.message
        ? { ok: result.ok, text: result.message }
        : result.ok
          ? null
          : { ok: false, text: "That did not work." },
    );
    if (result.panel?.kind === "picker") setPicker(result.panel as AppsPicker);
    if (result.open?.kind === "form") {
      setForm(result.open);
      setDraft({});
      setFormErrors({});
    }
    if (result.ok) onChanged?.();
    if (result.ok && !result.panel && !result.open) load();
    // An added registry server moves from the registry hits to the directory's MCP Servers.
    if (result.ok) setRegistryTick((tick) => tick + 1);
  };

  const runRowAction = async (row: AppRow, action: string) => {
    const mine = epoch.current;
    setPending((current) => ({ ...current, [row.id]: action }));
    try {
      const result = await api.panelAction(sessionId, "apps", action, { id: row.id });
      if (mine === epoch.current) showResult(result);
    } catch (error) {
      if (mine === epoch.current) setNotice({ ok: false, text: errorText(error) });
    } finally {
      if (mine === epoch.current) clearPending(row.id);
    }
  };

  // The form posts to the panel it names (`apps-install`), per deviation 7 in webui-unified-config.md.
  const submitForm = async (action: PanelAction, target: string | null) => {
    if (!form) return;
    const mine = epoch.current;
    setFormBusy(true);
    try {
      const result = await api.panelAction(sessionId, form.panel, action.id, {
        changes: formChanges(form, draft),
        ...(target ? { target } : {}),
        ...(form.context ? { context: form.context } : {}),
      });
      if (mine !== epoch.current) return;
      if (result.ok) leaveForm();
      else setFormErrors(result.errors ?? {});
      showResult(result);
    } catch (error) {
      if (mine === epoch.current) setNotice({ ok: false, text: errorText(error) });
    } finally {
      if (mine === epoch.current) setFormBusy(false);
    }
  };

  const handleOpenChange = (next: boolean) => {
    if (
      !next &&
      Object.keys(draft).length > 0 &&
      !window.confirm("Discard your unsaved changes?")
    ) {
      return;
    }
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[min(46rem,90dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(64rem,calc(100%-2rem))]"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          searchRef.current?.focus();
        }}
      >
        <header className="flex shrink-0 flex-col gap-3 border-b px-4 pt-4 pb-4 sm:px-6 sm:pt-5">
          <div className="flex min-w-0 items-center gap-3">
            <DialogTitle className="shrink-0 text-lg leading-none font-semibold">
              Apps
            </DialogTitle>
            <DialogDescription className="sr-only">
              MCP servers, CLI tools and skills for this session.
            </DialogDescription>
            <div className="ml-auto flex min-w-0 items-center gap-1">
              {picker ? (
                <button
                  type="button"
                  aria-pressed={installedOnly}
                  onClick={() => setInstalledOnly((value) => !value)}
                  className="hover:bg-accent focus-visible:ring-ring/50 flex min-w-0 items-center gap-2 rounded-full py-1 pr-1 pl-2 text-sm outline-none focus-visible:ring-[3px]"
                >
                  <span className="flex shrink-0 -space-x-2">
                    {added.slice(0, 4).map((row) => (
                      <AppIcon key={row.id} row={row} size="sm" />
                    ))}
                  </span>
                  <span className="whitespace-nowrap">{added.length} installed</span>
                  <ChevronRight className="text-muted-foreground size-4 shrink-0" />
                </button>
              ) : null}
              <DialogClose className="text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring/50 -mr-1.5 shrink-0 rounded-full p-1.5 outline-none focus-visible:ring-[3px]">
                <X className="size-4" />
                <span className="sr-only">Close</span>
              </DialogClose>
            </div>
          </div>
          {form ? null : (
            <div className="relative">
              <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2" />
              <Input
                ref={searchRef}
                type="search"
                aria-label="Search apps"
                placeholder="Search apps"
                className="h-10 rounded-full pl-10"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          )}
        </header>

        {notice ? (
          <div
            role="status"
            className={cn(
              "mx-4 mt-3 flex min-w-0 items-start gap-2 rounded-md border px-3 py-2 text-sm sm:mx-6",
              notice.ok
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "border-destructive/30 bg-destructive/10 text-destructive",
            )}
          >
            {notice.ok ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
            )}
            <span className="min-w-0 flex-1 break-words">{notice.text}</span>
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              aria-label="Dismiss"
              onClick={() => setNotice(null)}
            >
              <X />
            </Button>
          </div>
        ) : null}

        {form ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-2 px-4 pt-3 sm:px-6">
              <Button
                type="button"
                size="xs"
                variant="ghost"
                disabled={formBusy}
                onClick={leaveForm}
              >
                <ArrowLeft />
                Back
              </Button>
              <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">
                {form.title}
              </h3>
            </div>
            <FormPanel
              panel={form}
              draft={draft}
              errors={formErrors}
              busy={formBusy}
              onDraftChange={setDraft}
              onAction={(action, target) => void submitForm(action, target)}
            />
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
            {loadError ? (
              <div className="flex flex-col items-start gap-3">
                <p role="alert" className="text-destructive text-sm break-words">
                  {loadError}
                </p>
                <Button type="button" size="sm" variant="outline" onClick={load}>
                  Retry
                </Button>
              </div>
            ) : !picker ? (
              <div aria-busy="true" className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {Array.from({ length: 6 }, (_, index) => (
                  <Skeleton key={index} className="h-16 w-full rounded-xl" />
                ))}
              </div>
            ) : (
              <div className="flex flex-col gap-6">
                {visible.map((section) => {
                  const limit = foldAfter(section.id);
                  const folded =
                    !installedOnly && section.rows.length > limit && !expanded[section.id];
                  const shown = folded ? section.rows.slice(0, limit) : section.rows;
                  return (
                    <section
                      key={section.id}
                      aria-labelledby={`apps-${section.id}`}
                      className="flex flex-col gap-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <h3 id={`apps-${section.id}`} className="text-sm font-semibold">
                          {section.title}
                        </h3>
                        {!installedOnly && section.rows.length > limit ? (
                          <Button
                            type="button"
                            size="xs"
                            variant="ghost"
                            onClick={() =>
                              setExpanded((current) => ({
                                ...current,
                                [section.id]: folded,
                              }))
                            }
                          >
                            {folded ? "View all" : "Show less"}
                          </Button>
                        ) : null}
                      </div>
                      <ul className="grid grid-cols-1 items-start gap-x-6 gap-y-1 md:grid-cols-2">
                        {shown.map((row) => (
                          <li key={row.id}>
                            <AppRowItem
                              row={row}
                              pending={pending[row.id]}
                              onAction={(action) => void runRowAction(row, action)}
                            />
                          </li>
                        ))}
                      </ul>
                    </section>
                  );
                })}
                {visible.length === 0 ? (
                  <p className="text-muted-foreground rounded-lg border border-dashed px-3 py-6 text-center text-sm">
                    {showRegistry
                      ? `No apps in the directory match "${query.trim()}"`
                      : q
                        ? `No apps match "${query.trim()}"`
                        : installedOnly
                          ? "No apps installed yet."
                          : "Nothing to show here yet."}
                  </p>
                ) : null}
                {showRegistry ? (
                  <section
                    aria-labelledby="apps-registry"
                    aria-busy={registryBusy}
                    className="flex flex-col gap-2"
                  >
                    <div className="flex items-center gap-2">
                      <h3 id="apps-registry" className="text-sm font-semibold">
                        MCP Registry
                      </h3>
                      {registryBusy ? (
                        <Loader2
                          className="text-muted-foreground size-4 animate-spin"
                          aria-hidden="true"
                        />
                      ) : null}
                    </div>
                    {registry?.rows.length ? (
                      <ul className="grid grid-cols-1 items-start gap-x-6 gap-y-1 md:grid-cols-2">
                        {registry.rows.map((row) => (
                          <li key={row.id}>
                            <AppRowItem
                              row={row}
                              pending={pending[row.id]}
                              onAction={(action) => void runRowAction(row, action)}
                            />
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    <p role="status" className="text-muted-foreground text-xs break-words">
                      {registryBusy && !registry
                        ? registrySlow
                          ? "Still searching. The MCP Registry is slow right now."
                          : "Searching the MCP Registry…"
                        : registry?.note ||
                          "Servers from the official MCP Registry. Kollab has not reviewed them."}
                    </p>
                  </section>
                ) : null}
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
