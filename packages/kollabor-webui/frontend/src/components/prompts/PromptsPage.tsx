import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Library,
  Lock,
  PencilLine,
  Plus,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { errorText } from "@/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatTokens } from "@/components/trajectory/trajectory-format";
import type {
  Composition,
  PromptLibrary,
  PromptSection,
  PromptsApi,
} from "@/prompts-api";
import {
  ALL_VARIANT,
  DEFAULTS_LIBRARY,
  diffLines,
  includedTotals,
  isDirty,
  libraryGroups,
  libraryLabel,
  roleGroups,
  toggleRow,
  variantLabel,
  variantNames,
  variantOf,
  type PromptTarget,
  type SectionGroup,
  type SectionRow,
} from "./prompts-model";

type Message = { ok: boolean; text: string };
type EditorTab = "edit" | "changes" | "full";

/** Where a row's text is read and saved, and what Save means for it. */
type Source =
  | { kind: "role"; role: string; key: string } // the role's own file, or its copy of a defaults section
  | { kind: "library"; library: string; key: string }
  | { kind: "per-turn"; key: string };

function sourceFor(target: PromptTarget, row: SectionRow): Source {
  if (row.origin === "per-turn") return { kind: "per-turn", key: row.key };
  if (target.kind === "role" && (row.origin === "role" || row.library === DEFAULTS_LIBRARY)) {
    return { kind: "role", role: target.name, key: row.key };
  }
  return { kind: "library", library: row.library ?? DEFAULTS_LIBRARY, key: row.key };
}

const tok = (n: number) => `${formatTokens(n)} tok`;

/**
 * The System Prompts settings page. Libraries hold shared sections ("Defaults"
 * ships with Kollab); a role ticks the sections it sends, per model variant,
 * and keeps its own. The server owns every fact (tokens, used-by counts,
 * paths); this page orchestrates the calls in `PromptsApi`.
 */
