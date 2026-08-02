import { useState, type ComponentProps } from "react";
import { MessagesSquare, Plus, Trash2 } from "lucide-react";
import type { Profile, Session } from "@/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

/**
 * kollab session sidebar, built from the same shadcn `Sidebar` primitives the
 * assistant-ui kit uses for its own `threadlist-sidebar`. The kit's `ThreadList`
 * is driven by assistant-ui's ThreadListRuntime, which kollab does not use --
 * sessions come from the engine's /sessions endpoint -- so the data layer is
 * ours while the design language stays the kit's.
 */
export function AppSidebar({
  sessions,
  profiles,
  selectedProfile,
  activeId,
  busy,
  onProfileChange,
  // Named `onSelectSession`, not `onSelect`: ComponentProps<typeof Sidebar>
  // already carries the DOM `onSelect` handler, and the collision widens the
  // callback argument to `string | SyntheticEvent`.
  onSelectSession,
  onCreate,
  onDelete,
  ...props
}: ComponentProps<typeof Sidebar> & {
  sessions: Session[];
  profiles: Profile[];
  selectedProfile: string;
  activeId: string | null;
  busy: boolean;
  onProfileChange: (profile: string) => void;
  onSelectSession: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}) {
  // Deleting a session stops its daemon and is irreversible, so it goes behind
  // an AlertDialog rather than the bare "x" the previous shell shipped.
  const [pendingDelete, setPendingDelete] = useState<Session | null>(null);

  return (
    <Sidebar {...props}>
      <SidebarHeader className="gap-2 border-b">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" className="cursor-default hover:bg-transparent active:bg-transparent">
              <div className="bg-sidebar-primary text-sidebar-primary-foreground flex aspect-square size-8 items-center justify-center rounded-lg">
                <MessagesSquare className="size-4" />
              </div>
              <div className="flex flex-col gap-0.5 leading-none">
                <span className="font-semibold">kollab</span>
                <span className="text-muted-foreground text-xs">
                  {sessions.length} session{sessions.length === 1 ? "" : "s"}
                </span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={onCreate}
              disabled={busy}
              className="bg-sidebar-accent text-sidebar-accent-foreground justify-center font-medium"
            >
              <Plus className="size-4" />
              <span>{busy ? "Starting…" : "New session"}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Profile</SidebarGroupLabel>
          <SidebarGroupContent className="px-2">
            <Select
              value={selectedProfile}
              onValueChange={onProfileChange}
              disabled={busy || !profiles.length}
            >
              <SelectTrigger className="w-full" aria-label="LLM profile">
                <SelectValue placeholder="default" />
              </SelectTrigger>
              <SelectContent>
                {profiles.length ? (
                  profiles.map((profile) => (
                    <SelectItem key={profile.name} value={profile.name}>
                      {profile.name}
                    </SelectItem>
                  ))
                ) : (
                  <SelectItem value="default">default</SelectItem>
                )}
              </SelectContent>
            </Select>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel>Sessions</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {sessions.map((session) => (
                <SidebarMenuItem key={session.session_id}>
                  <SidebarMenuButton
                    isActive={session.session_id === activeId}
                    onClick={() => onSelectSession(session.session_id)}
                    className="h-auto flex-col items-start gap-0.5 py-2"
                  >
                    <span className="truncate font-medium">
                      {session.session_id.replace(/^sess_/, "")}
                    </span>
                    <span className="text-muted-foreground truncate text-xs">
                      {session.profile || "default"} ·{" "}
                      {session.history_length || 0} messages
                    </span>
                  </SidebarMenuButton>
                  <SidebarMenuAction
                    onClick={() => setPendingDelete(session)}
                    disabled={busy}
                    showOnHover
                    aria-label={`Delete session ${session.session_id}`}
                  >
                    <Trash2 />
                  </SidebarMenuAction>
                </SidebarMenuItem>
              ))}
              {!sessions.length ? (
                <p className="text-muted-foreground px-2 py-1 text-xs">
                  No sessions yet.
                </p>
              ) : null}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="border-t">
        <span className="text-muted-foreground px-2 py-1 text-xs">
          kollab engine · assistant-transport
        </span>
      </SidebarFooter>
      <SidebarRail />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this session?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete?.session_id} will be stopped and its conversation
              removed. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                // Read the id before clearing state; the dialog unmounts its
                // content on close and `pendingDelete` is null by the time an
                // async handler would otherwise read it.
                const id = pendingDelete?.session_id;
                setPendingDelete(null);
                if (id) onDelete(id);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sidebar>
  );
}
