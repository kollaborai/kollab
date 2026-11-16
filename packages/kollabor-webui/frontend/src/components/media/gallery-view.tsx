/**
 * gallery-view.tsx - one agent's Gallery (#1088): the images sent to it and
 * the tool outputs its work spilled to disk. Sent images open the shared
 * viewer (oldest first, the order they were sent); artifacts expand inline
 * with their text and a download.
 */
import { useCallback, useEffect, useMemo, useState, type FC } from "react";
import { DownloadIcon, Loader2Icon } from "lucide-react";

import type { EngineApi, MediaEntry } from "@/api";
import { MediaThumb, useMediaViewer } from "@/components/media/media-view";
import { cn } from "@/lib/utils";

const SECTION = "text-muted-foreground text-xs font-medium";
const EMPTY = "text-muted-foreground/70 border-border/60 rounded-lg border border-dashed px-3 py-6 text-center text-xs";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const SentImages: FC<{ api: EngineApi; sessionId: string }> = ({ api, sessionId }) => {
  const [media, setMedia] = useState<MediaEntry[] | null>(null);
  const [failed, setFailed] = useState(false);
  const openViewer = useMediaViewer();

  useEffect(() => {
    setMedia(null);
    setFailed(false);
    let current = true;
    api
      .listMedia(sessionId)
      .then((response) => {
        if (current) setMedia(response.media);
      })
      .catch(() => {
        if (current) setFailed(true);
      });
    return () => {
      current = false;
    };
  }, [api, sessionId]);

  // The list is oldest first; the grid reads newest first like a camera roll,
  // while the viewer steps through them in the order they were sent.
  const grid = useMemo(() => (media ? [...media].reverse() : []), [media]);

  if (failed) {
    return <p className={EMPTY}>Could not load sent images.</p>;
  }
  if (!media) {
    return (
      <p className={EMPTY}>
        <Loader2Icon className="mx-auto size-4 animate-spin" aria-label="Loading sent images" />
      </p>
    );
  }
  if (!media.length) {
    return <p className={EMPTY}>No images sent to this agent yet.</p>;
  }
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
      {grid.map((entry) => {
        const position = media.indexOf(entry);
        return (
          <MediaThumb
            key={entry.ref}
            ref_={entry.ref}
            className="aspect-square size-auto w-full"
            onOpen={openViewer ? () => openViewer(media, position) : undefined}
          />
        );
      })}
    </div>
  );
};

type ArtifactEntry = { ref: string; name: string; bytes: number; modified: string };

const Artifacts: FC<{ api: EngineApi; sessionId: string }> = ({ api, sessionId }) => {
  const [artifacts, setArtifacts] = useState<ArtifactEntry[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [openRef, setOpenRef] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    setArtifacts(null);
    setFailed(false);
    setOpenRef(null);
    setContent(null);
    let current = true;
    api
      .listArtifacts(sessionId)
      .then((response) => {
        if (current) setArtifacts(response.artifacts);
      })
      .catch(() => {
        if (current) setFailed(true);
      });
    return () => {
      current = false;
    };
  }, [api, sessionId]);

  const toggle = useCallback(
    (ref: string) => {
      if (openRef === ref) {
        setOpenRef(null);
        setContent(null);
        return;
      }
      setOpenRef(ref);
      setContent(null);
      setLoadFailed(false);
      api
        .readArtifact(sessionId, ref)
        .then((response) => setContent(response.content))
        .catch(() => setLoadFailed(true));
    },
    [api, openRef, sessionId],
  );

  const download = useCallback(
    (entry: ArtifactEntry, text: string) => {
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${entry.name}.output`;
      link.click();
      URL.revokeObjectURL(url);
    },
    [],
  );

  if (failed) {
    return <p className={EMPTY}>Could not load artifacts.</p>;
  }
  if (!artifacts) {
    return (
      <p className={EMPTY}>
        <Loader2Icon className="mx-auto size-4 animate-spin" aria-label="Loading artifacts" />
      </p>
    );
  }
  if (!artifacts.length) {
    return <p className={EMPTY}>No artifacts yet.</p>;
  }
  return (
    <div className="flex flex-col gap-1">
      {artifacts.map((entry) => (
        <div key={entry.ref} className="border-border/60 overflow-hidden rounded-lg border">
          <button
            type="button"
            onClick={() => toggle(entry.ref)}
            aria-expanded={openRef === entry.ref}
            data-testid={`artifact-${entry.name}`}
            className="hover:bg-accent/50 flex w-full items-center gap-2 px-2 py-1.5 text-start text-xs transition"
          >
            <span className="min-w-0 flex-1 truncate font-mono">{entry.name}</span>
            <span className="text-muted-foreground shrink-0 tabular-nums">{formatBytes(entry.bytes)}</span>
            <span className="text-muted-foreground/70 shrink-0 tabular-nums">{entry.modified}</span>
          </button>
          {openRef === entry.ref ? (
            <div className="bg-muted/30 border-border/60 border-t">
              {loadFailed ? (
                <p className="text-muted-foreground px-2 py-2 text-xs">Could not load this artifact.</p>
              ) : content === null ? (
                <p className="text-muted-foreground px-2 py-2 text-xs">Loading…</p>
              ) : (
                <>
                  <pre className="text-foreground max-h-64 overflow-auto px-2 py-2 font-mono text-xs whitespace-pre-wrap break-words">
                    {content}
                  </pre>
                  <div className="border-border/60 flex justify-end border-t px-2 py-1">
                    <button
                      type="button"
                      onClick={() => download(entry, content)}
                      className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs transition"
                      data-testid={`artifact-download-${entry.name}`}
                    >
                      <DownloadIcon className="size-3.5" />
                      Download
                    </button>
                  </div>
                </>
              )}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
};

/**
 * The agent's Gallery view: what was sent to it, what it produced.
 */
export function GalleryView({ api, sessionId }: { api: EngineApi; sessionId: string }) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3">
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        <section className="flex flex-col gap-2">
          <h2 className={SECTION}>Sent images</h2>
          <SentImages api={api} sessionId={sessionId} />
        </section>
        <section className={cn("flex flex-col gap-2")}>
          <h2 className={SECTION}>Artifacts</h2>
          <Artifacts api={api} sessionId={sessionId} />
        </section>
      </div>
    </div>
  );
}
