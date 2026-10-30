import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { PanelLeftIcon } from "lucide-react"
import { Slot } from "radix-ui"

import { SIDEBAR_BREAKPOINT, useIsMobile } from "@/hooks/use-mobile"
import { cn } from "@/lib/utils"
import {
  clampSidebarWidth,
  draggedSidebarWidth,
  keyboardSidebarWidth,
  sidebarWidthBounds,
  sidebarWidthStyle,
  SIDEBAR_WIDTH_STORAGE_KEY,
  storedSidebarWidth,
} from "./sidebar-width"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const SIDEBAR_COOKIE_NAME = "sidebar_state"
const SIDEBAR_COOKIE_MAX_AGE = 60 * 60 * 24 * 7
const SIDEBAR_WIDTH = "16rem"
const SIDEBAR_WIDTH_MOBILE = "18rem"
const SIDEBAR_WIDTH_ICON = "3rem"
const SIDEBAR_KEYBOARD_SHORTCUT = "b"

type SidebarContextProps = {
  state: "expanded" | "collapsed"
  open: boolean
  setOpen: (open: boolean) => void
  openMobile: boolean
  setOpenMobile: (open: boolean) => void
  isMobile: boolean
  toggleSidebar: () => void
  setSidebarWidth: (width: number, persist?: boolean) => void
  getSidebarWidth: (measuredWidth: number) => number
  isResizing: boolean
  setIsResizing: (resizing: boolean) => void
  setShellHeaderHeight: (height: number | null) => void
}

const SidebarContext = React.createContext<SidebarContextProps | null>(null)

function useSidebar() {
  const context = React.useContext(SidebarContext)
  if (!context) {
    throw new Error("useSidebar must be used within a SidebarProvider.")
  }

  return context
}

function SidebarProvider({
  defaultOpen = true,
  open: openProp,
  onOpenChange: setOpenProp,
  className,
  style,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
}) {
  const isMobile = useIsMobile(SIDEBAR_BREAKPOINT)
  const [openMobile, setOpenMobile] = React.useState(false)
  const styleWidth = (style as Record<string, string | number> | undefined)?.["--sidebar-width"]
  const [savedWidth, setSavedWidth] = React.useState<number | null>(() => {
    try {
      return storedSidebarWidth(window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY))
    } catch {
      return null
    }
  })
  const [resizedWidth, setResizedWidth] = React.useState<{ initial: typeof styleWidth; width: number } | null>(null)
  const requestedWidth = React.useRef<typeof resizedWidth>(null)
  const [isResizing, setIsResizing] = React.useState(false)
  const [shellHeaderHeight, setShellHeaderHeight] = React.useState<number | null>(null)
  const preferredWidth = resizedWidth && resizedWidth.initial === styleWidth
    ? resizedWidth.width
    : styleWidth ?? savedWidth ?? SIDEBAR_WIDTH
  const setSidebarWidth = React.useCallback((width: number, persist = true) => {
    const bounded = clampSidebarWidth(width, window.innerWidth)
    requestedWidth.current = { initial: styleWidth, width: bounded }
    setSavedWidth(bounded)
    setResizedWidth(requestedWidth.current)
    if (persist) {
      try {
        window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(bounded))
      } catch {
        // Resizing still works when device storage is unavailable.
      }
    }
  }, [styleWidth])
  const getSidebarWidth = React.useCallback((measuredWidth: number) => {
    // Repeated keys must use the requested width, not a CSS transition frame.
    const requested = requestedWidth.current
    return clampSidebarWidth(requested && requested.initial === styleWidth
      ? requested.width : measuredWidth, window.innerWidth)
  }, [styleWidth])

  // This is the internal state of the sidebar.
  // We use openProp and setOpenProp for control from outside the component.
  const [_open, _setOpen] = React.useState(defaultOpen)
  const open = openProp ?? _open
  const setOpen = React.useCallback(
    (value: boolean | ((value: boolean) => boolean)) => {
      const openState = typeof value === "function" ? value(open) : value
      if (setOpenProp) {
        setOpenProp(openState)
      } else {
        _setOpen(openState)
      }

      // This sets the cookie to keep the sidebar state.
      document.cookie = `${SIDEBAR_COOKIE_NAME}=${openState}; path=/; max-age=${SIDEBAR_COOKIE_MAX_AGE}`
    },
    [setOpenProp, open]
  )

  // Helper to toggle the sidebar.
  const toggleSidebar = React.useCallback(() => {
    return isMobile ? setOpenMobile((open) => !open) : setOpen((open) => !open)
  }, [isMobile, setOpen, setOpenMobile])

  // Adds a keyboard shortcut to toggle the sidebar.
  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.key === SIDEBAR_KEYBOARD_SHORTCUT &&
        (event.metaKey || event.ctrlKey)
      ) {
        event.preventDefault()
        toggleSidebar()
      }
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [toggleSidebar])

  // We add a state so that we can do data-state="expanded" or "collapsed".
  // This makes it easier to style the sidebar with Tailwind classes.
  const state = open ? "expanded" : "collapsed"

  const contextValue = React.useMemo<SidebarContextProps>(
    () => ({
      state,
      open,
      setOpen,
      isMobile,
      openMobile,
      setOpenMobile,
      toggleSidebar,
      setSidebarWidth,
      getSidebarWidth,
      isResizing,
      setIsResizing,
      setShellHeaderHeight,
    }),
    [state, open, setOpen, isMobile, openMobile, setOpenMobile, toggleSidebar, setSidebarWidth, getSidebarWidth, isResizing]
  )

  return (
    <SidebarContext.Provider value={contextValue}>
      <TooltipProvider delayDuration={0}>
        <div
          data-slot="sidebar-wrapper"
          data-resizing={isResizing || undefined}
          style={
            {
              "--sidebar-width-icon": SIDEBAR_WIDTH_ICON,
              "--shell-header-height": "3rem",
              ...style,
              "--sidebar-width": sidebarWidthStyle(preferredWidth),
              ...(shellHeaderHeight === null ? {} : { "--shell-header-height": `${shellHeaderHeight}px` }),
            } as React.CSSProperties
          }
          className={cn(
            "group/sidebar-wrapper flex min-h-svh w-full has-data-[variant=inset]:bg-sidebar",
            className
          )}
          {...props}
        >
          {children}
        </div>
      </TooltipProvider>
    </SidebarContext.Provider>
  )
}

