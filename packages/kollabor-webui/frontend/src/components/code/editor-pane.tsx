import { editorShortcutEnabled } from "@/components/code/editor-shortcuts";
import { editorOwner, editorOwnerEpoch } from "@/components/code/workspace-tabs";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { editor } from "monaco-editor";
import { Button } from "@/components/ui/button";
import { EmptyEditorState } from "./empty-editor-state";
import { saveWorkspaceFile } from "@/components/code/file-save";
import { isDiffTab, isDirty, setBufferSaveNotice, usePane, useEditorStore } from "@/components/code/editor-store";
import { getFileAccentColor } from "./file-tree";
import { StatusBar } from "./status-bar";
import { editorThemeColors } from "./editor-theme";

type MonacoReactModule = typeof import("@monaco-editor/react");

/**
 * Monaco is bundled with the app, never loaded from the CDN. Nothing loads until a pane first shows a file.
 */
let monacoReady: Promise<MonacoReactModule> | null = null;

function loadMonaco(): Promise<MonacoReactModule> {
  // monaco-setup bundles Monaco, wires its web workers and points the react loader at that copy. It must
  // finish before the first editor mounts; the floating panel does not import it any other way.
  monacoReady ??= Promise.all([import("@monaco-editor/react"), import("@/components/code/monaco-setup")]).then(
    ([reactMonaco]) => reactMonaco,
  );
  return monacoReady;
}

const MonacoEditor = lazy(() => loadMonaco().then((mod) => ({ default: mod.default })));
const MonacoDiffEditor = lazy(() => loadMonaco().then((mod) => ({ default: mod.DiffEditor })));

function PaneSpinner() {
  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
    </div>
  );
}

function defineVoidTheme(monaco: typeof import("monaco-editor"), accent: string) {
  const style = getComputedStyle(document.documentElement);
  const toHex = (color: string): string => {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) return color;
    context.fillStyle = color;
    context.fillRect(0, 0, 1, 1);
    const [r = 0, g = 0, b = 0] = context.getImageData(0, 0, 1, 1).data;
    return `#${[r, g, b].map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
  };
  const activePalette = editorThemeColors(document.documentElement.classList.contains("dark"), {
    background: toHex(style.getPropertyValue("--background").trim()),
    foreground: toHex(style.getPropertyValue("--foreground").trim()),
  });
  const hexToRgb = (hex: string) => {
    const h = hex.replace('#', '');
    return {
      r: parseInt(h.substring(0, 2), 16),
      g: parseInt(h.substring(2, 4), 16),
      b: parseInt(h.substring(4, 6), 16),
    };
  };
  const rgb = hexToRgb(accent);
  const a = (alpha: number) => {
    const r = Math.round(rgb.r * alpha + 14 * (1 - alpha));
    const g = Math.round(rgb.g * alpha + 14 * (1 - alpha));
    const b = Math.round(rgb.b * alpha + 20 * (1 - alpha));
    return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${b.toString(16).padStart(2,'0')}`;
  };

  // Monaco takes literal colors, not CSS variables, so the palette stays hex.
  monaco.editor.defineTheme("kollab-void", {
    base: activePalette.base,
    inherit: true,
    rules: [
      { token: "comment", foreground: "4a4a55", fontStyle: "italic" },
      { token: "string", foreground: "e8b661" },
      { token: "keyword", foreground: "7eb8da" },
      { token: "number", foreground: "6dd4c8" },
      { token: "type", foreground: "7eb8da" },
      { token: "function", foreground: "e0e0e8" },
      { token: "variable", foreground: "c8c8d0" },
      { token: "operator", foreground: "ffffff66" },
      { token: "delimiter", foreground: "ffffff40" },
    ],
    colors: {
      ...activePalette.colors,
      "editor.lineHighlightBackground": a(0.06),
      "editor.selectionBackground": a(0.22),
      "editor.selectionHighlightBackground": a(0.10),
      "editor.inactiveSelectionBackground": a(0.08),
      "editorCursor.foreground": accent + "e0",
      "editorLineNumber.foreground": "#ffffff12",
      "editorLineNumber.activeForeground": accent + "80",
      "minimapSlider.background": accent + "18",
      "minimapSlider.hoverBackground": accent + "30",
      "minimapSlider.activeBackground": accent + "40",
      "scrollbar.shadow": "#00000000",
      "editorOverviewRuler.border": "#00000000",
      "editor.rangeHighlightBackground": a(0.04),
      "editorBracketMatch.background": a(0.12),
      "editorBracketMatch.border": accent + "30",
      "editorIndentGuide.background": "#ffffff08",
      "editorIndentGuide.activeBackground": "#ffffff15",
      "editorWidget.background": "#0a0a0a",
      "editorWidget.border": "#ffffff10",
      "input.background": "#080808",
      "input.border": "#ffffff10",
      "list.activeSelectionBackground": a(0.15),
      "list.hoverBackground": a(0.08),
    },
  });
}

