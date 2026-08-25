import { useState, type ComponentProps } from "react";
import { Plus, Settings2, Trash2 } from "lucide-react";
import type { AgentPoolEntry, Profile, Session } from "@/api";
import { formatSessionName } from "@/utils/session-display";
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
  agents,
  selectedProfile,
  selectedIdentity,
  activeId,
  busy,
  onProfileChange,
  onIdentityChange,
  onSettings,
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
  agents: AgentPoolEntry[];
  selectedProfile: string;
  selectedIdentity: string;
  activeId: string | null;
  busy: boolean;
  onProfileChange: (profile: string) => void;
  onIdentityChange: (identity: string) => void;
  onSettings: () => void;
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
            <SidebarMenuButton
              size="lg"
              className="cursor-default px-2 hover:bg-transparent active:bg-transparent"
            >
              <div className="flex flex-col gap-0.5 leading-none">
                <span className="text-base font-semibold tracking-tight">kollab</span>
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
          <SidebarGroupLabel>New session</SidebarGroupLabel>
          <SidebarGroupContent className="px-2">
            <div className="flex flex-col gap-2">
              <Select
                value={selectedIdentity}
                onValueChange={onIdentityChange}
                disabled={busy || !agents.length}
              >
                <SelectTrigger className="w-full" aria-label="Agent identity">
                  <SelectValue placeholder="Choose an agent" />
                </SelectTrigger>
                <SelectContent>
                  {agents.length ? (
                    agents.map((agent) => (
                      <SelectItem
                        key={agent.name}
                        value={agent.name}
                        disabled={agent.active}
                      >
                        {agent.name}
                        {agent.active ? " · busy" : ""}
                      </SelectItem>
                    ))
                  ) : (
                    <SelectItem value="default">Pool unavailable</SelectItem>
                  )}
                </SelectContent>
              </Select>
              <Select
                value={selectedProfile}
                onValueChange={onProfileChange}
                disabled={busy || !profiles.length}
              >
                <SelectTrigger className="w-full" aria-label="New session profile">
                  <SelectValue placeholder="default" />
                </SelectTrigger>
                <SelectContent>
                  {profiles.length ? (
                    profiles.map((profile) => (
                      <SelectItem key={profile.name} value={profile.name}>
                        {profile.name}
                        {profile.model ? ` · ${profile.model}` : ""}
                      </SelectItem>
                    ))
                  ) : (
                    <SelectItem value="default">default</SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
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
                      {formatSessionName(session.name, session.session_id)}
                    </span>
                    <span className="text-muted-foreground truncate text-xs">
                      {session.identity || session.agent || "unassigned"} ·{" "}
                      {session.model || session.profile || "default"} ·{" "}
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

      <SidebarFooter className="border-t p-2">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={onSettings} disabled={!activeId}>
              <Settings2 className="size-4" />
              <span>Settings</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
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
              {formatSessionName(
                pendingDelete?.name,
                pendingDelete?.session_id,
              )}{" "}
              will be stopped and its conversation removed. This cannot be
              undone.
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
