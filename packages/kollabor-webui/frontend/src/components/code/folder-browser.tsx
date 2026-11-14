import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";

type Listing = { path: string; parent: string; folders: { name: string; path: string }[]; next: string | null };

export function FolderBrowser({ open, initialPath, onClose, onSelect }: {
  open: boolean; initialPath: string; onClose: () => void; onSelect: (path: string) => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [input, setInput] = useState(initialPath);
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const generation = useRef(0);
  const navigate = (next: string) => {
    if (next === path && listing) return;
    generation.current += 1;
    setLoadingMore(false);
    setListing(null);
    setError("");
    setInput(next);
    if (next === path) setRetry((value) => value + 1);
    else setPath(next);
  };
  useEffect(() => { if (open) { setPath(initialPath); setInput(initialPath); setLoadingMore(false); } }, [open, initialPath]);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setListing(null);
    setError("");
    void codeFetch(`/code/folders/browse?path=${encodeURIComponent(path)}`).then(async (response) => {
      const raw: unknown = await response.json();
      if (!response.ok) throw new Error("That folder is unavailable or cannot be read.");
      const data = unwrapCodeData<Listing>(raw);
      if (!cancelled) setListing(data);
    }).catch(() => { if (!cancelled) setError("That folder is unavailable or cannot be read."); });
    return () => { cancelled = true; generation.current += 1; };
  }, [open, path, retry]);
  const loadMore = async () => {
    if (!listing?.next || loadingMore) return;
    const current = generation.current;
    setLoadingMore(true);
    setError("");
    try {
      const response = await codeFetch(`/code/folders/browse?path=${encodeURIComponent(path)}&after=${encodeURIComponent(listing.next)}`);
      const data = unwrapCodeData<Listing>(await response.json());
      if (!response.ok) throw new Error("Could not load more folders.");
      if (current === generation.current) setListing((old) => old ? { ...data, folders: [...old.folders, ...data.folders] } : old);
    } catch (e) {
      if (current === generation.current) setError(e instanceof Error ? e.message : "Could not load more folders.");
    } finally {
      if (current === generation.current) setLoadingMore(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(value) => { if (!value) onClose(); }}>
      <DialogContent>
        <DialogTitle>Choose a folder</DialogTitle>
        <DialogDescription>Browse folders on the agent’s computer. Code and Git use the same folder.</DialogDescription>
        <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); navigate(input); }}>
          <input aria-label="Folder path" disabled={!listing && !error} value={input} onChange={(event) => setInput(event.target.value)} className="min-w-0 flex-1 rounded border bg-background px-2 text-sm" />
          <Button type="submit" variant="outline" size="sm">Go</Button>
        </form>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="max-h-64 min-h-24 overflow-y-auto rounded border">
          {listing && listing.parent !== listing.path && <button type="button" className="block w-full px-3 py-2 text-left text-sm hover:bg-muted" onClick={() => navigate(listing.parent)}>..</button>}
          {listing?.folders.map((folder) => <button type="button" key={`${folder.name}:${folder.path}`} className="block w-full truncate px-3 py-2 text-left text-sm hover:bg-muted" onClick={() => navigate(folder.path)}>{folder.name}/</button>)}
          {listing?.next && <button type="button" disabled={loadingMore} className="block w-full px-3 py-2 text-left text-sm hover:bg-muted" onClick={() => void loadMore()}>{loadingMore ? "Loading…" : "Load more folders"}</button>}
          {!listing && !error && <p className="p-3 text-sm text-muted-foreground">Loading folders…</p>}
          {listing && listing.folders.length === 0 && <p className="p-3 text-sm text-muted-foreground">No subfolders</p>}
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="button" disabled={!listing} onClick={() => { if (listing) onSelect(listing.path); }}>Open folder</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
