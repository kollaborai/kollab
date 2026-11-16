import { usePane, isDirty } from "@/components/code/editor-store";

const EXT_TO_LANG: Record<string, string> = {
  ".ts": "TypeScript",
  ".tsx": "TypeScript React",
  ".js": "JavaScript",
  ".jsx": "JavaScript React",
  ".mjs": "JavaScript",
  ".json": "JSON",
  ".yaml": "YAML",
  ".yml": "YAML",
  ".sh": "Shell",
  ".bash": "Shell",
  ".md": "Markdown",
  ".css": "CSS",
  ".scss": "SCSS",
  ".py": "Python",
  ".go": "Go",
  ".rs": "Rust",
  ".rb": "Ruby",
  ".html": "HTML",
  ".sql": "SQL",
  ".toml": "TOML",
  ".txt": "Plain Text",
};

interface StatusBarProps {
  paneId: string;
  cursorLine: number;
  cursorColumn: number;
  selectionLength?: number;
}

export function StatusBar({ paneId, cursorLine, cursorColumn, selectionLength }: StatusBarProps) {
  const { activeFile } = usePane(paneId);
  const file = activeFile;

  if (!file) return null;

  const language = EXT_TO_LANG[file.ext] ?? "Plain Text";
  const lines = file.content.split("\n").length;
  const size = new Blob([file.content]).size;
  const sizeLabel =
    size < 1024
      ? `${size} B`
      : size < 1024 * 1024
        ? `${(size / 1024).toFixed(1)} KB`
        : `${(size / 1024 / 1024).toFixed(1)} MB`;
  const dirty = isDirty(file);

  return (
    <div className="flex items-center justify-between px-3 py-0.5 shrink-0 text-[10px] text-muted-foreground font-mono bg-muted/30">
      <div className="flex items-center gap-3">
        <span className="hover:bg-muted/50 px-1 rounded-sm cursor-default transition-colors">
          Ln {cursorLine}, Col {cursorColumn}
        </span>
        {(selectionLength ?? 0) > 0 && (
          <span className="text-foreground/70">{selectionLength} selected</span>
        )}
        <span>{lines} lines</span>
        {dirty && (
          <span className="flex items-center gap-1 text-amber-500/70">
            <span className="w-1 h-1 rounded-full bg-amber-500/70 animate-pulse" />
            modified
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        <span>{sizeLabel}</span>
        <span>UTF-8</span>
        <span>LF</span>
        <span className="text-foreground/70">{language}</span>
      </div>
    </div>
  );
}
