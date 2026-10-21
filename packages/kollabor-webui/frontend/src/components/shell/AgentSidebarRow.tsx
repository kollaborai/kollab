import { cloneElement, useEffect, useRef, type ReactElement, type ReactNode } from "react";
import { DragDropContext, Draggable, Droppable } from "@hello-pangea/dnd";
import { EyeOff, MoreHorizontal, SlidersHorizontal, Trash2 } from "lucide-react";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from "@/components/ui/context-menu";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { SidebarMenu, SidebarMenuAction, SidebarMenuItem } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

export type AgentRowDrag = { id: string; index: number; disabled?: boolean };

/** Each workspace owns its drop zone, so sorting cannot move an agent to another folder/device. */
export function AgentSidebarList({ id, children, onReorder, beginDrag }: {
  id: string;
  children: ReactNode;
  onReorder: (key: string, to: number) => void;
  beginDrag: () => () => void;
}) {
  const releaseRef = useRef<(() => void) | null>(null);
  const release = () => {
    releaseRef.current?.();
    releaseRef.current = null;
  };
  useEffect(() => () => releaseRef.current?.(), []);
  return (
    <DragDropContext
      dragHandleUsageInstructions="To reorder, press Space, use the arrow keys, and press Space to drop. Escape cancels. Click or press Enter to open."
      onBeforeCapture={() => {
        release();
        releaseRef.current = beginDrag();
      }}
      onDragEnd={({ reason, source, destination, draggableId }) => {
        try {
          if (reason === "DROP" && destination?.droppableId === source.droppableId && destination.index !== source.index) {
            onReorder(draggableId, destination.index);
          }
        } finally {
          release();
        }
      }}
    >
      <Droppable droppableId={id}>
        {(provided) => (
          <SidebarMenu ref={provided.innerRef} {...provided.droppableProps}>
            {children}
            {provided.placeholder}
          </SidebarMenu>
        )}
      </Droppable>
    </DragDropContext>
  );
}

/** The same actions work by mouse, keyboard, right-click and phone tap. */
export function AgentSidebarRow({ name, children, onHide, onProperties, onDelete, drag, busy }: {
  name: string;
  children: ReactElement<{ className?: string; disabled?: boolean }>;
  onHide: () => void;
  onProperties?: () => void;
  onDelete?: () => void;
  drag: AgentRowDrag;
  busy?: boolean;
}) {
  const items = [
    ...(onProperties ? [{ label: "Properties", Icon: SlidersHorizontal, onSelect: onProperties }] : []),
    { label: "Hide Agent", Icon: EyeOff, onSelect: onHide },
  ];
  return (
    <Draggable draggableId={drag.id} index={drag.index} isDragDisabled={drag.disabled || children.props.disabled} disableInteractiveElementBlocking>
      {(provided, snapshot) => (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <SidebarMenuItem
          ref={provided.innerRef}
          {...provided.draggableProps}
          data-dragging={snapshot.isDragging || undefined}
          className={snapshot.isDragging ? "rounded-md bg-sidebar-accent shadow-lg" : undefined}
        >
          {cloneElement(children, {
            ...provided.dragHandleProps,
            className: cn(children.props.className, provided.dragHandleProps && "cursor-grab", snapshot.isDragging && "cursor-grabbing"),
          })}
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
      )}
    </Draggable>
  );
}
