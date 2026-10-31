import * as React from "react"

const MOBILE_BREAKPOINT = 768

// #458: both shell sidebars (sessions sidebar and the session panel) render as
// drawers below this width instead of squeezing the chat and Trajectory.
export const SIDEBAR_BREAKPOINT = 1024

/** True when both sidebars use the drawer treatment at this viewport width. */
export function sidebarIsDrawer(width: number): boolean {
  return width < SIDEBAR_BREAKPOINT
}

export function useIsMobile(breakpoint = MOBILE_BREAKPOINT) {
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(undefined)

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${breakpoint - 1}px)`)
    const onChange = () => {
      setIsMobile(window.innerWidth < breakpoint)
    }
    mql.addEventListener("change", onChange)
    setIsMobile(window.innerWidth < breakpoint)
    return () => mql.removeEventListener("change", onChange)
  }, [breakpoint])

  return !!isMobile
}
