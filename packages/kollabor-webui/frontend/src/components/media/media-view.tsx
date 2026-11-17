import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FC,
  type ReactNode,
} from "react";
import { ChevronLeftIcon, ChevronRightIcon, ImageOff, ImageIcon, XIcon } from "lucide-react";

import type { EngineApi, MediaEntry } from "@/api";
import { cn } from "@/lib/utils";

type LoadImage = (path: string) => Promise<string | null>;

const MediaImageContext = createContext<LoadImage | null>(null);

const SessionMediaContext = createContext<MediaEntry[] | null>(null);

/** Loads the active session's complete image list so chat thumbnails share the Gallery viewer order. */
export function MediaSessionProvider({
  api,
  sessionId,
  children,
}: {
  api: EngineApi;
  sessionId: string;
  children: ReactNode;
}) {
  const [media, setMedia] = useState<MediaEntry[] | null>(null);
  useEffect(() => {
    let current = true;
    setMedia(null);
    void api.listMedia(sessionId).then(
      (response) => {
        if (current) setMedia(response.media);
      },
      () => {
        if (current) setMedia(null);
      },
    );
    return () => {
      current = false;
    };
  }, [api, sessionId]);
  return <SessionMediaContext.Provider value={media}>{children}</SessionMediaContext.Provider>;
}

/** All images sent in the active chat, oldest first, once the list is loaded. */
export function useSessionMedia(): MediaEntry[] | null {
  return useContext(SessionMediaContext);
}

/**
 * Fetches the images the engine serves from /media/{ref}. An <img> cannot send
 * the bearer token, so each file is fetched once with it and shown from a blob
 * (the same pattern as the roster avatars); a file that fails is remembered as
 * failed and its tile shows a placeholder.
 */
export function MediaImageProvider({ api, children }: { api: EngineApi; children: ReactNode }) {
  const cache = useRef(new Map<string, Promise<string | null>>());
  useEffect(() => {
    const files = cache.current;
    return () => {
      for (const file of files.values()) void file.then((url) => url && URL.revokeObjectURL(url));
      files.clear();
    };
  }, []);
  const load = useCallback<LoadImage>(
    (path) => {
      let file = cache.current.get(path);
      if (!file) {
        file = api
          .request(path)
          .then((response) => (response.ok ? response.blob() : Promise.reject(new Error(String(response.status)))))
          .then((blob) => URL.createObjectURL(blob))
          .catch(() => null);
        cache.current.set(path, file);
      }
      return file;
    },
    [api],
  );
  return <MediaImageContext.Provider value={load}>{children}</MediaImageContext.Provider>;
}

/** The blob URL for a media ref: undefined while loading, null when it cannot be shown. */
export function useMediaImage(ref: string | null | undefined): string | null | undefined {
  const load = useContext(MediaImageContext);
  const path = ref && load ? `/media/${encodeURIComponent(ref)}` : null;
  const [loaded, setLoaded] = useState<{ path: string; url: string | null } | null>(null);
  useEffect(() => {
    if (!path || !load) return;
    let current = true;
    void load(path).then((url) => {
      if (current) setLoaded({ path, url });
    });
    return () => {
      current = false;
    };
  }, [path, load]);
  if (!ref) return null;
  if (!loaded || loaded.path !== path) return undefined;
  return loaded.url;
}

/** One sent image, small: the thumbnail in a user bubble or a gallery grid. */
export const MediaThumb: FC<{
  ref_: string;
  className?: string;
  onOpen?: () => void;
}> = ({ ref_, className, onOpen }) => {
  // Hook stays unconditional; useMediaImage itself null-guards empty refs.
  const url = useMediaImage(ref_);
  // A slot with no stored ref (failed storage) renders a muted, inert
  // placeholder: no image request, no click, no viewer (#1124 P2a).
  if (!ref_) {
    return (
      <span
        className={cn(
          "bg-muted/40 text-muted-foreground/50 relative block size-20 shrink-0 overflow-hidden rounded-lg border border-dashed",
          className,
        )}
        aria-label="image unavailable"
      >
        <span className="flex size-full items-center justify-center">
          <ImageOff className="size-4" />
        </span>
      </span>
    );
  }
  const body =
    url === undefined ? (
      <span className="bg-muted/60 animate-pulse block size-full" />
    ) : url === null ? (
      <span className="text-muted-foreground/70 bg-muted/60 flex size-full items-center justify-center">
        <ImageIcon className="size-4" />
      </span>
    ) : (
      <img src={url} alt="sent image" className="size-full object-cover" draggable={false} />
    );
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "focus-visible:ring-ring/60 relative block size-20 shrink-0 overflow-hidden rounded-lg border transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2",
        className,
      )}
      aria-label="Open image"
    >
      {body}
    </button>
  );
};

