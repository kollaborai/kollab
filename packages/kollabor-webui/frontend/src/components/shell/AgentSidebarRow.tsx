import type { ReactNode } from "react";
import { EyeOff, MoreHorizontal, SlidersHorizontal, Trash2 } from "lucide-react";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from "@/components/ui/context-menu";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { SidebarMenuAction, SidebarMenuItem } from "@/components/ui/sidebar";

/** The same actions work by mouse, keyboard, right-click and phone tap. */
export function AgentSidebarRow({ name, children, onHide, onProperties, onDelete, busy }: {
  name: string;
  children: ReactNode;
  onHide: () => void;
  onProperties?: () => void;
  onDelete?: () => void;
  busy?: boolean;
}) {
  const items = [
    ...(onProperties ? [{ label: "Properties", Icon: SlidersHorizontal, onSelect: onProperties }] : []),
    { label: "Hide Agent", Icon: EyeOff, onSelect: onHide },
  ];
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <SidebarMenuItem>
          {children}
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