const EXT_TO_LANG: Record<string, string> = {
  ".ts": "typescript",
  ".tsx": "typescriptreact",
  ".js": "javascript",
  ".jsx": "javascriptreact",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".json": "json",
  ".jsonl": "json",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".sh": "shell",
  ".bash": "shell",
  ".zsh": "shell",
  ".md": "markdown",
  ".mdx": "markdown",
  ".css": "css",
  ".scss": "scss",
  ".py": "python",
  ".go": "go",
  ".rs": "rust",
  ".rb": "ruby",
  ".html": "html",
  ".svg": "xml",
  ".xml": "xml",
  ".sql": "sql",
  ".graphql": "graphql",
  ".gql": "graphql",
  ".toml": "ini",
  ".ini": "ini",
  ".conf": "ini",
  ".txt": "plaintext",
};

/** The `error.message` of a `{ success: false, error }` body, or the fallback. */
function errorMessageOf(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = payload.error;
    if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
      return error.message || fallback;
    }
  }
  return fallback;
}

interface EditorPaneProps {
  paneId: string;
  rootPath: string;
  showFilePicker: boolean;
  onOpenFiles: () => void;
}

export function EditorPane({ paneId, rootPath, showFilePicker, onOpenFiles }: EditorPaneProps) {
  const { activeFile, pane } = usePane(paneId);
  const updateContent = useEditorStore((s) => s.updateContent);
  const markSaved = useEditorStore((s) => s.markSaved);
  const closeFileAction = useEditorStore((s) => s.closeFile);
  const setActiveFileAction = useEditorStore((s) => s.setActiveFile);
  const pendingReveal = useEditorStore((s) => s.pendingReveal);
  const setPendingReveal = useEditorStore((s) => s.setPendingReveal);
  const editorConfig = useEditorStore((s) => s.editorConfig);

  const file = activeFile;

  const [saving, setSaving] = useState(false);
  const notice = file?.saveNotice;
  const saveStale = Boolean(notice?.stale && notice.owner === editorOwner() && notice.epoch === editorOwnerEpoch());
  const saveError = notice ? (notice.stale && !saveStale
    ? "The file changed on disk. Save again before overwriting." : notice.message) : "";
  const [cursorLine, setCursorLine] = useState(1);
  const [cursorColumn, setCursorColumn] = useState(1);
  const [selectionLength, setSelectionLength] = useState(0);
  const [mdPreview, setMdPreview] = useState(true);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);

  const isMarkdown = file ? (file.ext === ".md" || file.ext === ".mdx") : false;
  const statusRight = isMarkdown ? "right-[72px]" : "right-4";

  // reset to preview mode when switching to a markdown file
  const prevPathRef = useRef<string | null>(null);
  useEffect(() => {
    if (file && file.path !== prevPathRef.current) {
      prevPathRef.current = file.path;
      const isMd = file.ext === ".md" || file.ext === ".mdx";
      setMdPreview(isMd);
    }
  }, [file]);

  const accent = file ? getFileAccentColor(file.path, rootPath) : "#64748b";

  const handleBeforeMount = useCallback((monaco: typeof import("monaco-editor")) => {
    monacoRef.current = monaco;
    defineVoidTheme(monaco, accent);
  }, [accent]);

  // re-define theme when accent changes
  useEffect(() => {
    if (monacoRef.current) {
      defineVoidTheme(monacoRef.current, accent);
    }
  }, [accent]);

  const handleSave = useCallback(async (force = false) => {
    if (!file || !isDirty(file)) return;
    if (force && (!notice?.stale || notice.owner !== editorOwner() || notice.epoch !== editorOwnerEpoch())) return;
    setBufferSaveNotice(file.path, file.bufferId, undefined);
    setSaving(true);
    const owner = editorOwner();
    const epoch = editorOwnerEpoch();
    const bufferId = file.bufferId;
    const saveRevision = file.saveRevision ?? 0;
    const result = await saveWorkspaceFile(rootPath, file.path, file.content, { force });
    const sameContext = owner === editorOwner() && epoch === editorOwnerEpoch();
    const sameBuffer = useEditorStore.getState().fileCache.get(file.path)?.bufferId === bufferId;
    if (sameBuffer) {
      if (result.ok && sameContext) {
        markSaved(file.path, file.content);
      } else if (result.ok || !result.stale || (useEditorStore.getState().fileCache.get(file.path)?.saveRevision ?? 0) === saveRevision) {
        setBufferSaveNotice(file.path, bufferId, {
          owner, epoch,
          message: result.ok ? "The save completed while the workspace changed. Check the file before retrying." : result.message,
          stale: !result.ok && result.stale,
        });
      }
    }
    setSaving(false);
  }, [file, markSaved, rootPath, notice]);

  // keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!editorShortcutEnabled(paneId)) return;
      const mod = e.metaKey || e.ctrlKey;

      // cmd+s save
      if (mod && e.key === "s") {
        e.preventDefault();
        handleSave();
        return;
      }

      // cmd+w close active tab
      if (mod && e.key === "w") {
        e.preventDefault();
        if (file?.path) {
          if (isDirty(file) && !window.confirm(`Discard unsaved changes to ${file.name}?`)) return;
          closeFileAction(paneId, file.path);
        }
        return;
      }

      // cmd+shift+[ previous tab
      if (mod && e.shiftKey && e.key === "[") {
        e.preventDefault();
        if (!pane) return;
        const idx = pane.openPaths.indexOf(file?.path ?? "");
        if (idx > 0) setActiveFileAction(paneId, pane.openPaths[idx - 1]);
        return;
      }

      // cmd+shift+] next tab
      if (mod && e.shiftKey && e.key === "]") {
        e.preventDefault();
        if (!pane) return;
        const idx = pane.openPaths.indexOf(file?.path ?? "");
        if (idx < pane.openPaths.length - 1) setActiveFileAction(paneId, pane.openPaths[idx + 1]);
        return;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSave, file, closeFileAction, setActiveFileAction, pane, paneId]);

  const handleEditorMount = useCallback(
    (editorInstance: editor.IStandaloneCodeEditor) => {
      editorRef.current = editorInstance;
      editorInstance.onDidChangeCursorPosition((e) => {
        setCursorLine(e.position.lineNumber);
        setCursorColumn(e.position.column);
      });
      editorInstance.onDidChangeCursorSelection(() => {
        const sel = editorInstance.getSelection();
        if (sel) {
          const text = editorInstance.getModel()?.getValueInRange(sel) ?? "";
          setSelectionLength(text.length);
        }
      });

      // check for pending reveal on mount
      if (pendingReveal && file && pendingReveal.path === file.path) {
        editorInstance.revealLineInCenter(pendingReveal.line);
        editorInstance.setPosition({
          lineNumber: pendingReveal.line,
          column: pendingReveal.column,
        });
        setPendingReveal(null);
      }
    },
    [file, pendingReveal, setPendingReveal]
  );

  // handle pending reveal when switching tabs (file path changes)
  useEffect(() => {
    if (!pendingReveal || !file || pendingReveal.path !== file.path) return;
    if (!editorRef.current) return;

    editorRef.current.revealLineInCenter(pendingReveal.line);
    editorRef.current.setPosition({
      lineNumber: pendingReveal.line,
      column: pendingReveal.column,
    });
    setPendingReveal(null);
  }, [file, file?.path, pendingReveal, setPendingReveal]);

  if (!file) {
    return <EmptyEditorState showFilePicker={showFilePicker} onOpenFiles={onOpenFiles} />;
  }

  if (file.loading) {
    return <PaneSpinner />;
  }

  const language = EXT_TO_LANG[file.ext] ?? "plaintext";

  const showMarkdownPreview = isMarkdown && mdPreview;

  return (
    <div className="flex h-full flex-col">
      <div className="relative flex-1 overflow-hidden">
        {showMarkdownPreview ? (
          <div className="h-full overflow-y-auto">
            <div className="mx-auto max-w-3xl px-8 py-8 text-sm leading-relaxed text-foreground [&_a]:text-highlight [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_code]:font-mono [&_code]:text-[0.9em] [&_h1]:mb-3 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:mt-4 [&_h3]:font-semibold [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-3 [&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:bg-muted [&_pre]:p-3 [&_ul]:list-disc [&_ul]:pl-5">
              <Markdown remarkPlugins={[remarkGfm]}>{file.content}</Markdown>
            </div>
          </div>
        ) : (
          <Suspense fallback={<PaneSpinner />}>
            {isDiffTab(file) ? (
              <MonacoDiffEditor
                height="100%"
                language={language}
                original={file.headContent ?? ""}
                modified={file.content}
                beforeMount={handleBeforeMount}
                theme="kollab-void"
                options={{
                  fontSize: editorConfig.fontSize,
                  fontFamily:
                    "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace",
                  lineHeight: 1.6,
                  minimap: { enabled: false },
                  scrollBeyondLastLine: false,
                  padding: { top: 8, bottom: 8 },
                  smoothScrolling: true,
                  renderWhitespace: editorConfig.renderWhitespace,
                  ...(editorConfig.tabSize ? { tabSize: editorConfig.tabSize } : {}),
                  lineNumbers: editorConfig.lineNumbers,
                  overviewRulerBorder: false,
                  readOnly: true,
                  renderSideBySide: true,
                }}
              />
            ) : (
              <MonacoEditor
                height="100%"
                path={file.virtual ? undefined : file.path}
                language={language}
                value={file.content}
                onChange={(val) => updateContent(file.path, val ?? "")}
                onMount={handleEditorMount}
                beforeMount={handleBeforeMount}
                theme="kollab-void"
                options={{
                  fontSize: editorConfig.fontSize,
                  readOnly: file.virtual === true,
                  fontFamily:
                    "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace",
                  lineHeight: 1.6,
                  minimap: { enabled: editorConfig.minimap, renderCharacters: false, showSlider: "always" },
                  scrollBeyondLastLine: false,
                  renderLineHighlight: "all",
                  padding: { top: 8, bottom: 8 },
                  smoothScrolling: true,
                  cursorStyle: "line-thin",
                  cursorSmoothCaretAnimation: "on",
                  cursorBlinking: "phase",
                  roundedSelection: true,
                  renderWhitespace: editorConfig.renderWhitespace,
                  tabSize: editorConfig.tabSize,
                  wordWrap: editorConfig.wordWrap,
                  lineNumbers: editorConfig.lineNumbers,
                  overviewRulerBorder: false,
                  hideCursorInOverviewRuler: true,
                }}
              />
            )}
          </Suspense>
        )}

        {/* markdown mode toggle */}
        {isMarkdown && (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setMdPreview((v) => !v)}
            className="absolute top-2 right-4 z-10 font-mono text-[10px] text-muted-foreground"
          >
            {mdPreview ? "edit" : "preview"}
          </Button>
        )}

        {/* floating save indicator - offset right when md toggle is present */}
        {isDirty(file) && !saveError && (
          <div className={`absolute top-2 z-10 flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground animate-in fade-in duration-300 ${statusRight}`}>
            <span className="size-1 animate-pulse rounded-full bg-foreground/60" />
            unsaved
          </div>
        )}
        {saving && (
          <div className={`absolute top-2 z-10 font-mono text-[10px] text-muted-foreground ${statusRight}`}>
            saving...
          </div>
        )}
        {!saving && saveError && (
          <div className={`absolute top-2 z-10 flex items-center gap-3 font-mono text-[10px] text-destructive ${statusRight}`}>
            <button
              type="button"
              title={saveError}
              onClick={() => {
                if (file) setBufferSaveNotice(file.path, file.bufferId, undefined);
              }}
              className="max-w-[min(70vw,28rem)] cursor-pointer whitespace-normal text-left"
            >
              ✕ {saveStale ? "Disk changed; overwrite replaces its contents." : saveError}
            </button>
            {saveStale && (
              <button type="button" onClick={() => void handleSave(true)} className="cursor-pointer text-foreground underline">
                Overwrite
              </button>
            )}
          </div>
        )}
      </div>

      <StatusBar paneId={paneId} cursorLine={cursorLine} cursorColumn={cursorColumn} selectionLength={selectionLength} />
    </div>
  );
}
