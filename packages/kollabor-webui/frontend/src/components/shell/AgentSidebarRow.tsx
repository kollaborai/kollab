import { Children, cloneElement, createContext, isValidElement, useContext, useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { DragDropContext, Draggable, Droppable, type DraggableProvided, type DraggableStateSnapshot } from "@hello-pangea/dnd";
import { EyeOff, MoreHorizontal, SlidersHorizontal, Trash2 } from "lucide-react";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from "@/components/ui/context-menu";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { SidebarMenu, SidebarMenuAction, SidebarMenuItem } from "@/components/ui/sidebar";
import { hiddenAgentTransform, outsideSidebar, sidebarDropAction } from "./sidebar-drag";
import { cn } from "@/lib/utils";

export type AgentRowDrag = { id: string; index: number; disabled?: boolean };
type AgentSidebarRowProps = {
  name: string;
  children: ReactElement<{ className?: string; disabled?: boolean }>;
  onHide: () => void | Promise<void>;
  onProperties?: () => void;
  onDelete?: () => void;
  drag: AgentRowDrag;
  busy?: boolean;
};
const DragOutContext = createContext({ outside: false, hiding: new Set<string>() });

function rowById(children: ReactNode, id: string) {
  return Children.toArray(children).find((child): child is ReactElement<AgentSidebarRowProps> =>
    isValidElement<AgentSidebarRowProps>(child) && child.type === AgentSidebarRow && child.props.drag.id === id);
}

/** Each workspace owns its drop zone, so sorting cannot move an agent to another folder/device. */
export function AgentSidebarList({ id, children, onReorder, beginDrag }: {
  id: string;
  children: ReactNode;
  onReorder: (key: string, to: number) => void;
  beginDrag: () => () => void;
}) {
  const listRef = useRef<HTMLUListElement | null>(null);
  const activeRef = useRef<{
    row: ReactElement<AgentSidebarRowProps> | undefined;
    outside: boolean;
    released: boolean;
  } | null>(null);
  const releaseRef = useRef<(() => void) | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const [outside, setOutside] = useState(false);
  const [hiding, setHiding] = useState(new Set<string>());
  const release = () => {
    cleanupRef.current?.();
    cleanupRef.current = null;
    releaseRef.current?.();
    releaseRef.current = null;
    activeRef.current = null;
    setOutside(false);
  };
  useEffect(() => () => {
    cleanupRef.current?.();
    releaseRef.current?.();
  }, []);

  return (
    <DragDropContext
      dragHandleUsageInstructions="To reorder, press Space, use the arrow keys, and press Space to drop. Escape cancels. Drag outside the sidebar to hide, or use Hide Agent in the actions menu. Click or press Enter to open."
      onBeforeCapture={({ draggableId, mode }) => {
        release();
        releaseRef.current = beginDrag();
        activeRef.current = { row: rowById(children, draggableId), outside: false, released: false };
        if (mode !== "FLUID") return;
        const sidebar = listRef.current?.closest('[data-sidebar="sidebar"]');
        const move = (event: MouseEvent | TouchEvent) => {
          const active = activeRef.current;
          if (!active || active.released || !sidebar) return;
          const pointer = "touches" in event ? event.touches[0] || event.changedTouches[0] : event;
          if (!pointer) return;
          const next = outsideSidebar(pointer.clientX, sidebar.getBoundingClientRect());
          if (next !== active.outside) {
            active.outside = next;
            setOutside(next);
          }
        };
        const end = (event: MouseEvent | TouchEvent) => {
          move(event);
          if (activeRef.current) activeRef.current.released = true;
        };
        const cancel = () => {
          if (activeRef.current) {
            activeRef.current.outside = false;
            activeRef.current.released = true;
          }
          setOutside(false);
        };
        const key = (event: KeyboardEvent) => { if (event.key === "Escape") cancel(); };
        window.addEventListener("mousemove", move, true);
        window.addEventListener("touchmove", move, { capture: true, passive: true });
        window.addEventListener("mouseup", end, true);
        window.addEventListener("touchend", end, true);
        window.addEventListener("touchcancel", cancel, true);
        window.addEventListener("blur", cancel);
        window.addEventListener("keydown", key, true);
        cleanupRef.current = () => {
          window.removeEventListener("mousemove", move, true);
          window.removeEventListener("touchmove", move, true);
          window.removeEventListener("mouseup", end, true);
          window.removeEventListener("touchend", end, true);
          window.removeEventListener("touchcancel", cancel, true);
          window.removeEventListener("blur", cancel);
          window.removeEventListener("keydown", key, true);
        };
      }}
      onDragEnd={(result, { announce }) => {
        try {
          const action = sidebarDropAction(result, activeRef.current?.outside ?? false);
          const row = rowById(children, result.draggableId);
          if (action?.type === "hide" && row) {
            const key = result.draggableId;
            setHiding((current) => new Set(current).add(key));
            const finish = () => setHiding((current) => {
              const next = new Set(current);
              next.delete(key);
              return next;
            });
            // Keep the gap closed while the engine responds; a failed hide restores the row.
            Promise.resolve().then(row.props.onHide).then(finish, finish);
            announce("Released " + row.props.name + " outside the sidebar. Hidden agents can be restored from Hidden Agents.");
          } else if (action?.type === "reorder") {
            onReorder(result.draggableId, action.index);
          }
        } finally {
          release();
        }
      }}
    >
      <DragOutContext.Provider value={{ outside, hiding }}>
        <Droppable
          droppableId={id}
          renderClone={(provided, snapshot, rubric) => {
            const row = rowById(children, rubric.draggableId) || activeRef.current?.row;
            return row ? <AgentSidebarItem {...row.props} provided={provided} snapshot={snapshot} /> : null;
          }}
        >
          {(provided) => (
            <SidebarMenu ref={(node) => { listRef.current = node; provided.innerRef(node); }} {...provided.droppableProps}>
              {children}
              {provided.placeholder}
            </SidebarMenu>
          )}
        </Droppable>
      </DragOutContext.Provider>
    </DragDropContext>
  );
}

/** The same actions work by mouse, keyboard, right-click and phone tap. */
export function AgentSidebarRow(props: AgentSidebarRowProps) {
  return (
    <Draggable draggableId={props.drag.id} index={props.drag.index} isDragDisabled={props.drag.disabled || props.children.props.disabled} disableInteractiveElementBlocking>
      {(provided, snapshot) => <AgentSidebarItem {...props} provided={provided} snapshot={snapshot} />}
    </Draggable>
  );
}

/** The body clone can leave even the mobile sidebar's clipping/transform container. */
function AgentSidebarItem({ name, children, onHide, onProperties, onDelete, drag, busy, provided, snapshot }: AgentSidebarRowProps & {
  provided: DraggableProvided;
  snapshot: DraggableStateSnapshot;
}) {
  const { outside, hiding } = useContext(DragOutContext);
  const lastTransform = useRef<string | undefined>(undefined);
  if (!snapshot.isDropAnimating) lastTransform.current = provided.draggableProps.style?.transform;
  const poof = outside && snapshot.isDropAnimating;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const duration = reducedMotion ? "1ms" : "220ms";
  const target = poof ? document.querySelector('[data-sidebar-hide-target]')?.getBoundingClientRect() : null;
  const box = provided.draggableProps.style;
  const style = poof ? {
    ...box,
    transform: target && box && "left" in box ? hiddenAgentTransform(box, target) : (lastTransform.current || "") + " scale(0.02)",
    opacity: 0,
    transition: "transform " + duration + " cubic-bezier(0.2, 0.8, 0.2, 1), opacity " + (reducedMotion ? "1ms" : "100ms 120ms") + " ease-out",
  } : provided.draggableProps.style;
  const items = [
    ...(onProperties ? [{ label: "Properties", Icon: SlidersHorizontal, onSelect: onProperties }] : []),
    { label: "Hide Agent", Icon: EyeOff, onSelect: onHide },
  ];
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <SidebarMenuItem
          ref={provided.innerRef}
          {...provided.draggableProps}
          style={style}
          hidden={hiding.has(drag.id)}
          data-dragging={snapshot.isDragging || undefined}
          data-hide-preview={snapshot.isDragging && outside || undefined}
          className={snapshot.isDragging ? "rounded-md bg-sidebar-accent shadow-lg" : undefined}
        >
          {cloneElement(children, {
            ...provided.dragHandleProps,
            className: cn(children.props.className, provided.dragHandleProps && "cursor-grab", snapshot.isDragging && "cursor-grabbing"),
          })}
          {snapshot.isDragging && outside ? (
            <span role="status" className="pointer-events-none absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md">
              Release to hide
            </span>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuAction showOnHover aria-label={`Actions for ${name}`}>
                <MoreHorizontal />
              </SidebarMenuAction>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              {items.map(({ label, Icon, onSelect }) => (
                <DropdownMenuItem key={label} onSelect={onSelect}><Icon />{label}</DropdownMenuItem>
              ))}
              {onDelete ? <><DropdownMenuSeparator /><DropdownMenuItem variant="destructive" disabled={busy} onSelect={onDelete}><Trash2 />Delete Session</DropdownMenuItem></> : null}
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-44">
        {items.map(({ label, Icon, onSelect }) => (
          <ContextMenuItem key={label} onSelect={onSelect}><Icon />{label}</ContextMenuItem>
        ))}
        {onDelete ? <><ContextMenuSeparator /><ContextMenuItem variant="destructive" disabled={busy} onSelect={onDelete}><Trash2 />Delete Session</ContextMenuItem></> : null}
      </ContextMenuContent>
    </ContextMenu>
  );
}
