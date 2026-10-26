/**
 * A lifted sidebar row is swapped for a clone in the page body, so the element the
 * finger went down on is unmounted. The browser keeps sending that touch to the
 * unmounted element, which has no path to the window: the drag library never sees the
 * move or the release, the row hangs lifted and the sidebar scrolls instead. This hands
 * those events to the window, and cancels the native ones the library cancels.
 */
const HANDLE = "[data-rfd-drag-handle-draggable-id]";
const FOLLOWED = ["touchmove", "touchend", "touchcancel"] as const;

type Point = Record<string, unknown>;
type NativeTouchEvent = Event & { touches?: ArrayLike<Point>; targetTouches?: ArrayLike<Point>; changedTouches?: ArrayLike<Point> };
type Source = EventTarget & { isConnected: boolean; closest?: (selector: string) => unknown };
type Win = Pick<EventTarget, "addEventListener" | "removeEventListener" | "dispatchEvent">;

function relayed(native: NativeTouchEvent): Event {
  const copy = new Event(native.type, { bubbles: true, cancelable: true });
  for (const list of ["touches", "targetTouches", "changedTouches"] as const) {
    const points = Array.from(native[list] ?? [], (touch) => ({ ...touch, clientX: touch.clientX, clientY: touch.clientY, force: touch.force ?? 0 }));
    Object.defineProperty(copy, list, { value: points });
  }
  return copy;
}

export function relayDetachedTouches(win: Win): () => void {
  const onStart = (start: Event) => {
    const target = start.target as Source | null;
    if (!target || typeof target.closest !== "function" || !target.closest(HANDLE)) return;
    const follow = (native: Event) => {
      if (target.isConnected) return; // the window gets it as it is
      const copy = relayed(native as NativeTouchEvent);
      win.dispatchEvent(copy);
      if (copy.defaultPrevented && native.cancelable) native.preventDefault();
      if (native.type !== "touchmove") stop();
    };
    const stop = () => FOLLOWED.forEach((type) => target.removeEventListener(type, follow));
    FOLLOWED.forEach((type) => target.addEventListener(type, follow, { passive: false }));
  };
  win.addEventListener("touchstart", onStart, { capture: true, passive: true });
  return () => win.removeEventListener("touchstart", onStart, { capture: true });
}
