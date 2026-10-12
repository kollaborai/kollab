import { useCallback, useEffect, useState } from "react";
import { Check, ChevronRight, CornerLeftUp, Folder, FolderOpen, Loader2 } from "lucide-react";
import type { EngineApi, WorkspaceEntry } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

type Listing = { path: string; name: string; parent: string | null; dirs: string[] };

/** New Session's folder: the engine's own (empty value), a recent one, or any folder browsed to or typed. */
export function WorkspacePicker({
  api,
  value,
  onChange,
}: {
  api: EngineApi;
  value: string;
  onChange: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [home, setHome] = useState<WorkspaceEntry | null>(null);
  const [recent, setRecent] = useState<WorkspaceEntry[]>([]);
  const [listing, setListing] = useState<Listing | null>(null);
  const [typed, setTyped] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const browse = useCallback(
    async (path: string) => {
      setLoading(true);
      setError("");
      try {
        const next = await api.browseWorkspace(path);
        setListing(next);
        return next;
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [api],
  );

  // The engine's folder names the field before it is ever opened.
  useEffect(() => {
    api
      .listWorkspaces()
      .then((result) => setHome(result.home))
      .catch(() => {});
  }, [api]);

  useEffect(() => {
    if (!open) return;
    setTyped("");
    api
      .listWorkspaces()
      .then((result) => {
        setHome(result.home);
        setRecent(result.recent);
        void browse(value || result.home.path);
      })
      .catch(() => void browse(value));
  }, [open, api, browse, value]);

  const choose = (path: string) => {
    onChange(path === home?.path ? "" : path);
    setOpen(false);
  };
  const chosen = value || home?.path || "";
  const name = value ? value.split("/").filter(Boolean).pop() || value : home?.name;

  const row = (entry: WorkspaceEntry, label?: string) => (
    <button
      key={entry.path}
      type="button"
      onClick={() => choose(entry.path)}
      title={entry.path}
      className="hover:bg-accent flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left"
    >
      <Folder className="text-muted-foreground size-4 shrink-0" />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-xs font-medium">
          {entry.name}
          {label ? <span className="text-muted-foreground font-normal"> · {label}</span> : null}
        </span>
        <Tail className="text-[0.6875rem]" text={entry.path} />
      </span>
      {entry.live ? (
        <span className="text-muted-foreground shrink-0 text-[0.6875rem] tabular-nums">{entry.live} Live</span>
      ) : null}
      {entry.path === chosen ? <Check className="size-3.5 shrink-0" /> : null}
    </button>
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id="new-session-workspace"
          type="button"
          variant="outline"
          size="sm"
          className="h-8 w-full min-w-0 justify-start gap-2 px-2.5 text-xs font-normal"
          title={value || "This engine's folder"}
        >
          <FolderOpen className="text-muted-foreground size-3.5 shrink-0" />
          <span className="truncate">{name || "Default Folder"}</span>
          {value ? null : <span className="text-muted-foreground shrink-0">· Default</span>}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="start"
        collisionPadding={16}
        className="flex max-h-[min(30rem,70vh)] w-80 max-w-[calc(100vw-2rem)] flex-col gap-2 p-2"
      >
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            const path = typed.trim();
            if (!path) return;
            const found = await browse(path);
            if (found) choose(found.path);
          }}
        >
          <Input
            autoFocus
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder="Type a path, press Enter"
            className="h-8 text-xs"
            aria-label="Folder path"
          />
        </form>
        {error ? <p className="text-destructive px-1 text-[0.6875rem]">{error}</p> : null}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {home ? (
            <>
              <p className="text-muted-foreground px-2 pt-1 pb-0.5 text-[0.6875rem] font-medium">Default</p>
              {row(home, "Engine Folder")}
            </>
          ) : null}
          {recent.length ? (
            <>
              <p className="text-muted-foreground px-2 pt-2 pb-0.5 text-[0.6875rem] font-medium">Recent</p>
              {recent.map((entry) => row(entry))}
            </>
          ) : null}
          <p className="text-muted-foreground flex items-center gap-1 px-2 pt-2 pb-0.5 text-[0.6875rem] font-medium">
            Browse
            {loading ? <Loader2 className="size-3 animate-spin" /> : null}
          </p>
          {listing ? (
            <>
              <div className="flex items-center gap-1 px-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7 shrink-0"
                  disabled={!listing.parent}
                  onClick={() => listing.parent && void browse(listing.parent)}
                  aria-label="Up One Folder"
                >
                  <CornerLeftUp className="size-3.5" />
                </Button>
                <Tail className="min-w-0 flex-1 text-[0.6875rem]" text={listing.path} />
              </div>
              {listing.dirs.map((dir) => (
                <button
                  key={dir}
                  type="button"
                  onClick={() => void browse(`${listing.path.replace(/\/$/, "")}/${dir}`)}
                  className="hover:bg-accent flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs"
                >
                  <Folder className="text-muted-foreground size-3.5 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{dir}</span>
                  <ChevronRight className="text-muted-foreground size-3.5 shrink-0" />
                </button>
              ))}
              {!listing.dirs.length ? (
                <p className="text-muted-foreground px-2 py-1 text-[0.6875rem]">No folders inside.</p>
              ) : null}
            </>
          ) : null}
        </div>
        {listing ? (
          <Button
            type="button"
            size="sm"
            className={cn("w-full min-w-0", listing.path === chosen && "opacity-80")}
            onClick={() => choose(listing.path)}
          >
            <span className="truncate">Use {listing.name}</span>
          </Button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

/** A path cut from the left, so the folder it ends in stays visible. */
function Tail({ text, className }: { text: string; className?: string }) {
  return (
    <span dir="rtl" title={text} className={cn("text-muted-foreground truncate text-left", className)}>
      {`‎${text}`}
    </span>
  );
}