function Sidebar({
  side = "left",
  variant = "sidebar",
  collapsible = "offcanvas",
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  side?: "left" | "right"
  variant?: "sidebar" | "floating" | "inset"
  collapsible?: "offcanvas" | "icon" | "none"
}) {
  const { isMobile, state, openMobile, setOpenMobile, isResizing } = useSidebar()
  const sidebarId = React.useId()

  if (collapsible === "none") {
    return (
      <div
        data-slot="sidebar"
        className={cn(
          "flex h-full w-(--sidebar-width) flex-col bg-sidebar text-sidebar-foreground",
          className
        )}
        {...props}
      >
        {children}
      </div>
    )
  }

  if (isMobile) {
    return (
      <Sheet open={openMobile} onOpenChange={setOpenMobile} {...props}>
        <SheetContent
          data-sidebar="sidebar"
          data-slot="sidebar"
          data-mobile="true"
          className={cn(
            "w-(--sidebar-width) bg-sidebar p-0 text-sidebar-foreground [&>button]:hidden",
            variant === "floating" && cn("inset-y-2 h-auto overflow-hidden rounded-xl shadow-lg", side === "left" ? "left-2" : "right-2")
          )}
          style={
            {
              "--sidebar-width": SIDEBAR_WIDTH_MOBILE,
            } as React.CSSProperties
          }
          side={side}
          // Focusing the first control on open popped its tooltip (New
          // Session), and the first Escape closed the tooltip, not the sheet.
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          <SheetHeader className="sr-only">
            <SheetTitle>Sidebar</SheetTitle>
            <SheetDescription>Displays the mobile sidebar.</SheetDescription>
          </SheetHeader>
          <div className="flex h-full w-full flex-col">{children}</div>
        </SheetContent>
      </Sheet>
    )
  }

  return (
    <div
      className="group peer hidden text-sidebar-foreground md:block"
      data-state={state}
      data-collapsible={state === "collapsed" ? collapsible : ""}
      data-variant={variant}
      data-side={side}
      data-slot="sidebar"
    >
      {/* This is what handles the sidebar gap on desktop */}
      <div
        data-slot="sidebar-gap"
        className={cn(
          "relative w-(--sidebar-width) bg-transparent transition-[width] duration-200 ease-linear",
          "group-data-[collapsible=offcanvas]:w-0",
          "group-data-[side=right]:rotate-180",
          variant === "floating" || variant === "inset"
            ? "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4)))]"
            : "group-data-[collapsible=icon]:w-(--sidebar-width-icon)",
          isResizing && "transition-none"
        )}
      />
      <div
        id={sidebarId}
        data-slot="sidebar-container"
        className={cn(
          "fixed inset-y-0 z-10 hidden h-svh w-(--sidebar-width) transition-[left,right,width] duration-200 ease-linear md:flex",
          side === "left"
            ? "left-0 group-data-[collapsible=offcanvas]:left-[calc(var(--sidebar-width)*-1)]"
            : "right-0 group-data-[collapsible=offcanvas]:right-[calc(var(--sidebar-width)*-1)]",
          // Adjust the padding for floating and inset variants.
          variant === "floating" || variant === "inset"
            ? "p-2 group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4))+2px)]"
            : "group-data-[collapsible=icon]:w-(--sidebar-width-icon)",
          className,
          isResizing && "transition-none"
        )}
        {...props}
      >
        <div
          data-sidebar="sidebar"
          data-slot="sidebar-inner"
          className="flex h-full w-full flex-col bg-sidebar group-data-[variant=floating]:overflow-hidden group-data-[variant=floating]:rounded-xl group-data-[variant=floating]:border group-data-[variant=floating]:border-sidebar-border group-data-[variant=floating]:shadow-lg"
        >
          {children}
        </div>
      </div>
    </div>
  )
}

