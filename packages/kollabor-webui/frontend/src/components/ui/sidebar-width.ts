export const SIDEBAR_WIDTH_STORAGE_KEY = "kollab.sidebar.width"
export const SIDEBAR_MIN_WIDTH = 220
export const SIDEBAR_MAX_WIDTH = 480
const MIN_CHAT_WIDTH = 320

export function sidebarWidthBounds(viewportWidth: number) {
  return {
    min: SIDEBAR_MIN_WIDTH,
    max: Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, viewportWidth - MIN_CHAT_WIDTH)),
  }
}

export function clampSidebarWidth(width: number, viewportWidth: number) {
  const { min, max } = sidebarWidthBounds(viewportWidth)
  return Math.round(Math.max(min, Math.min(max, width)))
}

export function storedSidebarWidth(value: string | null): number | null {
  if (!value?.trim()) return null
  const width = Number(value)
  return Number.isFinite(width) && width >= SIDEBAR_MIN_WIDTH && width <= SIDEBAR_MAX_WIDTH
    ? width
    : null
}

/** Keep rem/calc caller defaults, bounded by the room left for the chat. */
export function sidebarWidthStyle(preferred: string | number) {
  const width = typeof preferred === "number" ? `${preferred}px` : preferred
  return `clamp(${SIDEBAR_MIN_WIDTH}px, ${width}, min(${SIDEBAR_MAX_WIDTH}px, max(${SIDEBAR_MIN_WIDTH}px, calc(100vw - ${MIN_CHAT_WIDTH}px))))`
}

export function draggedSidebarWidth(startWidth: number, deltaX: number, side: "left" | "right", viewportWidth: number) {
  return clampSidebarWidth(startWidth + deltaX * (side === "left" ? 1 : -1), viewportWidth)
}

export function keyboardSidebarWidth(key: string, width: number, side: "left" | "right", viewportWidth: number, largeStep = false) {
  const bounds = sidebarWidthBounds(viewportWidth)
  if (key === "Home") return bounds.min
  if (key === "End") return bounds.max
  if (key !== "ArrowLeft" && key !== "ArrowRight") return null
  return draggedSidebarWidth(width, (key === "ArrowRight" ? 1 : -1) * (largeStep ? 32 : 8), side, viewportWidth)
}
