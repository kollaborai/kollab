import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DragDropContext, Draggable, Droppable, type DraggableProvided, type DraggableStateSnapshot } from "@hello-pangea/dnd";
import { SidebarDragBoundary, SidebarDragContext } from "./SidebarDragBoundary";
import { createSidebarDragRouter } from "./sidebar-drag-router";
import { sidebarDropAction } from "./sidebar-drag";
import { deviceOrderKey } from "./sidebar-order";
import { cn } from "@/lib/utils";

/** Each network moves whole device groups; its agent lists share the context but keep their own scope. */
export function DeviceSidebarList<Device extends { device: string }>({
  networkId, devices, count, children, onReorder, beginDrag,
}: {
  networkId: string;
  devices: Device[];
  count: (device: Device) => number;
  children: (device: Device) => ReactNode;
  onReorder: (key: string, to: number, devices: Device[]) => void;
  beginDrag: () => () => void;
}) {
  const id = JSON.stringify(["devices", networkId]);
  const router = useMemo(createSidebarDragRouter, []);
  const releaseRef = useRef<(() => void) | null>(null);
  const frozenRef = useRef<Device[] | null>(null);
  const [frozen, setFrozen] = useState<Device[] | null>(null);
  const shown = frozen ?? devices;
  useEffect(() => () => { releaseRef.current?.(); }, []);

  const item = (device: Device, provided: DraggableProvided, snapshot: DraggableStateSnapshot) => (
    <DeviceSidebarItem device={device.device} count={count(device)} provided={provided} snapshot={snapshot}>
      {children(device)}
    </DeviceSidebarItem>
  );

  return (
    <DragDropContext
      dragHandleUsageInstructions="Press Space to lift, use the arrow keys to move, and press Space to drop. Escape cancels. Device headers move their whole group within this network. Agents move within their workspace and can be dragged outside the sidebar to hide."
      onBeforeCapture={(capture) => {
        // Polling may change presence while dragging; keep the lifted rows and target indices stable.
        frozenRef.current = devices;
        setFrozen(devices);
        if (!router.beforeCapture(capture)) {
          frozenRef.current = null;
          setFrozen(null);
        }
      }}
      onDragEnd={(result, provided) => {
        try {
          router.end(result, provided);
        } finally {
          frozenRef.current = null;
          setFrozen(null);
        }
      }}
    >
      <SidebarDragContext.Provider value={router}>
        <SidebarDragBoundary
          id={id}
          handlers={{
            owns: (key) => shown.some((device) => deviceOrderKey(networkId, device.device) === key),
            beforeCapture: () => {
              releaseRef.current?.();
              releaseRef.current = beginDrag();
            },
            end: (result) => {
              try {
                // Device gestures never use the agent drag-out hide action.
                const action = sidebarDropAction(result, false);
                if (action?.type === "reorder") {
                  onReorder(result.draggableId, action.index, frozenRef.current ?? devices);
                }
              } finally {
                releaseRef.current?.();
                releaseRef.current = null;
              }
            },
          }}
        >
          <Droppable
            droppableId={id}
            type={id}
            renderClone={(provided, snapshot, rubric) => {
              const device = shown.find((row) => deviceOrderKey(networkId, row.device) === rubric.draggableId);
              return device ? item(device, provided, snapshot) : null;
            }}
          >
            {(provided) => (
              <div ref={provided.innerRef} {...provided.droppableProps} className="w-full min-w-0">
                {shown.map((device, index) => (
                  <Draggable key={device.device} draggableId={deviceOrderKey(networkId, device.device)} index={index} isDragDisabled={shown.length < 2}>
                    {(drag, snapshot) => item(device, drag, snapshot)}
                  </Draggable>
                ))}
                {provided.placeholder}
              </div>
            )}
          </Droppable>
        </SidebarDragBoundary>
      </SidebarDragContext.Provider>
    </DragDropContext>
  );
}

function DeviceSidebarItem({ device, count, children, provided, snapshot }: {
  device: string;
  count: number;
  children: ReactNode;
  provided: DraggableProvided;
  snapshot: DraggableStateSnapshot;
}) {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return (
    <div
      ref={provided.innerRef}
      {...provided.draggableProps}
      style={{ ...provided.draggableProps.style, ...(snapshot.isDropAnimating && reducedMotion ? { transitionDuration: "1ms" } : {}) }}
      data-sidebar-device={device}
      data-dragging={snapshot.isDragging || undefined}
      className={cn("w-full min-w-0", snapshot.isDragging && "rounded-md bg-sidebar-accent shadow-lg")}
    >
      <div
        {...provided.dragHandleProps}
        data-sidebar-device-handle={device}
        aria-label={`Move device ${device}`}
        title={provided.dragHandleProps ? `Drag ${device} to reorder devices` : device}
        className={cn("text-muted-foreground flex h-6 items-center gap-2 rounded-md px-2 text-xs select-none", provided.dragHandleProps && "cursor-grab hover:bg-sidebar-accent focus-visible:bg-sidebar-accent focus-visible:outline-none", snapshot.isDragging && "cursor-grabbing")}
      >
        <span className="min-w-0 truncate">{device}</span>
        <span className="ml-auto shrink-0 text-[0.625rem] tabular-nums">{count}</span>
      </div>
      {children}
    </div>
  );
}