function SidebarTrigger({
  className,
  onClick,
  ...props
}: React.ComponentProps<typeof Button>) {
  const { toggleSidebar } = useSidebar()

  return (
    <Button
      data-sidebar="trigger"
      data-slot="sidebar-trigger"
      variant="ghost"
      size="icon"
      className={cn("size-7", className)}
      onClick={(event) => {
        onClick?.(event)
        toggleSidebar()
      }}
      {...props}
    >
      <PanelLeftIcon />
      <span className="sr-only">Toggle Sidebar</span>
    </Button>
  )
}

function SidebarRail({ className, ref: forwardedRef, onPointerDown, onPointerMove, onPointerUp,
  onPointerCancel, onLostPointerCapture, onKeyDown, onClick, ...props }: React.ComponentProps<"button">) {
  const { open, setOpen, isMobile, setSidebarWidth, getSidebarWidth, setIsResizing } = useSidebar()
  const railRef = React.useRef<HTMLButtonElement | null>(null)
  const suppressClick = React.useRef(false)
  const setRailRef = React.useCallback((node: HTMLButtonElement | null) => {
    railRef.current = node
    if (typeof forwardedRef === "function") return forwardedRef(node)
    if (forwardedRef) forwardedRef.current = node
  }, [forwardedRef])
  const [panelId, setPanelId] = React.useState<string>()
  const [width, setWidth] = React.useState(256)
  const [viewport, setViewport] = React.useState(() => typeof window === "undefined" ? 1280 : window.innerWidth)
  const drag = React.useRef<{
    target: HTMLButtonElement; pointerId: number; startX: number; startWidth: number;
    width: number; side: "left" | "right"; moved: boolean; restore: () => void;
  } | null>(null)
  const finishDrag = React.useCallback(() => {
    const held = drag.current
    if (!held) return
    drag.current = null
    suppressClick.current = held.moved
    held.restore()
    setIsResizing(false)
    if (held.moved) setSidebarWidth(held.width)
    if (held.target.hasPointerCapture(held.pointerId)) held.target.releasePointerCapture(held.pointerId)
  }, [setIsResizing, setSidebarWidth])

  React.useEffect(() => {
    window.addEventListener("blur", finishDrag)
    return () => {
      window.removeEventListener("blur", finishDrag)
      finishDrag()
    }
  }, [finishDrag])
  React.useEffect(() => {
    if (!open || isMobile) finishDrag()
    const panel = railRef.current?.closest<HTMLElement>('[data-slot="sidebar-container"]')
    if (isMobile || !panel) return
    const measure = () => {
      setPanelId(panel.id || undefined)
      if (open) setWidth(Math.round(panel.getBoundingClientRect().width))
      setViewport(window.innerWidth)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(panel)
    window.addEventListener("resize", measure)
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", measure)
    }
  }, [open, isMobile, finishDrag])

  if (isMobile) return null
  const bounds = sidebarWidthBounds(viewport)
  const panelSide = (target: HTMLElement): "left" | "right" =>
    target.closest('[data-side="right"]') ? "right" : "left"

  return (
    <button
      {...props}
      ref={setRailRef}
      type="button"
      data-sidebar="rail"
      data-slot="sidebar-rail"
      role={open ? "separator" : undefined}
      aria-label={open ? "Resize Sidebar" : "Expand Sidebar"}
      aria-controls={panelId}
      aria-orientation={open ? "vertical" : undefined}
      aria-valuemin={open ? bounds.min : undefined}
      aria-valuemax={open ? bounds.max : undefined}
      aria-valuenow={open ? clampSidebarWidth(width, viewport) : undefined}
      aria-valuetext={open ? `${width} pixels` : undefined}
      tabIndex={0}
      onClick={(event) => {
        onClick?.(event)
        if (suppressClick.current && event.detail !== 0) return
        if (!event.defaultPrevented && !open) setOpen(true)
      }}
      onPointerDown={(event) => {
        onPointerDown?.(event)
        suppressClick.current = false
        if (event.defaultPrevented || !open || event.button !== 0 || drag.current) return
        const panel = event.currentTarget.closest<HTMLElement>('[data-slot="sidebar-container"]')
        if (!panel) return
        event.preventDefault()
        event.currentTarget.focus({ preventScroll: true })
        event.currentTarget.setPointerCapture(event.pointerId)
        const body = document.body.style
        const previous = ["user-select", "cursor"].map(name => ({ name,
          value: body.getPropertyValue(name), priority: body.getPropertyPriority(name) }))
        const startWidth = panel.getBoundingClientRect().width
        drag.current = { target: event.currentTarget, pointerId: event.pointerId,
          startX: event.clientX, startWidth, width: startWidth, side: panelSide(event.currentTarget), moved: false,
          restore: () => previous.forEach(({ name, value, priority }) => {
            if (value) body.setProperty(name, value, priority)
            else body.removeProperty(name)
          }) }
        body.setProperty("user-select", "none", "important")
        body.setProperty("cursor", "col-resize", "important")
        setIsResizing(true)
      }}
      onPointerMove={(event) => {
        onPointerMove?.(event)
        const held = drag.current
        if (!held || held.pointerId !== event.pointerId) return
        held.width = draggedSidebarWidth(held.startWidth, event.clientX - held.startX, held.side, window.innerWidth)
        held.moved ||= Math.abs(event.clientX - held.startX) > 2
        setSidebarWidth(held.width, false)
      }}
      onPointerUp={(event) => {
        onPointerUp?.(event)
        if (drag.current?.pointerId === event.pointerId) finishDrag()
      }}
      onPointerCancel={(event) => {
        onPointerCancel?.(event)
        if (drag.current?.pointerId === event.pointerId) finishDrag()
      }}
      onLostPointerCapture={(event) => {
        onLostPointerCapture?.(event)
        if (drag.current?.pointerId === event.pointerId) finishDrag()
      }}
      onKeyDown={(event) => {
        onKeyDown?.(event)
        if (event.defaultPrevented || !open || event.altKey || event.ctrlKey || event.metaKey) return
        const panel = event.currentTarget.closest<HTMLElement>('[data-slot="sidebar-container"]')
        const next = keyboardSidebarWidth(event.key, getSidebarWidth(panel?.getBoundingClientRect().width ?? width),
          panelSide(event.currentTarget), window.innerWidth, event.shiftKey)
        if (next === null) return
        event.preventDefault()
        setSidebarWidth(next)
      }}
      title={open ? "Drag or use arrow keys to resize Sidebar" : "Expand Sidebar"}
      className={cn(
        "absolute inset-y-0 z-20 hidden w-4 -translate-x-1/2 touch-none transition-colors ease-linear group-data-[side=left]:-right-4 group-data-[side=right]:left-0 after:absolute after:inset-y-0 after:left-1/2 after:w-[2px] hover:after:bg-sidebar-ring [[data-resizing]_&]:after:bg-sidebar-ring focus-visible:outline-none focus-visible:after:bg-ring md:flex",
        // A floating sidebar's edge is the gap beside its card: the line sits in it.
        "[[data-side=left][data-variant=floating][data-state=expanded]_&]:-right-3 [[data-side=right][data-variant=floating][data-state=expanded]_&]:left-1",
        open && "cursor-col-resize",
        "[[data-side=left][data-state=collapsed]_&]:cursor-e-resize [[data-side=right][data-state=collapsed]_&]:cursor-w-resize",
        "group-data-[collapsible=offcanvas]:translate-x-0 group-data-[collapsible=offcanvas]:after:left-full hover:group-data-[collapsible=offcanvas]:bg-sidebar",
        "[[data-side=left][data-collapsible=offcanvas]_&]:-right-2",
        "[[data-side=right][data-collapsible=offcanvas]_&]:-left-2",
        className
      )}
    />
  )
}

