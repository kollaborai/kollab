import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus } from "lucide-react";
import { errorText } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatTokens } from "@/components/trajectory/trajectory-format";
import type {
  PromptEntry,
  PromptSection,
  PromptSectionContent,
  PromptsApi,
} from "@/prompts-api";
import {
  groupPrompts,
  isDirty,
  isEditable,
  kindClass,
  provenanceNote,
  sectionKeyFromTitle,
  sortSections,
  type PromptGroup,
} from "./prompts-model";

type Message = { ok: boolean; text: string };

/**
 * The System Prompts settings page: a trajectory-style split view with the
 * bundle sidebar, the selected bundle's sections, and the section editor.
 * The server owns every fact (tokens, kinds, override paths); this page only
 * orchestrates the calls in `PromptsApi` and renders what comes back.
 */
export function PromptsPage({ api }: { api: PromptsApi }) {
  const [prompts, setPrompts] = useState<PromptEntry[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [bundle, setBundle] = useState<string | null>(null);
  const [sections, setSections] = useState<PromptSection[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<PromptSectionContent | null>(null);
  const [draft, setDraft] = useState("");
  const [creating, setCreating] = useState<{ title: string; key: string } | null>(
    null,
  );
  const [creatingOpen, setCreatingOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<{ rendered: string; tokens: number } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message | null>(null);

  const selectBundle = useCallback(
    async (next: string) => {
      setBundle(next);
      setSelected(null);
      setContent(null);
      setSections(null);
      setCreating(null);
      setCreatingOpen(false);
      setPreviewing(false);
      setMessage(null);
      try {
        const result = await api.listSections(next);
        const ordered = sortSections(result.sections);
        setSections(ordered);
        if (ordered.length) setSelected(ordered[0].key);
      } catch (reason) {
        setMessage({ ok: false, text: errorText(reason) });
      }
    },
    [api],
  );

  const selectSection = useCallback(
    async (key: string) => {
      setSelected(key);
      setCreating(null);
      setPreviewing(false);
      setMessage(null);
      if (!bundle) return;
      try {
        const loaded = await api.getSection(bundle, key);
        setContent(loaded);
        setDraft(loaded.content);
      } catch (reason) {
        setContent(null);
        setDraft("");
        setMessage({ ok: false, text: errorText(reason) });
      }
    },
    [api, bundle],
  );

  useEffect(() => {
    let live = true;
    api
      .listPrompts()
      .then((result) => {
        if (!live) return;
        setPrompts(result.prompts);
        const first = groupPrompts(result.prompts)[0]?.prompts[0];
        if (first) void selectBundle(first.bundle);
      })
      .catch((reason) => {
        if (live) setLoadError(errorText(reason));
      });
    return () => {
      live = false;
    };
  }, [api, selectBundle]);

  // The editor tracks the selected section's stored content.
  useEffect(() => {
    if (!bundle || !selected || creating) return;
    let live = true;
    api
      .getSection(bundle, selected)
      .then((loaded) => {
        if (!live) return;
        setContent(loaded);
        setDraft(loaded.content);
      })
      .catch(() => {
        // Row stays selected; the message from the rows load still shows.
      });
    return () => {
      live = false;
    };
  }, [api, bundle, selected, creating]);

  const beginCreate = () => {
    if (!sections || !newTitle.trim()) return;
    const title = newTitle.trim();
    const key = sectionKeyFromTitle(
      title,
      sections.map((section) => section.key),
    );
    setCreating({ title, key });
    setSelected(key);
    setContent(null);
    setDraft("");
    setCreatingOpen(false);
    setMessage(null);
  };

  const save = async () => {
    if (!bundle || (!selected && !creating)) return;
    const key = creating ? creating.key : selected;
    if (!key) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.saveSection(bundle, key, draft);
      const result = await api.listSections(bundle);
      setSections(sortSections(result.sections));
      try {
        const stored = await api.getSection(bundle, key);
        setContent(stored);
        setDraft(stored.content);
      } catch {
        // A brand-new section the server has not listed yet: keep the draft.
        setContent({
          content: draft,
          kind: "editable",
          source_path: "",
          overridden: true,
          override_path: null,
        });
      }
      setCreating(null);
      setSelected(key);
      setMessage({
        ok: true,
        text: creating
          ? `Section "${key}" saved as this bundle's override.`
          : "Override saved.",
      });
    } catch (reason) {
      setMessage({ ok: false, text: errorText(reason) });
    } finally {
      setBusy(false);
    }
  };

  const removeOverride = async () => {
    if (!bundle || !selected) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.deleteOverride(bundle, selected);
      const result = await api.listSections(bundle);
      setSections(sortSections(result.sections));
      const stored = await api.getSection(bundle, selected);
      setContent(stored);
      setDraft(stored.content);
      setMessage({
        ok: true,
        text: "Override removed; the shipped section shows through.",
      });
    } catch (reason) {
      setMessage({ ok: false, text: errorText(reason) });
    } finally {
      setBusy(false);
    }
  };

  const togglePreview = async () => {
    if (!bundle) return;
    if (previewing) {
      setPreviewing(false);
      return;
    }
    setPreviewing(true);
    try {
      setPreview(await api.preview(bundle));
    } catch (reason) {
      setMessage({ ok: false, text: errorText(reason) });
    }
  };

  const groups = useMemo(() => (prompts ? groupPrompts(prompts) : null), [prompts]);

  return (
    <PromptsView
      groups={groups}
      loadError={loadError}
      bundle={bundle}
      sections={sections}
      selected={selected}
      creating={creating}
      creatingOpen={creatingOpen}
      newTitle={newTitle}
      content={content}
      draft={draft}
      previewing={previewing}
      preview={preview}
      busy={busy}
      message={message}
      onNewTitle={setNewTitle}
      onOpenCreate={() => {
        setCreatingOpen(true);
        setNewTitle("");
      }}
      onBeginCreate={beginCreate}
      onCancelCreate={() => setCreatingOpen(false)}
      onSelectBundle={(next) => void selectBundle(next)}
      onSelectSection={(key) => void selectSection(key)}
      onDraftChange={setDraft}
      onSave={() => void save()}
      onDeleteOverride={() => void removeOverride()}
      onTogglePreview={() => void togglePreview()}
    />
  );
}

/** All panels, presentational. Exported so tests can render it with fixtures. */
export function PromptsView({
  groups,
  loadError,
  bundle,
  sections,
  selected,
  creating,
  creatingOpen,
  newTitle,
  content,
  draft,
  previewing,
  preview,
  busy,
  message,
  onNewTitle,
  onOpenCreate,
  onBeginCreate,
  onCancelCreate,
  onSelectBundle,
  onSelectSection,
  onDraftChange,
  onSave,
  onDeleteOverride,
  onTogglePreview,
}: {
  groups: PromptGroup[] | null;
  loadError: string | null;
  bundle: string | null;
  sections: PromptSection[] | null;
  selected: string | null;
  creating: { title: string; key: string } | null;
  creatingOpen: boolean;
  newTitle: string;
  content: PromptSectionContent | null;
  draft: string;
  previewing: boolean;
  preview: { rendered: string; tokens: number } | null;
  busy: boolean;
  message: Message | null;
  onNewTitle: (title: string) => void;
  onOpenCreate: () => void;
  onBeginCreate: () => void;
  onCancelCreate: () => void;
  onSelectBundle: (bundle: string) => void;
  onSelectSection: (key: string) => void;
  onDraftChange: (draft: string) => void;
  onSave: () => void;
  onDeleteOverride: () => void;
  onTogglePreview: () => void;
}) {
  const section = sections?.find((row) => row.key === selected) ?? null;
  const kind = creating ? "editable" : (content?.kind ?? section?.kind ?? "editable");
  const editable = Boolean(creating) || isEditable(kind);
  const overridden = Boolean(content?.overridden ?? section?.overridden);
  const dirty = creating ? draft.trim().length > 0 : isDirty(draft, content?.content ?? null);
  const provenance = creating ? null : (section ? provenanceNote(section) : null);

  return (
    <div className="flex min-h-0 flex-1" data-testid="prompts-page">
      <aside
        aria-label="Agent bundles"
        data-testid="prompts-sidebar"
        className="w-52 shrink-0 overflow-y-auto border-r pb-2"
      >
        {loadError ? (
          <p role="alert" className="text-destructive px-3 py-2 text-xs break-words">
            {loadError}
          </p>
        ) : groups === null ? (
          <p className="text-muted-foreground px-3 py-2 text-xs">Loading bundles…</p>
        ) : (
          groups.map((group) => (
            <div key={group.source}>
              <p className="text-muted-foreground px-3 pt-3 pb-1 text-xs font-medium">
                {group.label}
              </p>
              {group.prompts.map((prompt) => (
                <button
                  key={prompt.bundle}
                  type="button"
                  data-testid="prompt-row"
                  data-bundle={prompt.bundle}
                  aria-current={prompt.bundle === bundle ? "true" : undefined}
                  onClick={() => onSelectBundle(prompt.bundle)}
                  className={cn(
                    "hover:bg-accent flex w-full items-center px-3 py-1.5 text-left text-sm",
                    prompt.bundle === bundle && "bg-accent font-medium",
                  )}
                >
                  <span className="min-w-0 truncate">{prompt.name}</span>
                </button>
              ))}
            </div>
          ))
        )}
      </aside>

      <div
        aria-label="Prompt sections"
        data-testid="prompts-sections"
        className="w-64 shrink-0 overflow-y-auto border-r"
      >
        <div className="flex items-center justify-between px-3 pt-3 pb-1">
          <p className="text-muted-foreground text-xs font-medium">
            Sections{bundle ? ` · ${bundle}` : ""}
          </p>
          <Button
            type="button"
            size="xs"
            variant="outline"
            data-testid="create-section"
            onClick={onOpenCreate}
          >
            <Plus aria-hidden="true" />
            New
          </Button>
        </div>
        {creatingOpen && (
          <div className="flex items-center gap-1 px-3 pb-2">
            <input
              data-testid="new-section-title"
              value={newTitle}
              placeholder="Section title"
              onChange={(event) => onNewTitle(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && onBeginCreate()}
              className="border-input bg-background h-7 min-w-0 flex-1 rounded-md border px-2 text-xs"
            />
            <Button
              type="button"
              size="xs"
              data-testid="confirm-create-section"
              disabled={!newTitle.trim()}
              onClick={onBeginCreate}
            >
              Add
            </Button>
            <Button type="button" size="xs" variant="ghost" onClick={onCancelCreate}>
              Cancel
            </Button>
          </div>
        )}
        {sections === null ? (
          <p className="text-muted-foreground px-3 py-2 text-xs">
            {bundle ? "Loading sections…" : "Pick a bundle."}
          </p>
        ) : sections.length === 0 ? (
          <p className="text-muted-foreground px-3 py-2 text-xs">
            No sections yet. New creates the first override.
          </p>
        ) : (
          sections.map((row) => (
            <button
              key={row.key}
              type="button"
              data-testid="section-row"
              data-key={row.key}
              aria-current={row.key === selected && !creating ? "true" : undefined}
              onClick={() => onSelectSection(row.key)}
              className={cn(
                "hover:bg-accent flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm",
                row.key === selected && !creating && "bg-accent font-medium",
              )}
            >
              <span className="min-w-0 flex-1 truncate">{row.title}</span>
              <span
                data-testid="section-tokens"
                className="text-muted-foreground text-xs tabular-nums"
              >
                {formatTokens(row.tokens)}
              </span>
              <span
                data-testid="kind-badge"
                className={cn("shrink-0 text-xs font-medium", kindClass(row.kind))}
              >
                {row.kind}
              </span>
              {row.overridden && (
                <span
                  data-testid="overridden-badge"
                  className="text-foreground shrink-0 rounded border px-1 text-[10px] leading-4"
                >
                  override
                </span>
              )}
            </button>
          ))
        )}
      </div>

      <section
        aria-label="Section editor"
        data-testid="prompts-editor"
        className="flex min-w-0 flex-1 flex-col"
      >
        <header className="flex items-center gap-2 border-b px-3 py-2">
          <h3 data-testid="editor-title" className="min-w-0 truncate text-sm font-semibold">
            {creating ? creating.title : (section?.title ?? "No section selected")}
          </h3>
          <span
            data-testid="editor-kind"
            className={cn("shrink-0 text-xs font-medium", kindClass(kind))}
          >
            {kind}
          </span>
          {overridden && (
            <span
              data-testid="editor-overridden"
              className="text-foreground shrink-0 rounded border px-1 text-[10px] leading-4"
            >
              override
            </span>
          )}
          <span className="ml-auto flex shrink-0 gap-1">
            <Button
              type="button"
              size="xs"
              variant={previewing ? "outline" : "default"}
              onClick={onTogglePreview}
            >
              {previewing ? "Back to section" : "Preview"}
            </Button>
          </span>
        </header>
        {previewing ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <p data-testid="preview-tokens" className="text-muted-foreground text-xs">
              {formatTokens(preview?.tokens)} tokens rendered
            </p>
            <pre
              data-testid="preview-rendered"
              className="mt-2 font-mono text-xs whitespace-pre-wrap"
            >
              {preview?.rendered ?? "Loading preview…"}
            </pre>
          </div>
        ) : (
          <>
            {provenance && (
              <p
                data-testid="provenance-note"
                className="bg-muted/30 text-muted-foreground border-b px-3 py-2 text-xs break-words"
              >
                {provenance}
              </p>
            )}
            {overridden && content?.override_path && (
              <p className="text-muted-foreground border-b px-3 py-2 text-xs break-all">
                Override file: <code>{content.override_path}</code>
              </p>
            )}
            <textarea
              data-testid="section-content"
              value={draft}
              readOnly={!editable}
              placeholder={
                sections === null
                  ? ""
                  : "Select a section, or use New to start one."
              }
              onChange={(event) => onDraftChange(event.target.value)}
              spellCheck={false}
              className="bg-transparent p-3 font-mono text-xs outline-none placeholder:text-muted-foreground min-h-0 flex-1 resize-none"
            />
            <footer className="flex items-center gap-2 border-t px-3 py-2">
              {editable && (
                <Button
                  type="button"
                  size="xs"
                  data-testid="save-section"
                  disabled={!dirty || busy}
                  onClick={onSave}
                >
                  {creating ? "Save section" : "Save override"}
                </Button>
              )}
              {overridden && (
                <Button
                  type="button"
                  size="xs"
                  variant="destructive"
                  data-testid="delete-override"
                  disabled={busy}
                  onClick={onDeleteOverride}
                >
                  Remove override
                </Button>
              )}
              {message && (
                <span
                  data-testid="status-message"
                  role="status"
                  className={cn(
                    "text-xs break-words",
                    message.ok ? "text-muted-foreground" : "text-destructive",
                  )}
                >
                  {message.text}
                </span>
              )}
            </footer>
          </>
        )}
      </section>
    </div>
  );
}
