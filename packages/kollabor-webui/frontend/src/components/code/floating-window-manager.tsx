import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { Maximize2, Minimize2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const MIN_W = 300;
const MIN_H = 200;
const MOBILE_QUERY = "(max-width: 640px)";

export interface FloatingWindowManagerProps {
  /** localStorage key the geometry persists under. Read once, on mount. */
  storageKey: string;
  zIndex: number;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** Attributes set on the root, e.g. `{ "data-floating-code-panel": "" }`. */
  dataAttr?: Record<string, string>;
}

interface WindowGeometry {
  x: number;
  y: number;
  w: number;
  h: number;
  maximized: boolean;
}

interface ResizeEdges {
  top?: boolean;
  bottom?: boolean;
  left?: boolean;
  right?: boolean;
}

const RESIZE_HANDLES: { key: string; className: string; edges: ResizeEdges }[] = [
  { key: "top", className: "absolute top-0 left-2 right-2 h-1 cursor-ns-resize z-10", edges: { top: true } },
  { key: "bottom", className: "absolute bottom-0 left-2 right-2 h-1 cursor-ns-resize z-10", edges: { bottom: true } },
  { key: "left", className: "absolute top-2 bottom-2 left-0 w-1 cursor-ew-resize z-10", edges: { left: true } },
  { key: "right", className: "absolute top-2 bottom-2 right-0 w-1 cursor-ew-resize z-10", edges: { right: true } },
  { key: "top-left", className: "absolute top-0 left-0 w-3 h-3 cursor-nwse-resize z-20", edges: { top: true, left: true } },
  { key: "top-right", className: "absolute top-0 right-0 w-3 h-3 cursor-nesw-resize z-20", edges: { top: true, right: true } },
  { key: "bottom-left", className: "absolute bottom-0 left-0 w-3 h-3 cursor-nesw-resize z-20", edges: { bottom: true, left: true } },
  { key: "bottom-right", className: "absolute bottom-0 right-0 w-3 h-3 cursor-nwse-resize z-20", edges: { bottom: true, right: true } },
];

const clamp = (n: number, lo: number, hi: number): number => Math.min(Math.max(n, lo), Math.max(lo, hi));

const isFiniteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function isWindowGeometry(value: unknown): value is WindowGeometry {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    isFiniteNumber(v.x) &&
    isFiniteNumber(v.y) &&
    isFiniteNumber(v.w) &&
    isFiniteNumber(v.h) &&
    typeof v.maximized === "boolean"
  );
}

/** Keeps a window inside the viewport and at least its minimum size. */
function fitToViewport(g: WindowGeometry): WindowGeometry {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = clamp(g.w, MIN_W, vw);
  const h = clamp(g.h, MIN_H, vh);
  return { ...g, w, h, x: clamp(g.x, 0, vw - w), y: clamp(g.y, 0, vh - h) };
}

/** Centered, 70% wide and 80% tall, the same first-open size as mentiko's floating panels. */
function defaultGeometry(): WindowGeometry {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = Math.max(MIN_W, vw * 0.7);
  const h = Math.max(MIN_H, vh * 0.8);
  return fitToViewport({ x: (vw - w) / 2, y: (vh - h) / 2, w, h, maximized: false });
}

function loadGeometry(storageKey: string): WindowGeometry {
  try {
    const raw = window.localStorage.getItem(storageKey);
    const parsed: unknown = raw === null ? null : JSON.parse(raw);
    if (isWindowGeometry(parsed)) return fitToViewport(parsed);
  } catch {
    // corrupt entry or storage blocked: fall through to the default
  }
  return defaultGeometry();
}

function saveGeometry(storageKey: string, geometry: WindowGeometry): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(geometry));
  } catch {
    // storage blocked or full: the window still works, it just forgets its place
  }
}

function resizeGeometry(start: WindowGeometry, dx: number, dy: number, edges: ResizeEdges): WindowGeometry {
  let x = start.x;
  let y = start.y;
  let w = start.w;
  let h = start.h;
  if (edges.right) w = Math.max(MIN_W, start.w + dx);
  if (edges.bottom) h = Math.max(MIN_H, start.h + dy);
  if (edges.left) {
    // the right edge stays put until the window reaches its minimum width
    w = Math.max(MIN_W, start.w - dx);
    x = start.x + (start.w - w);
  }
  if (edges.top) {
    h = Math.max(MIN_H, start.h - dy);
    y = start.y + (start.h - h);
  }
  return { ...start, x, y, w, h };
}