type MediaViewerState = { items: MediaEntry[]; index: number } | null;

type OpenViewer = (items: MediaEntry[], index: number) => void;

const MediaViewerContext = createContext<OpenViewer | null>(null);

/** Opens the full-size viewer; null where no provider is mounted. */
export function useMediaViewer(): OpenViewer | null {
  return useContext(MediaViewerContext);
}

/**
 * The session's sent images, one at a time: arrows step through them in the
 * order given (oldest first), Escape or the backdrop closes. Mounts near the
 * app root so any thumbnail on any panel can open it.
 */
export function MediaViewerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<MediaViewerState>(null);
  const open = useCallback<OpenViewer>((items, index) => {
    if (items.length) setState({ items, index: Math.min(Math.max(index, 0), items.length - 1) });
  }, []);
  const close = useCallback(() => setState(null), []);
  const step = useCallback((delta: number) => {
    setState((current) => {
      if (!current) return current;
      const index = current.index + delta;
      if (index < 0 || index >= current.items.length) return current;
      return { ...current, index };
    });
  }, []);

  useEffect(() => {
    if (!state) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
      else if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, close, step]);

  const value = useMemo(() => open, [open]);
  const items = state?.items ?? null;
  const index = state?.index ?? 0;
  const current = items ? items[index] : null;
  return (
    <MediaViewerContext.Provider value={value}>
      {children}
      {current && items ? (
        <div
          className="fixed inset-0 z-[80] flex items-center justify-center bg-black/80 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Sent image"
          onClick={close}
        >
          <div className="relative flex max-h-full max-w-full flex-col items-center gap-3" onClick={(e) => e.stopPropagation()}>
            <ViewerImage ref_={current.ref} />
            <div className="text-muted-foreground/80 flex items-center gap-4 text-sm">
              {items.length > 1 ? (
                <button
                  type="button"
                  onClick={() => step(-1)}
                  disabled={index === 0}
                  className="text-foreground/90 hover:bg-white/10 disabled:opacity-30 rounded-full p-1"
                  aria-label="Older image"
                >
                  <ChevronLeftIcon className="size-6" />
                </button>
              ) : null}
              <span>
                {index + 1} / {items.length}
              </span>
              {items.length > 1 ? (
                <button
                  type="button"
                  onClick={() => step(1)}
                  disabled={index === items.length - 1}
                  className="text-foreground/90 hover:bg-white/10 disabled:opacity-30 rounded-full p-1"
                  aria-label="Newer image"
                >
                  <ChevronRightIcon className="size-6" />
                </button>
              ) : null}
            </div>
            <button
              type="button"
              onClick={close}
              className="text-foreground/90 hover:bg-white/10 absolute -top-2 end-0 translate-y-[-100%] rounded-full p-1"
              aria-label="Close"
            >
              <XIcon className="size-6" />
            </button>
          </div>
        </div>
      ) : null}
    </MediaViewerContext.Provider>
  );
}

const ViewerImage: FC<{ ref_: string }> = ({ ref_ }) => {
  const url = useMediaImage(ref_);
  return url ? (
    <img src={url} alt="sent image" className="max-h-[80vh] max-w-full rounded-lg object-contain" draggable={false} />
  ) : (
    <div className="bg-muted/40 flex size-64 items-center justify-center rounded-lg">
      <span className="text-muted-foreground/70 animate-pulse text-sm">Loading…</span>
    </div>
  );
};
