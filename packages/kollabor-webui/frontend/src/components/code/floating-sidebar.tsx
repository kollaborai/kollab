import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { cn } from "@/lib/utils";

// ─── constants ──────────────────────────────────────────────

const WIDTH_KEY = "kollab:code-sidebar-width:v1";
const DEFAULT_WIDTH = 240;
const MIN_WIDTH = 180;
const MAX_WIDTH = 420;
// one arrow-key press on the divider moves it this many pixels
const KEY_STEP = 8;
// below 640px the sidebar can be hidden behind a toggle in the code window's title bar
const NARROW_QUERY = "(max-width: 639.98px)";

// ─── width (persisted per browser) ──────────────────────────

function clampWidth(value: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(value)));
}

function loadWidth(): number {
  try {
    const raw = window.localStorage.getItem(WIDTH_KEY);
    if (raw === null) return DEFAULT_WIDTH;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? clampWidth(parsed) : DEFAULT_WIDTH;
  } catch {
    // unreadable storage: use the default width
    return DEFAULT_WIDTH;
  }
}

function saveWidth(width: number): void {
  try {
    window.localStorage.setItem(WIDTH_KEY, String(width));
  } catch {
    // storage blocked or full: the width is not remembered
  }
}

// ─── visibility (hidden only below 640px) ───────────────────

/**
 * Whether the files sidebar shows. At 640px and wider it always shows. Below 640px it starts hidden,
 * and `toggle` shows or hides it. The caller renders the toggle in the window's title bar when `narrow`.
 */
export function useFileSidebarVisibility(): { narrow: boolean; visible: boolean; toggle: () => void } {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW_QUERY).matches);
  const [hidden, setHidden] = useState(true);

  useEffect(() => {
    const query = window.matchMedia(NARROW_QUERY);
    const onChange = () => setNarrow(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return {
    narrow,
    visible: !narrow || !hidden,
    toggle: () => setHidden((value) => !value),
  };
}

// ─── component ──────────────────────────────────────────────

interface FloatingFileSidebarProps {
  /** false hides the column. It stays mounted, so the tree, search results and git state survive a hide. */
  visible: boolean;
  /** true below 640px: the column floats over the editor instead of sitting beside it. */
  narrow?: boolean;
  children: ReactNode;
}

interface DragState {
  startX: number;
  startWidth: number;
  latest: number;
}

/**
 * The files, search and git column inside the code window. A flex child beside the editor, the full height
 * of the window body, with a divider on its right edge that drags the width between 180px and 420px.
 */
export function FloatingFileSidebar({ visible, narrow = false, children }: FloatingFileSidebarProps) {
  const [width, setWidth] = useState(loadWidth);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<DragState | null>(null);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // capture is best-effort: the drag still works while the pointer stays over the divider
    }
    drag.current = { startX: e.clientX, startWidth: width, latest: width };
    setDragging(true);
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current) return;
    current.latest = clampWidth(current.startWidth + e.clientX - current.startX);
    setWidth(current.latest);
  };

  const endDrag = () => {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    setDragging(false);
    saveWidth(current.latest);
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next = clampWidth(width + (e.key === "ArrowRight" ? KEY_STEP : -KEY_STEP));
    setWidth(next);
    saveWidth(next);
  };

  // narrow: the column floats over the editor (the toggle opens and closes it), so the editor keeps its width
  return (
    <div
      className={cn(
        "min-h-0 shrink-0",
        visible ? "flex" : "hidden",
        narrow ? "absolute inset-y-2 left-2 z-20 shadow-xl" : "relative",
      )}
      style={{ width: narrow ? Math.min(width, 280) : width }}
    >
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg bg-sidebar text-sidebar-foreground">
        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">{children}</div>
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize files sidebar"
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        aria-valuenow={width}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
        className={cn(
          "absolute inset-y-0 -right-2 z-10 w-2 cursor-col-resize touch-none select-none outline-none hover:bg-muted focus-visible:bg-muted",
          dragging && "bg-muted",
        )}
      />
    </div>
  );
}