/** Listens to the pointer across the whole document. Returns a function that removes the listeners. */
function followPointer(onMove: (ev: MouseEvent) => void, onRelease: () => void): () => void {
  function stop(): void {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", release);
  }
  function release(): void {
    stop();
    onRelease();
  }
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", release);
  return stop;
}

function useMobileWindow(): boolean {
  const [mobile, setMobile] = useState(() => window.matchMedia(MOBILE_QUERY).matches);
  useEffect(() => {
    const query = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setMobile(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

/**
 * Floating window chrome: a title bar (drag, maximize, close), a body, and 8 resize handles.
 * Escape closes it while focus is inside the window. At 640px wide or less it fills the screen.
 */
export function FloatingWindowManager({
  storageKey,
  zIndex,
  title,
  onClose,
  children,
  dataAttr,
}: FloatingWindowManagerProps) {
  const compact = useMobileWindow();
  const [geometry, setGeometry] = useState<WindowGeometry>(() => loadGeometry(storageKey));
  const rootRef = useRef<HTMLDivElement>(null);
  const stopFollowRef = useRef<(() => void) | null>(null);

  // a drag or resize still in flight is dropped when the window unmounts
  useEffect(() => () => stopFollowRef.current?.(), []);

  const follow = (onMove: (ev: MouseEvent) => void, onRelease: () => void) => {
    stopFollowRef.current?.();
    stopFollowRef.current = followPointer(onMove, onRelease);
  };

  const fill = compact || geometry.maximized;

  const beginDrag = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (fill || (e.target as HTMLElement).closest("button, input, [role=button]")) return;
    e.preventDefault();
    rootRef.current?.focus({ preventScroll: true });
    const startX = e.clientX;
    const startY = e.clientY;
    const start = geometry;
    let latest = start;
    follow(
      (ev) => {
        latest = { ...start, x: start.x + ev.clientX - startX, y: start.y + ev.clientY - startY };
        setGeometry(latest);
      },
      () => saveGeometry(storageKey, latest)
    );
  };

  const beginResize = (e: ReactMouseEvent<HTMLDivElement>, edges: ResizeEdges) => {
    e.preventDefault();
    rootRef.current?.focus({ preventScroll: true });
    const startX = e.clientX;
    const startY = e.clientY;
    const start = geometry;
    let latest = start;
    follow(
      (ev) => {
        latest = resizeGeometry(start, ev.clientX - startX, ev.clientY - startY, edges);
        setGeometry(latest);
      },
      () => saveGeometry(storageKey, latest)
    );
  };

  const toggleMaximized = () => {
    const next = { ...geometry, maximized: !geometry.maximized };
    setGeometry(next);
    saveGeometry(storageKey, next);
  };

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    // a key the content already used (terminal, editor) does not close the window
    if (e.key === "Escape" && !e.defaultPrevented) onClose();
  };

  const maximizeLabel = geometry.maximized ? "Restore window" : "Maximize window";

  return (
    <div
      {...dataAttr}
      ref={rootRef}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
      className={cn(
        "fixed flex min-h-0 flex-col overflow-hidden bg-background shadow-2xl ring-1 ring-foreground/10 outline-none",
        fill ? "rounded-none" : "rounded-lg"
      )}
      style={
        fill
          ? { zIndex, left: 0, top: 0, width: "100%", height: "100%" }
          : { zIndex, left: geometry.x, top: geometry.y, width: geometry.w, height: geometry.h }
      }
    >
      <div
        onMouseDown={beginDrag}
        className={cn(
          "flex shrink-0 items-center gap-2 bg-muted/50 px-3 py-1.5 select-none",
          !fill && "cursor-grab active:cursor-grabbing"
        )}
      >
        <div className="min-w-0 flex-1 truncate text-sm font-medium">{title}</div>
        {!compact && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={maximizeLabel}
            title={maximizeLabel}
            onClick={toggleMaximized}
          >
            {geometry.maximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label="Close window"
          title="Close (Esc)"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>

      {!fill &&
        RESIZE_HANDLES.map((handle) => (
          <div
            key={handle.key}
            className={handle.className}
            onMouseDown={(e) => beginResize(e, handle.edges)}
          />
        ))}
    </div>
  );
}
