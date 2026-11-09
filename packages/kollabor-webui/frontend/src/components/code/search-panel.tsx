import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Loader2, Search } from "lucide-react";
import { useEditorStore } from "@/components/code/editor-store";
import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";
import { cn } from "@/lib/utils";

export interface SearchResult {
  path: string;
  name: string;
  line: number;
  column: number;
  text: string;
  context: string;
}

interface SearchPanelProps {
  workspace: string;
}

export function SearchPanel({ workspace }: SearchPanelProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [regex, setRegex] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // Bumped by every search and by a cleared query; a response that is no longer the latest is dropped.
  const searchSeq = useRef(0);

  const activePaneId = useEditorStore((s) => s.activePaneId);
  const openFile = useEditorStore((s) => s.openFile);
  const pinFile = useEditorStore((s) => s.pinFile);
  const setFileLoading = useEditorStore((s) => s.setFileLoading);
  const setPendingReveal = useEditorStore((s) => s.setPendingReveal);

  // auto-focus when opened
  useEffect(() => {
    const timer = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, []);

  const doSearch = useCallback(
    async (q: string) => {
      const seq = ++searchSeq.current;
      setLoading(true);
      try {
        const url = `/api/fs/search?workspace=${encodeURIComponent(workspace)}&query=${encodeURIComponent(q)}&regex=${regex}`;
        const res = await codeFetch(url);
        const raw: unknown = await res.json();
        if (res.ok && seq === searchSeq.current) {
          const data = unwrapCodeData<{ results?: SearchResult[] }>(raw);
          setResults(data.results ?? []);
        }
      } catch {
        if (seq === searchSeq.current) setResults([]);
      } finally {
        if (seq === searchSeq.current) setLoading(false);
      }
    },
    [workspace, regex],
  );

  // debounce search
  useEffect(() => {
    if (!query || query.length < 2) {
      searchSeq.current += 1; // a search still in flight is now stale
      setLoading(false);
      setResults([]);
      return;
    }

    const timer = setTimeout(() => {
      void doSearch(query);
    }, 300);

    return () => clearTimeout(timer);
  }, [query, regex, doSearch]);

  const handleResultClick = useCallback(
    async (result: SearchResult) => {
      if (!activePaneId) return;

      const ext = result.name.includes(".")
        ? result.name.slice(result.name.lastIndexOf("."))
        : "";

      // construct full path from relative path
      const fullPath = result.path.startsWith("/")
        ? result.path
        : `${workspace}/${result.path}`;

      // set loading state
      openFile(activePaneId, fullPath, result.name, ext, "");
      pinFile(fullPath);
      setFileLoading(fullPath, true);

      try {
        const url = `/api/fs/file?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(fullPath)}`;
        const res = await codeFetch(url);
        const raw: unknown = await res.json();
        if (res.ok) {
          const data = unwrapCodeData<{ content?: string }>(raw);
          openFile(activePaneId, fullPath, result.name, ext, data.content ?? "");
          // trigger reveal after file loads
          setPendingReveal({ path: fullPath, line: result.line, column: result.column });
        }
      } catch {
        // silent
      } finally {
        setFileLoading(fullPath, false);
      }
    },
    [activePaneId, workspace, openFile, pinFile, setFileLoading, setPendingReveal],
  );

  // group results by file
  const grouped = results.reduce((acc, r) => {
    if (!acc[r.path]) acc[r.path] = [];
    acc[r.path].push(r);
    return acc;
  }, {} as Record<string, SearchResult[]>);

  const fileCount = Object.keys(grouped).length;
  const resultCount = results.length;

  // highlight every match in the line; an invalid regex falls back to plain text
  const highlightMatch = (text: string, q: string): ReactNode => {
    if (!q) return text;
    let pattern: RegExp;
    try {
      pattern = new RegExp(regex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    } catch {
      return text;
    }
    const nodes: ReactNode[] = [];
    let last = 0;
    for (const match of text.matchAll(pattern)) {
      const hit = match[0];
      if (hit === "") continue; // an empty match highlights nothing
      const start = match.index ?? 0;
      if (start > last) nodes.push(text.slice(last, start));
      nodes.push(
        <mark key={start} className="text-foreground bg-highlight/30 rounded-sm">
          {hit}
        </mark>,
      );
      last = start + hit.length;
    }
    if (last < text.length) nodes.push(text.slice(last));
    return nodes;
  };

  return (
    <div className="flex flex-col h-full bg-muted">
      {/* search input */}
      <div className="flex items-center gap-2 px-3 py-2 shrink-0">
        <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <input
          ref={inputRef}
          type="text"
          aria-label="Search in files"
          placeholder="search in files..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="flex-1 min-w-0 bg-muted text-xs text-foreground placeholder:text-muted-foreground outline-none font-mono"
        />
        <button
          type="button"
          aria-label="Match as regular expression"
          aria-pressed={regex}
          onClick={() => setRegex((v) => !v)}
          className={cn(
            "text-[10px] px-1.5 py-0.5 rounded-sm transition-colors shrink-0 font-mono",
            regex ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          .*
        </button>
        {loading && (
          <Loader2 aria-label="Searching" className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
        )}
      </div>

      {/* results */}
      <div className="flex-1 overflow-y-auto">
        {query.length < 2 ? (
          <div className="px-3 py-4 text-xs text-muted-foreground text-center">
            type at least 2 characters
          </div>
        ) : loading && results.length === 0 ? (
          <div className="px-3 py-4 flex justify-center">
            <Loader2 aria-label="Searching" className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : resultCount === 0 ? (
          <div className="px-3 py-4 text-xs text-muted-foreground text-center">
            no results found
          </div>
        ) : (
          <>
            {/* result count header */}
            <div className="px-3 py-1.5 text-[10px] text-muted-foreground font-mono">
              {resultCount} result{resultCount !== 1 ? "s" : ""} in {fileCount} file{fileCount !== 1 ? "s" : ""}
            </div>

            {/* grouped results */}
            {Object.entries(grouped).map(([filePath, fileResults]) => (
              <div key={filePath}>
                {/* file header */}
                <div
                  className="px-3 py-1 bg-accent/20 text-[10px] text-foreground/50 font-mono truncate cursor-pointer hover:bg-accent/30 transition-colors"
                  onClick={() => {
                    const [first] = fileResults;
                    if (first) void handleResultClick(first);
                  }}
                >
                  {filePath}
                </div>

                {/* file results */}
                {fileResults.map((r, idx) => (
                  <div
                    key={idx}
                    onClick={() => void handleResultClick(r)}
                    className="px-3 py-1 text-xs font-mono hover:bg-accent transition-colors cursor-pointer border-l-2 border-transparent hover:border-foreground/20 overflow-hidden"
                  >
                    <div className="flex items-start gap-2">
                      <span className="text-muted-foreground shrink-0 select-none">
                        {r.line}
                      </span>
                      <span className="flex-1 text-foreground/70 overflow-hidden text-ellipsis whitespace-nowrap block">
                        {highlightMatch(r.text, query)}
                      </span>
                    </div>
                    {r.context && (
                      <div className="ml-4 mt-0.5 text-muted-foreground text-[11px] overflow-hidden text-ellipsis">
                        {r.context}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