export function PromptsPage({ api }: { api: PromptsApi }) {
  const [libraries, setLibraries] = useState<PromptLibrary[] | null>(null);
  const [roles, setRoles] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [target, setTarget] = useState<PromptTarget>({ kind: "role", name: "default" });
  const [roleSections, setRoleSections] = useState<PromptSection[]>([]);
  const [composition, setComposition] = useState<Composition | null>(null);
  const [variant, setVariant] = useState(ALL_VARIANT);
  const [selected, setSelected] = useState<SectionRow | null>(null);
  const [stored, setStored] = useState<{ content: string; changed: boolean } | null>(null);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<EditorTab>("edit");
  const [preview, setPreview] = useState<{ rendered: string; tokens: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const request = useRef(0);

  const refreshLibraries = useCallback(async () => {
    const result = await api.listLibraries();
    setLibraries(result.libraries);
    setRoles(result.roles);
  }, [api]);

  useEffect(() => {
    refreshLibraries().catch((reason) => setLoadError(errorText(reason)));
  }, [refreshLibraries]);

  // A role's own sections, overrides and picks.
  useEffect(() => {
    if (target.kind !== "role") return;
    let live = true;
    setRoleSections([]);
    setComposition(null);
    Promise.all([api.listSections(target.name), api.getComposition(target.name)])
      .then(([sections, picks]) => {
        if (!live) return;
        setRoleSections(sections.sections);
        setComposition(picks);
      })
      .catch((reason) => live && setMessage({ ok: false, text: errorText(reason) }));
    return () => {
      live = false;
    };
  }, [api, target]);

  const groups = useMemo<SectionGroup[]>(() => {
    if (!libraries) return [];
    if (target.kind === "library") {
      const library = libraries.find((l) => l.name === target.name);
      return library ? libraryGroups(library) : [];
    }
    return roleGroups(roleSections, libraries, variantOf(composition, variant));
  }, [libraries, target, roleSections, composition, variant]);

  const loadRow = useCallback(
    async (row: SectionRow) => {
      const id = ++request.current;
      setSelected(row);
      setStored(null);
      setDraft("");
      setTab("edit");
      setPreview(null);
      setMessage(null);
      setEditorOpen(true);
      const source = sourceFor(target, row);
      try {
        const loaded =
          source.kind === "role"
            ? await api.getSection(source.role, source.key).then((s) => ({ content: s.content, changed: s.overridden }))
            : await api
                .getLibrarySection(source.kind === "library" ? source.library : DEFAULTS_LIBRARY, source.key)
                .then((s) => ({ content: s.content, changed: s.edited }));
        if (id !== request.current) return;
        setStored(loaded);
        setDraft(loaded.content);
      } catch (reason) {
        if (id === request.current) setMessage({ ok: false, text: errorText(reason) });
      }
    },
    [api, target],
  );

  const openTarget = (next: PromptTarget) => {
    if (busy) return;
    ++request.current;
    setTarget(next);
    setVariant(ALL_VARIANT);
    setSelected(null);
    setStored(null);
    setDraft("");
    setMessage(null);
    setEditorOpen(false);
  };

  const run = async (label: string, work: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setMessage({ ok: true, text: label });
    try {
      await work();
    } catch (reason) {
      setMessage({ ok: false, text: errorText(reason) });
    } finally {
      setBusy(false);
    }
  };

  const toggle = (row: SectionRow, include: boolean) =>
    run(include ? `Adding ${row.title}…` : `Removing ${row.title}…`, async () => {
      if (target.kind !== "role") return;
      const next = toggleRow(composition, variant, row, include);
      setComposition(next);
      setComposition(await api.saveComposition(target.name, next));
      await refreshLibraries();
      setMessage(null);
    });

  const save = () =>
    selected &&
    run("Saving…", async () => {
      const source = sourceFor(target, selected);
      if (source.kind === "per-turn") return;
      if (source.kind === "role") await api.saveSection(source.role, source.key, draft);
      else await api.saveLibrarySection(source.library, source.key, draft);
      setStored({ content: draft, changed: true });
      if (target.kind === "role") setRoleSections((await api.listSections(target.name)).sections);
      await refreshLibraries();
      setMessage({ ok: true, text: "Saved." });
    });

  const reset = () =>
    selected &&
    run("Resetting…", async () => {
      const source = sourceFor(target, selected);
      if (source.kind === "role") await api.deleteOverride(source.role, source.key);
      else if (source.kind === "library") await api.deleteLibrarySection(source.library, source.key);
      if (target.kind === "role") setRoleSections((await api.listSections(target.name)).sections);
      await refreshLibraries();
      if (source.kind === "library" && source.library !== DEFAULTS_LIBRARY) {
        setSelected(null);
        setEditorOpen(false);
        setMessage({ ok: true, text: "Section deleted." });
        return;
      }
      await loadRow(selected);
    });

  const showTab = async (next: EditorTab) => {
    setTab(next);
    if (next !== "full" || target.kind !== "role") return;
    const id = ++request.current;
    setPreview(null);
    try {
      const loaded = await api.preview(target.name, variant);
      if (id === request.current) setPreview(loaded);
    } catch (reason) {
      if (id === request.current) setMessage({ ok: false, text: errorText(reason) });
    }
  };

  const addVariant = (name: string, providers: string[]) =>
    run("Adding variant…", async () => {
      if (target.kind !== "role") return;
      const base = variantOf(composition, ALL_VARIANT);
      const next: Composition = {
        variants: { ...(composition?.variants ?? {}), [name]: { exclude: [...base.exclude], add: [...base.add], providers } },
      };
      setComposition(await api.saveComposition(target.name, next));
      setVariant(name);
      setMessage(null);
    });

  const addLibrary = (name: string) =>
    run("Creating library…", async () => {
      await api.createLibrary(name);
      await refreshLibraries();
      openTarget({ kind: "library", name });
    });

  const addSection = (key: string) =>
    run("Adding section…", async () => {
      if (target.kind !== "library") return;
      await api.saveLibrarySection(target.name, key, `# ${key}\n`);
      await refreshLibraries();
      setMessage(null);
    });

  return (
    <PromptsView
      libraries={libraries}
      roles={roles}
      loadError={loadError}
      target={target}
      groups={groups}
      composition={composition}
      variant={variant}
      selected={selected}
      stored={stored}
      draft={draft}
      tab={tab}
      preview={preview}
      busy={busy}
      message={message}
      editorOpen={editorOpen}
      onOpenTarget={openTarget}
      onSelectRow={(row) => void loadRow(row)}
      onToggleRow={(row, include) => void toggle(row, include)}
      onVariant={(name) => {
        setVariant(name);
        setPreview(null);
        if (tab === "full") setTab("edit");
      }}
      onAddVariant={(name, providers) => void addVariant(name, providers)}
      onAddLibrary={(name) => void addLibrary(name)}
      onAddSection={(key) => void addSection(key)}
      onDraftChange={setDraft}
      onTab={(next) => void showTab(next)}
      onSave={() => void save()}
      onReset={() => void reset()}
      onEditInLibrary={() => {
        if (!selected?.library) return;
        const row = selected;
        openTarget({ kind: "library", name: row.library ?? DEFAULTS_LIBRARY });
        setTimeout(() => void loadRow({ ...row, origin: "library" }), 0);
      }}
      onCloseEditor={() => setEditorOpen(false)}
    />
  );
}

const SLUG = /^[a-z0-9][a-z0-9_-]*$/;

/** One-line "name it" form used for new libraries, sections and variants. */
function InlineCreate({
  label,
  placeholder,
  onCreate,
  busy,
  withProviders = false,
}: {
  label: string;
  placeholder: string;
  onCreate: (name: string, providers: string[]) => void;
  busy: boolean;
  withProviders?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [providers, setProviders] = useState("");
  const valid = SLUG.test(name);
  if (!open) {
    return (
      <Button type="button" variant="ghost" size="xs" aria-label={label} onClick={() => setOpen(true)} disabled={busy}>
        <Plus />
        {withProviders ? "Variant" : null}
      </Button>
    );
  }
  return (
    <form
      className="bg-muted/60 flex w-full flex-col gap-2 rounded-lg p-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid) return;
        onCreate(
          name,
          providers.split(",").map((p) => p.trim()).filter(Boolean),
        );
        setOpen(false);
        setName("");
        setProviders("");
      }}
    >
      <label className="text-muted-foreground flex flex-col gap-1 text-xs font-medium">
        {label}
        <input
          autoFocus
          value={name}
          onChange={(event) => setName(event.target.value.toLowerCase())}
          placeholder={placeholder}
          className="bg-background text-foreground h-8 rounded-md px-2 text-sm outline-none"
        />
      </label>
      {withProviders && (
        <label className="text-muted-foreground flex flex-col gap-1 text-xs font-medium">
          Providers that use it
          <input
            value={providers}
            onChange={(event) => setProviders(event.target.value)}
            placeholder="ollama, lmstudio"
            className="bg-background text-foreground h-8 rounded-md px-2 text-sm outline-none"
          />
        </label>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="xs" disabled={!valid || busy}>
          Create
        </Button>
        <Button type="button" size="xs" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function UnderlineTab({
  active,
  onClick,
  children,
  ...rest
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "h-9 shrink-0 border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
        active ? "border-highlight text-foreground" : "text-muted-foreground hover:text-foreground/80 border-transparent",
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

/** All panels, presentational. Exported so tests can render it with fixtures. */
export function PromptsView(props: {
  libraries: PromptLibrary[] | null;
  roles: string[];
  loadError: string | null;
  target: PromptTarget;
  groups: SectionGroup[];
  composition: Composition | null;
  variant: string;
  selected: SectionRow | null;
  stored: { content: string; changed: boolean } | null;
  draft: string;
  tab: EditorTab;
  preview: { rendered: string; tokens: number } | null;
  busy: boolean;
  message: Message | null;
  editorOpen: boolean;
  onOpenTarget: (target: PromptTarget) => void;
  onSelectRow: (row: SectionRow) => void;
  onToggleRow: (row: SectionRow, include: boolean) => void;
  onVariant: (name: string) => void;
  onAddVariant: (name: string, providers: string[]) => void;
  onAddLibrary: (name: string) => void;
  onAddSection: (key: string) => void;
  onDraftChange: (draft: string) => void;
  onTab: (tab: EditorTab) => void;
  onSave: () => void;
  onReset: () => void;
  onEditInLibrary: () => void;
  onCloseEditor: () => void;
}) {
  const { libraries, roles, target, groups, selected, busy } = props;
  const [filter, setFilter] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const isRole = target.kind === "role";
  const library = !isRole ? libraries?.find((l) => l.name === target.name) : undefined;
  const totals = includedTotals(groups);
  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? groups
        .map((g) => ({ ...g, rows: g.rows.filter((r) => r.title.toLowerCase().includes(needle)) }))
        .filter((g) => g.rows.length)
    : groups;

  const targetValue = `${target.kind}:${target.name}`;
  const picker = (
    <label className="text-muted-foreground flex flex-col gap-1.5 text-xs font-medium lg:hidden">
      Library or Role
      <select
        data-testid="prompts-target-select"
        value={targetValue}
        disabled={busy}
        onChange={(event) => {
          const [kind, ...name] = event.target.value.split(":");
          props.onOpenTarget({ kind: kind as PromptTarget["kind"], name: name.join(":") });
        }}
        className="bg-muted text-foreground h-11 rounded-lg px-3 text-base"
      >
        <optgroup label="Libraries">
          {libraries?.map((l) => (
            <option key={l.name} value={`library:${l.name}`}>
              {libraryLabel(l.name)}
            </option>
          ))}
        </optgroup>
        <optgroup label="Roles">
          {roles.map((r) => (
            <option key={r} value={`role:${r}`}>
              {r}
            </option>
          ))}
        </optgroup>
      </select>
    </label>
  );

  return (
    <div className="relative flex min-h-0 flex-1 flex-col lg:flex-row" data-testid="prompts-page">
      <aside
        aria-label="Libraries and roles"
        data-testid="prompts-sidebar"
        className="bg-sidebar hidden w-52 shrink-0 overflow-y-auto px-2 py-3 lg:block"
      >
        {props.loadError ? (
          <p role="alert" className="text-destructive px-3 py-2 text-xs break-words">
            {props.loadError}
          </p>
        ) : libraries === null ? (
          <p className="text-muted-foreground px-3 py-2 text-xs">Loading…</p>
        ) : (
          <>
            <div className="flex items-center justify-between px-3 pt-1 pb-1">
              <span className="text-muted-foreground text-xs font-medium">Libraries</span>
              <InlineCreate label="New library" placeholder="local-lite" busy={busy} onCreate={props.onAddLibrary} />
            </div>
            {libraries.map((l) => {
              const current = !isRole && target.name === l.name;
              return (
                <button
                  key={l.name}
                  type="button"
                  data-testid="library-row"
                  aria-current={current ? "true" : undefined}
                  disabled={busy}
                  onClick={() => props.onOpenTarget({ kind: "library", name: l.name })}
                  className={cn(
                    "hover:bg-accent flex w-full flex-col rounded-lg px-3 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                    current && "bg-highlight/15",
                  )}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <Library className={cn("size-3.5", current ? "text-highlight" : "text-muted-foreground")} />
                    {libraryLabel(l.name)}
                  </span>
                  <span className="text-muted-foreground pl-5.5 text-xs">
                    {l.sections.length} {l.sections.length === 1 ? "Section" : "Sections"}
                  </span>
                </button>
              );
            })}
            <p className="text-muted-foreground px-3 pt-4 pb-1 text-xs font-medium">Roles</p>
            {roles.map((r) => {
              const current = isRole && target.name === r;
              return (
                <button
                  key={r}
                  type="button"
                  data-testid="role-row"
                  aria-current={current ? "true" : undefined}
                  disabled={busy}
                  onClick={() => props.onOpenTarget({ kind: "role", name: r })}
                  className={cn(
                    "hover:bg-accent flex w-full items-center rounded-lg px-3 py-1.5 text-left text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                    current && "bg-highlight/15",
                  )}
                >
                  {r}
                </button>
              );
            })}
          </>
        )}
      </aside>

      <div
        aria-label="Prompt sections"
        data-testid="prompts-sections"
        className="flex min-h-0 w-full flex-1 flex-col lg:w-[22rem] lg:flex-none"
      >
        <div className="flex flex-col gap-2 px-4 pt-3 pb-2">
          {picker}
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="text-lg font-semibold" data-testid="target-title">
              {isRole ? target.name : libraryLabel(target.name)}
            </h3>
            <span className="text-muted-foreground text-xs whitespace-nowrap" data-testid="target-total">
              {isRole
                ? `${totals.sections} Sections · ${tok(totals.tokens)}`
                : `${library?.sections.length ?? 0} Sections`}
            </span>
          </div>
          <p className="text-muted-foreground text-xs">
            {isRole ? (
              <>
                Pick the sections this role sends. Runs as any persona:{" "}
                <code className="font-mono">kollab --agent {target.name} --as lapis</code>
              </>
            ) : target.name === DEFAULTS_LIBRARY ? (
              "Ships with Kollab. An edit here reaches every role that sends the section."
            ) : (
              "Your sections. Roles add them from their checklist."
            )}
          </p>
          {isRole && (
            <div role="tablist" aria-label="Model variant" className="flex flex-wrap items-center gap-1">
              {variantNames(props.composition).map((name) => (
                <UnderlineTab key={name} active={props.variant === name} onClick={() => props.onVariant(name)}>
                  {variantLabel(name)}
                </UnderlineTab>
              ))}
              <InlineCreate label="New variant" placeholder="local" busy={busy} withProviders onCreate={props.onAddVariant} />
            </div>
          )}
          {!isRole && target.name !== DEFAULTS_LIBRARY && (
            <InlineCreate label="New section" placeholder="tool-workflow" busy={busy} onCreate={(key) => props.onAddSection(key)} />
          )}
          <label className="bg-muted text-muted-foreground flex h-9 items-center gap-2 rounded-lg px-3">
            <Search className="size-4" />
            <input
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter Sections"
              aria-label="Filter sections"
              className="text-foreground min-w-0 flex-1 bg-transparent text-base outline-none md:text-sm"
            />
          </label>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
          {shown.map((group) => {
            const open = !closed.has(group.id) || Boolean(needle);
            const checkable = group.rows.filter((r) => r.checkable);
            const meta = checkable.length
              ? `${checkable.filter((r) => r.included).length} of ${checkable.length}`
              : `${group.rows.length}`;
            return (
              <div key={group.id} className="mt-2" data-testid="section-group">
                <button
                  type="button"
                  aria-expanded={open}
                  onClick={() =>
                    setClosed((current) => {
                      const next = new Set(current);
                      if (next.has(group.id)) next.delete(group.id);
                      else next.add(group.id);
                      return next;
                    })
                  }
                  className="text-muted-foreground hover:text-foreground flex min-h-9 w-full items-center gap-2 px-2 text-left text-xs font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                >
                  <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
                  <span className={cn("flex-1", group.id === "role" && "text-highlight")}>
                    {group.library && group.library !== DEFAULTS_LIBRARY ? `${libraryLabel(group.library)} · ` : ""}
                    {group.name}
                  </span>
                  <span>{group.id === "per-turn" ? "Always sent" : meta}</span>
                </button>
                {open &&
                  group.rows.map((row) => {
                    const current = selected?.id === row.id;
                    return (
                      <div
                        key={row.id}
                        data-testid="section-row"
                        data-key={row.key}
                        className={cn(
                          "flex min-h-11 items-center gap-1 rounded-lg pr-1 lg:min-h-9",
                          current && "bg-highlight/15",
                        )}
                      >
                        {isRole && (
                          <label className="flex size-11 shrink-0 items-center justify-center lg:size-9">
                            {row.checkable || row.origin === "per-turn" ? (
                              <input
                                type="checkbox"
                                data-testid="section-include"
                                checked={row.included}
                                disabled={!row.checkable || busy}
                                onChange={(event) => props.onToggleRow(row, event.target.checked)}
                                aria-label={`Include ${row.title}`}
                                className="size-4"
                                style={{ accentColor: "var(--highlight)" }}
                              />
                            ) : null}
                          </label>
                        )}
                        <button
                          type="button"
                          onClick={() => props.onSelectRow(row)}
                          aria-current={current ? "true" : undefined}
                          className={cn(
                            "flex min-h-11 min-w-0 flex-1 items-center gap-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60 lg:min-h-9",
                            !isRole && "pl-3",
                            row.included ? "text-foreground" : "text-muted-foreground",
                          )}
                        >
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{row.title}</span>
                          {row.origin === "per-turn" && (
                            <span className="text-muted-foreground flex items-center gap-1 text-xs font-medium">
                              <Lock className="size-3" />
                              Per-Turn
                            </span>
                          )}
                          {(row.overridden || row.edited) && (
                            <span className="text-highlight flex items-center gap-1 text-xs font-medium" data-testid="section-edited">
                              <PencilLine className="size-3" />
                              {row.overridden ? "Copy" : "Edited"}
                            </span>
                          )}
                          {!isRole && row.usedBy !== null && (
                            <span className="text-muted-foreground text-xs">Used by {row.usedBy}</span>
                          )}
                          <span className="text-muted-foreground font-mono text-xs whitespace-nowrap">{tok(row.tokens)}</span>
                        </button>
                      </div>
                    );
                  })}
              </div>
            );
          })}
        </div>
      </div>

      <SectionEditor {...props} />
    </div>
  );
}

function SectionEditor(props: Parameters<typeof PromptsView>[0]) {
  const { target, selected, stored, draft, tab, busy, message } = props;
  const [confirmReset, setConfirmReset] = useState(false);
  const isRole = target.kind === "role";
  const perTurn = selected?.origin === "per-turn";
  const shared = selected?.library ?? null;
  const dirty = isDirty(draft, stored?.content ?? null);

  let status = message?.text ?? "";
  let saveLabel = "Save";
  let resetLabel: string | null = null;
  if (selected && !message) {
    if (perTurn) status = "Read-only: rendered each turn and sent ahead of your newest message.";
    else if (isRole && selected.origin === "role") {
      status = dirty ? `Saves to ${target.name} only.` : `This role only.`;
    } else if (isRole && shared === DEFAULTS_LIBRARY) {
      saveLabel = `Save for ${target.name}`;
      status = stored?.changed
        ? `${target.name} uses its own copy. Reset to go back to the library text.`
        : `From Defaults. Saving makes a copy for ${target.name} only.`;
    } else if (shared === DEFAULTS_LIBRARY) {
      saveLabel = "Save to Library";
      status = `Shared. Reaches the ${selected.usedBy ?? 0} roles that send it.`;
    } else if (shared) {
      saveLabel = "Save to Library";
      status = `In ${libraryLabel(shared)}. Used by ${selected.usedBy ?? 0} roles.`;
    }
  }
  if (selected && stored?.changed) {
    if (isRole && (selected.origin === "role" || shared === DEFAULTS_LIBRARY)) resetLabel = isRole && shared ? "Reset to Library" : "Reset";
    else if (shared === DEFAULTS_LIBRARY) resetLabel = "Revert to Shipped";
  }
  if (selected && !isRole && shared && shared !== DEFAULTS_LIBRARY) resetLabel = "Delete Section";

  const tabs: [EditorTab, string][] = perTurn
    ? [["edit", "Text"]]
    : isRole
      ? [["edit", "Edit"], ["changes", "Changes"], ["full", "Full Prompt"]]
      : [["edit", "Edit"], ["changes", "Changes"]];

  return (
    <section
      aria-label="Section editor"
      data-testid="prompts-editor"
      className={cn(
        "bg-card flex min-w-0 flex-col gap-3 px-4 pt-3 pb-4 lg:static lg:flex lg:flex-1 lg:px-6",
        props.editorOpen ? "absolute inset-0 z-10 md:left-auto md:w-[36rem]" : "hidden lg:flex",
      )}
    >
      {props.editorOpen && <div aria-hidden className="fixed inset-0 -z-10 hidden bg-black/50 md:block lg:hidden" />}
      <header className="flex items-start gap-2">
        <Button type="button" variant="ghost" size="icon" aria-label="Back to sections" className="md:hidden" onClick={props.onCloseEditor}>
          <ChevronLeft />
        </Button>
        <div className="min-w-0 flex-1">
          <h3 id="prompt-editor-title" data-testid="editor-title" className="truncate text-lg font-semibold">
            {selected?.title ?? "No section selected"}
          </h3>
          {selected && (
            <p className={cn("text-xs font-medium", shared ? "text-highlight" : "text-muted-foreground")}>
              {shared ? `${libraryLabel(shared)} · Used by ${selected.usedBy ?? 0} Roles` : perTurn ? "Per-Turn Context" : `${target.name} only`}
              {" · "}
              {tok(selected.tokens)}
            </p>
          )}
        </div>
        <Button type="button" variant="ghost" size="icon" aria-label="Close editor" className="hidden md:inline-flex lg:hidden" onClick={props.onCloseEditor}>
          <X />
        </Button>
      </header>

      {selected && (
        <div role="tablist" aria-label="Section view" className="flex gap-1">
          {tabs.map(([id, label]) => (
            <UnderlineTab key={id} active={tab === id} onClick={() => props.onTab(id)}>
              {label}
            </UnderlineTab>
          ))}
        </div>
      )}

      {tab === "changes" ? (
        <div data-testid="section-diff" className="bg-background min-h-0 flex-1 overflow-auto rounded-lg py-3 font-mono text-xs leading-5">
          {!dirty ? (
            <p className="text-muted-foreground px-4">No unsaved changes.</p>
          ) : (
            diffLines(stored?.content ?? "", draft).map((line, i) => (
              <div
                key={i}
                className={cn(
                  "flex gap-3 px-4 whitespace-pre-wrap",
                  line.mark === "+" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
                  line.mark === "-" && "bg-destructive/10 text-destructive",
                  line.mark === " " && "text-muted-foreground",
                )}
              >
                <span className="w-3 shrink-0">{line.mark === "-" ? "−" : line.mark}</span>
                <span>{line.text}</span>
              </div>
            ))
          )}
        </div>
      ) : tab === "full" ? (
        <pre data-testid="preview-rendered" className="bg-background min-h-0 flex-1 overflow-auto rounded-lg p-4 font-mono text-xs leading-5 whitespace-pre-wrap">
          {props.preview
            ? `${props.preview.rendered}\n\n— ${formatTokens(props.preview.tokens)} tokens · ${variantLabel(props.variant)} —`
            : "Loading the full prompt…"}
        </pre>
      ) : (
        <textarea
          aria-labelledby="prompt-editor-title"
          data-testid="section-content"
          value={draft}
          readOnly={!selected || perTurn || !stored || busy}
          placeholder={selected ? "" : "Pick a section."}
          onChange={(event) => props.onDraftChange(event.target.value)}
          spellCheck={false}
          className={cn(
            "bg-background placeholder:text-muted-foreground min-h-0 flex-1 resize-none rounded-lg p-4 font-mono text-base leading-6 outline-none focus-visible:ring-2 focus-visible:ring-ring/50 md:text-xs md:leading-5",
            perTurn && "text-muted-foreground",
          )}
        />
      )}

      <footer className="flex flex-wrap items-center justify-between gap-3">
        <p
          data-testid="status-message"
          role="status"
          className={cn("min-w-0 text-xs break-words", message && !message.ok ? "text-destructive" : "text-muted-foreground")}
        >
          {status}
        </p>
        {selected && !perTurn && (
          <div className="flex shrink-0 flex-wrap gap-2">
            {resetLabel && (
              <Button type="button" variant="secondary" size="sm" data-testid="reset-section" disabled={busy} onClick={() => setConfirmReset(true)}>
                <RotateCcw />
                {resetLabel}
              </Button>
            )}
            {isRole && shared && (
              <Button type="button" variant="secondary" size="sm" data-testid="edit-in-library" disabled={busy} onClick={props.onEditInLibrary}>
                Edit in Library
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              data-testid="save-section"
              disabled={!dirty || !stored || busy}
              onClick={props.onSave}
              className="bg-highlight text-highlight-foreground hover:bg-highlight/90"
            >
              {saveLabel}
            </Button>
          </div>
        )}
      </footer>
      <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{resetLabel}?</AlertDialogTitle>
            <AlertDialogDescription>
              {resetLabel === "Delete Section"
                ? "This section and any unsaved edits are deleted, and roles that send it stop getting it."
                : "Your saved edit and any unsaved changes to this section are discarded."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" data-testid="confirm-reset" onClick={props.onReset}>
              {resetLabel}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
