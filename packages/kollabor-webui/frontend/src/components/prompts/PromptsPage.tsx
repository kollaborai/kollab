import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<{ rendered: string; tokens: number } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message | null>(null);

  const selectionRequest = useRef(0);
  const sectionRequest = useRef(0);
  const previewRequest = useRef(0);

  const mutationPending = useRef(false);

  const loadSection = useCallback(
    async (targetBundle: string, key: string, request: number) => {
      const sectionRequestId = ++sectionRequest.current;
      setContent(null);
      setDraft("");
      try {
        const loaded = await api.getSection(targetBundle, key);
        if (
          request !== selectionRequest.current ||
          sectionRequestId !== sectionRequest.current
        ) return;
        setContent(loaded);
        setDraft(loaded.content);
      } catch (reason) {
        if (
          request !== selectionRequest.current ||
          sectionRequestId !== sectionRequest.current
        ) return;
        setContent(null);
        setDraft("");
        setMessage({ ok: false, text: errorText(reason) });
      }
    },
    [api],
  );

  const selectBundle = useCallback(
    async (next: string) => {
      if (mutationPending.current) return;
      const request = ++selectionRequest.current;
      ++sectionRequest.current;
      ++previewRequest.current;
      setBundle(next);
      setSelected(null);
      setContent(null);
      setDraft("");
      setSections(null);
      setPreviewing(false);
      setPreview(null);
      setMessage(null);
      try {
        const result = await api.listSections(next);
        if (request !== selectionRequest.current) return;
        const ordered = sortSections(result.sections);
        setSections(ordered);
        if (ordered.length) {
          setSelected(ordered[0].key);
          void loadSection(next, ordered[0].key, request);
        }
      } catch (reason) {
        if (request === selectionRequest.current) {
          setMessage({ ok: false, text: errorText(reason) });
        }
      }
    },
    [api, loadSection],
  );

  const selectSection = useCallback(
    (key: string) => {
      if (mutationPending.current || !bundle) return;
      const request = ++selectionRequest.current;
      ++previewRequest.current;
      setSelected(key);
      setPreviewing(false);
      setPreview(null);
      setMessage(null);
      void loadSection(bundle, key, request);
    },
    [bundle, loadSection],
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

  const save = async () => {
    if (!bundle || !selected || !content || mutationPending.current) return;
    const targetBundle = bundle;
    const key = selected;
    const value = draft;
    const request = selectionRequest.current;
    mutationPending.current = true;
    setBusy(true);
    setMessage({ ok: true, text: "Saving override…" });
    try {
      try {
        await api.saveSection(targetBundle, key, value);
      } catch (reason) {
        if (request === selectionRequest.current) {
          setMessage({ ok: false, text: errorText(reason) });
        }
        return;
      }
      if (request !== selectionRequest.current) return;

      setContent((current) =>
        current ? { ...current, content: value, overridden: true } : current,
      );
      setDraft(value);
      setSections((current) =>
        current?.map((section) =>
          section.key === key ? { ...section, overridden: true } : section,
        ) ?? current,
      );
      setMessage({ ok: true, text: "Override saved." });

      try {
        const result = await api.listSections(targetBundle);
        if (request !== selectionRequest.current) return;
        setSections(sortSections(result.sections));
        const stored = await api.getSection(targetBundle, key);
        if (request !== selectionRequest.current) return;
        setContent(stored);
        setDraft(stored.content);
      } catch (reason) {
        if (request === selectionRequest.current) {
          setMessage({
            ok: false,
            text: `Override saved, but refresh failed: ${errorText(reason)}`,
          });
        }
      }
    } finally {
      mutationPending.current = false;
      setBusy(false);
    }
  };

  const removeOverride = async () => {
    const sectionKind = sections?.find((section) => section.key === selected)?.kind;
    if (
      mutationPending.current ||
      !bundle ||
      !selected ||
      !content?.overridden ||
      !isEditable(sectionKind ?? "editable")
    ) return;
    const targetBundle = bundle;
    const key = selected;
    const request = selectionRequest.current;
    mutationPending.current = true;
    setBusy(true);
    setMessage({ ok: true, text: "Removing override…" });
    try {
      try {
        await api.deleteOverride(targetBundle, key);
      } catch (reason) {
        if (request === selectionRequest.current) {
          setMessage({ ok: false, text: errorText(reason) });
        }
        return;
      }
      if (request !== selectionRequest.current) return;

      setSections((current) =>
        current?.map((section) =>
          section.key === key ? { ...section, overridden: false } : section,
        ) ?? current,
      );
      setContent(null);
      setDraft("");
      setMessage({
        ok: true,
        text: "Override removed; refreshing the shipped section.",
      });

      try {
        const result = await api.listSections(targetBundle);
        if (request !== selectionRequest.current) return;
        setSections(sortSections(result.sections));
        const stored = await api.getSection(targetBundle, key);
        if (request !== selectionRequest.current) return;
        setContent(stored);
        setDraft(stored.content);
        setMessage({
          ok: true,
          text: "Override removed; the shipped section shows through.",
        });
      } catch (reason) {
        if (request === selectionRequest.current) {
          setMessage({
            ok: false,
            text: `Override removed, but refresh failed: ${errorText(reason)}`,
          });
        }
      }
    } finally {
      mutationPending.current = false;
      setBusy(false);
    }
  };

  const togglePreview = async () => {
    if (!bundle) return;
    if (previewing) {
      ++previewRequest.current;
      setPreviewing(false);
      setPreview(null);
      return;
    }
    const request = ++previewRequest.current;
    const targetBundle = bundle;
    setPreview(null);
    setPreviewing(true);
    setMessage(null);
    try {
      const loaded = await api.preview(targetBundle);
      if (request !== previewRequest.current) return;
      setPreview(loaded);
    } catch (reason) {
      if (request !== previewRequest.current) return;
      setPreviewing(false);
      setPreview(null);
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
      content={content}
      draft={draft}
      previewing={previewing}
      preview={preview}
      busy={busy}
      message={message}
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
  content,
  draft,
  previewing,
  preview,
  busy,
  message,
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
  content: PromptSectionContent | null;
  draft: string;
  previewing: boolean;
  preview: { rendered: string; tokens: number } | null;
  busy: boolean;
  message: Message | null;
  onSelectBundle: (bundle: string) => void;
  onSelectSection: (key: string) => void;
  onDraftChange: (draft: string) => void;
  onSave: () => void;
  onDeleteOverride: () => void;
  onTogglePreview: () => void;
}) {
  const section = sections?.find((row) => row.key === selected) ?? null;
  const kind = section?.kind ?? "editable";
  const editable = isEditable(kind);
  const overridden = Boolean(content?.overridden ?? section?.overridden);
  const dirty = isDirty(draft, content?.content ?? null);
  const provenance = section ? provenanceNote(section) : null;

  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false);

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-visible"
      data-testid="prompts-page"
    >
      <aside
        aria-label="Agent bundles"
        data-testid="prompts-sidebar"
        className="max-h-40 w-full shrink-0 overflow-y-auto border-b pb-2 md:max-h-none md:w-52 md:border-r md:border-b-0"
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
                  disabled={busy}
                  aria-current={prompt.bundle === bundle ? "true" : undefined}
                  onClick={() => onSelectBundle(prompt.bundle)}
                  className={cn(
                    "hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-inset outline-none flex w-full items-center px-3 py-1.5 text-left text-sm",
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
        className="max-h-48 w-full shrink-0 overflow-y-auto border-b md:max-h-none md:w-64 md:border-r md:border-b-0"
      >
        <div className="flex items-center justify-between px-3 pt-3 pb-1">
          <p className="text-muted-foreground text-xs font-medium">
            Sections{bundle ? ` · ${bundle}` : ""}
          </p>
        </div>
        {sections === null ? (
          <p className="text-muted-foreground px-3 py-2 text-xs">
            {bundle ? "Loading sections…" : "Pick a bundle."}
          </p>
        ) : sections.length === 0 ? (
          <p className="text-muted-foreground px-3 py-2 text-xs">
            This bundle has no sections.
          </p>
        ) : (
          sections.map((row) => (
            <button
              key={row.key}
              type="button"
              data-testid="section-row"
              data-key={row.key}
              disabled={busy}
              aria-current={row.key === selected ? "true" : undefined}
              onClick={() => onSelectSection(row.key)}
              className={cn(
                "hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-inset outline-none flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm",
                row.key === selected && "bg-accent font-medium",
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
        className="flex min-h-96 min-w-0 flex-1 flex-col md:min-h-0"
      >
        <header className="flex items-center gap-2 border-b px-3 py-2">
          <h3 id="prompt-editor-title" data-testid="editor-title" className="min-w-0 truncate text-sm font-semibold">
            {section?.title ?? "No section selected"}
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
              disabled={busy}
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
            <textarea
              aria-labelledby="prompt-editor-title"
              data-testid="section-content"
              value={draft}
              readOnly={!editable || !content || busy}
              placeholder={
                sections === null
                  ? ""
                  : "Select a section."
              }
              onChange={(event) => onDraftChange(event.target.value)}
              spellCheck={false}
              className="bg-transparent p-3 font-mono text-xs outline-none placeholder:text-muted-foreground min-h-0 flex-1 resize-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset"
            />
            <footer className="flex items-center gap-2 border-t px-3 py-2">
              {editable && (
                <Button
                  type="button"
                  size="xs"
                  data-testid="save-section"
                  disabled={!dirty || !content || busy}
                  onClick={onSave}
                >
                  Save override
                </Button>
              )}
              {overridden && editable && content?.overridden && (
                <Button
                  type="button"
                  size="xs"
                  variant="destructive"
                  data-testid="delete-override"
                  disabled={busy}
                  onClick={() => setRemoveConfirmOpen(true)}
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
      <AlertDialog
        open={removeConfirmOpen}
        onOpenChange={setRemoveConfirmOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this prompt override?</AlertDialogTitle>
            <AlertDialogDescription>
              Your saved override and any unsaved edits for this section will be
              deleted. The shipped section will be used instead. This cannot be
              undone from the editor.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={onDeleteOverride}
            >
              Remove override
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