function SidebarInset({ className, ...props }: React.ComponentProps<"main">) {
  return (
    <main
      data-slot="sidebar-inset"
      className={cn(
        "relative m-2 flex min-w-0 w-full flex-1 flex-col overflow-hidden rounded-xl bg-background md:ml-0",
        "md:peer-data-[variant=inset]:m-2 md:peer-data-[variant=inset]:ml-0 md:peer-data-[variant=inset]:rounded-xl md:peer-data-[variant=inset]:shadow-sm md:peer-data-[variant=inset]:peer-data-[state=collapsed]:ml-2",
        className
      )}
      {...props}
    />
  )
}

function SidebarInput({
  className,
  ...props
}: React.ComponentProps<typeof Input>) {
  return (
    <Input
      data-slot="sidebar-input"
      data-sidebar="input"
      className={cn("h-8 w-full bg-background shadow-none", className)}
      {...props}
    />
  )
}

function SidebarHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-header"
      data-sidebar="header"
      className={cn("flex flex-col gap-2 p-2", className)}
      {...props}
    />
  )
}

function SidebarFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-footer"
      data-sidebar="footer"
      className={cn("flex flex-col gap-2 p-2", className)}
      {...props}
    />
  )
}

function SidebarSeparator({
  className,
  ...props
}: React.ComponentProps<typeof Separator>) {
  return (
    <Separator
      data-slot="sidebar-separator"
      data-sidebar="separator"
      className={cn("mx-2 w-auto bg-sidebar-border", className)}
      {...props}
    />
  )
}

function SidebarContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-content"
      data-sidebar="content"
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-2 overflow-auto group-data-[collapsible=icon]:overflow-hidden",
        className
      )}
      {...props}
    />
  )
}

function SidebarGroup({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-group"
      data-sidebar="group"
      className={cn("relative flex w-full min-w-0 flex-col p-2", className)}
      {...props}
    />
  )
}

function SidebarGroupLabel({
  className,
  asChild = false,
  ...props
}: React.ComponentProps<"div"> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "div"

  return (
    <Comp
      data-slot="sidebar-group-label"
      data-sidebar="group-label"
      className={cn(
        "flex h-8 shrink-0 items-center rounded-md px-2 text-xs font-medium text-sidebar-foreground/70 ring-sidebar-ring outline-hidden transition-[margin,opacity] duration-200 ease-linear focus-visible:ring-2 [&>svg]:size-4 [&>svg]:shrink-0",
        "group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0",
        className
      )}
      {...props}
    />
  )
}

function SidebarGroupAction({
  className,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "button"

  return (
    <Comp
      data-slot="sidebar-group-action"
      data-sidebar="group-action"
      className={cn(
        "absolute top-3.5 right-3 flex aspect-square w-5 items-center justify-center rounded-md p-0 text-sidebar-foreground ring-sidebar-ring outline-hidden transition-transform hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 [&>svg]:size-4 [&>svg]:shrink-0",
        // Increases the hit area of the button on mobile.
        "after:absolute after:-inset-2 md:after:hidden",
        "group-data-[collapsible=icon]:hidden",
        className
      )}
      {...props}
    />
  )
}

function SidebarGroupContent({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-group-content"
      data-sidebar="group-content"
      className={cn("w-full text-sm", className)}
      {...props}
    />
  )
}

function SidebarMenu({ className, ...props }: React.ComponentProps<"ul">) {
  return (
    <ul
      data-slot="sidebar-menu"
      data-sidebar="menu"
      className={cn("flex w-full min-w-0 flex-col gap-1", className)}
      {...props}
    />
  )
}

function SidebarMenuItem({ className, ...props }: React.ComponentProps<"li">) {
  return (
    <li
      data-slot="sidebar-menu-item"
      data-sidebar="menu-item"
      className={cn("group/menu-item relative", className)}
      {...props}
    />
  )
}

const sidebarMenuButtonVariants = cva(
  "peer/menu-button flex w-full items-center gap-2 overflow-hidden rounded-md p-2 text-left text-sm ring-sidebar-ring outline-hidden transition-[width,height,padding] group-has-data-[sidebar=menu-action]/menu-item:pr-8 group-data-[collapsible=icon]:size-8! group-data-[collapsible=icon]:p-2! hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 active:bg-sidebar-accent active:text-sidebar-accent-foreground disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50 data-[active=true]:bg-sidebar-accent data-[active=true]:font-medium data-[active=true]:text-sidebar-accent-foreground data-[state=open]:hover:bg-sidebar-accent data-[state=open]:hover:text-sidebar-accent-foreground [&>span:last-child]:truncate [&>svg]:size-4 [&>svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
        outline:
          "bg-background shadow-[0_0_0_1px_var(--sidebar-border)] hover:bg-sidebar-accent hover:text-sidebar-accent-foreground hover:shadow-[0_0_0_1px_var(--sidebar-accent)]",
      },
      size: {
        default: "h-8 text-sm",
        sm: "h-7 text-xs",
        lg: "h-12 text-sm group-data-[collapsible=icon]:p-0!",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function SidebarMenuButton({
  asChild = false,
  isActive = false,
  variant = "default",
  size = "default",
  tooltip,
  className,
  ...props
}: React.ComponentProps<"button"> & {
  asChild?: boolean
  isActive?: boolean
  tooltip?: string | React.ComponentProps<typeof TooltipContent>
} & VariantProps<typeof sidebarMenuButtonVariants>) {
  const Comp = asChild ? Slot.Root : "button"
  const { isMobile, state } = useSidebar()

  const button = (
    <Comp
      data-slot="sidebar-menu-button"
      data-sidebar="menu-button"
      data-size={size}
      data-active={isActive}
      className={cn(sidebarMenuButtonVariants({ variant, size }), className)}
      {...props}
    />
  )

  if (!tooltip) {
    return button
  }

  if (typeof tooltip === "string") {
    tooltip = {
      children: tooltip,
    }
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent
        side="right"
        align="center"
        hidden={state !== "collapsed" || isMobile}
        {...tooltip}
      />
    </Tooltip>
  )
}

function SidebarMenuAction({
  className,
  asChild = false,
  showOnHover = false,
  ...props
}: React.ComponentProps<"button"> & {
  asChild?: boolean
  showOnHover?: boolean
}) {
  const Comp = asChild ? Slot.Root : "button"

  return (
    <Comp
      data-slot="sidebar-menu-action"
      data-sidebar="menu-action"
      className={cn(
        "absolute top-1.5 right-1 flex aspect-square w-5 items-center justify-center rounded-md p-0 text-sidebar-foreground ring-sidebar-ring outline-hidden transition-transform peer-hover/menu-button:text-sidebar-accent-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 [&>svg]:size-4 [&>svg]:shrink-0",
        // Increases the hit area of the button on mobile.
        "after:absolute after:-inset-2 md:after:hidden",
        "peer-data-[size=sm]/menu-button:top-1",
        "peer-data-[size=default]/menu-button:top-1.5",
        "peer-data-[size=lg]/menu-button:top-2.5",
        "group-data-[collapsible=icon]:hidden",
        showOnHover &&
          "group-focus-within/menu-item:opacity-100 group-hover/menu-item:opacity-100 peer-data-[active=true]/menu-button:text-sidebar-accent-foreground data-[state=open]:opacity-100 md:opacity-0",
        className
      )}
      {...props}
    />
  )
}

function SidebarMenuBadge({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-menu-badge"
      data-sidebar="menu-badge"
      className={cn(
        "pointer-events-none absolute right-1 flex h-5 min-w-5 items-center justify-center rounded-md px-1 text-xs font-medium text-sidebar-foreground tabular-nums select-none",
        "peer-hover/menu-button:text-sidebar-accent-foreground peer-data-[active=true]/menu-button:text-sidebar-accent-foreground",
        "peer-data-[size=sm]/menu-button:top-1",
        "peer-data-[size=default]/menu-button:top-1.5",
        "peer-data-[size=lg]/menu-button:top-2.5",
        "group-data-[collapsible=icon]:hidden",
        className
      )}
      {...props}
    />
  )
}

function SidebarMenuSkeleton({
  className,
  showIcon = false,
  ...props
}: React.ComponentProps<"div"> & {
  showIcon?: boolean
}) {
  // Random width between 50 to 90%.
  const width = React.useMemo(() => {
    return `${Math.floor(Math.random() * 40) + 50}%`
  }, [])

  return (
    <div
      data-slot="sidebar-menu-skeleton"
      data-sidebar="menu-skeleton"
      className={cn("flex h-8 items-center gap-2 rounded-md px-2", className)}
      {...props}
    >
      {showIcon && (
        <Skeleton
          className="size-4 rounded-md"
          data-sidebar="menu-skeleton-icon"
        />
      )}
      <Skeleton
        className="h-4 max-w-(--skeleton-width) flex-1"
        data-sidebar="menu-skeleton-text"
        style={
          {
            "--skeleton-width": width,
          } as React.CSSProperties
        }
      />
    </div>
  )
}

function SidebarMenuSub({ className, ...props }: React.ComponentProps<"ul">) {
  return (
    <ul
      data-slot="sidebar-menu-sub"
      data-sidebar="menu-sub"
      className={cn(
        "mx-3.5 flex min-w-0 translate-x-px flex-col gap-1 border-l border-sidebar-border px-2.5 py-0.5",
        "group-data-[collapsible=icon]:hidden",
        className
      )}
      {...props}
    />
  )
}

function SidebarMenuSubItem({
  className,
  ...props
}: React.ComponentProps<"li">) {
  return (
    <li
      data-slot="sidebar-menu-sub-item"
      data-sidebar="menu-sub-item"
      className={cn("group/menu-sub-item relative", className)}
      {...props}
    />
  )
}

function SidebarMenuSubButton({
  asChild = false,
  size = "md",
  isActive = false,
  className,
  ...props
}: React.ComponentProps<"a"> & {
  asChild?: boolean
  size?: "sm" | "md"
  isActive?: boolean
}) {
  const Comp = asChild ? Slot.Root : "a"

  return (
    <Comp
      data-slot="sidebar-menu-sub-button"
      data-sidebar="menu-sub-button"
      data-size={size}
      data-active={isActive}
      className={cn(
        "flex h-7 min-w-0 -translate-x-px items-center gap-2 overflow-hidden rounded-md px-2 text-sidebar-foreground ring-sidebar-ring outline-hidden hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 active:bg-sidebar-accent active:text-sidebar-accent-foreground disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50 [&>span:last-child]:truncate [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-sidebar-accent-foreground",
        "data-[active=true]:bg-sidebar-accent data-[active=true]:text-sidebar-accent-foreground",
        size === "sm" && "text-xs",
        size === "md" && "text-sm",
        "group-data-[collapsible=icon]:hidden",
        className
      )}
      {...props}
    />
  )
}

export {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInput,
  SidebarInset,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarRail,
  SidebarSeparator,
  SidebarTrigger,
  useSidebar,
}
