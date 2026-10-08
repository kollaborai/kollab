"use client";

import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/attachment";
import {
  ComposerPalette,
  type ComposerPaletteItem,
} from "@/components/assistant-ui/composer-palette";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/follow-up-suggestions";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { MessageTimingBadge } from "@/components/assistant-ui/message-timing";
import {
  Reasoning,
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
  ReasoningTrigger,
} from "@/components/assistant-ui/reasoning";
import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useVoiceMode, type VoiceMode } from "@/voice-mode";
import type { AgentPoolEntry, NetworkAgent, SlashCommand } from "@/api";
import {
  panelCommandRequest,
  type PanelOpenRequest,
} from "@/components/panels/panel-model";
import {
  ActionBarMorePrimitive,
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  BranchPickerPrimitive,
  ComposerPrimitive,
  ErrorPrimitive,
  groupPartByType,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartComponent,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CopyIcon,
  DownloadIcon,
  MicIcon,
  MoreHorizontalIcon,
  PencilIcon,
  RefreshCwIcon,
  SquareIcon,
} from "lucide-react";
import {
  createContext,
  useContext,
  useMemo,
  type ComponentType,
  type FC,
  type KeyboardEvent,
  type PropsWithChildren,
} from "react";

export type ThreadGroupPart = MessagePrimitive.GroupedParts.GroupPart;

/**
 * Optional component overrides for the thread. `AssistantMessage` and
 * `Welcome` replace whole sections; the remaining slots override how the
 * assistant message renders tool calls and part groups. Tool UIs registered
 * by name (toolkit `render`, `useAssistantDataUI`) take precedence over
 * `ToolFallback`.
 */
export type ThreadComponents = {
  AssistantMessage?: ComponentType | undefined;
  Welcome?: ComponentType | undefined;
  ToolFallback?: ToolCallMessagePartComponent | undefined;
  ToolGroup?:
    | ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>>
    | undefined;
  ReasoningGroup?:
    | ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>>
    | undefined;
  /** A system message, e.g. another agent's hub message. */
  SystemMessage?: ComponentType | undefined;
};

export type ThreadProps = {
  components?: ThreadComponents | undefined;
  agents?: readonly AgentPoolEntry[] | undefined;
  commands?: readonly SlashCommand[] | undefined;
  /** Open a Settings tab; called for a bare panel command typed in the composer. */
  onOpenPanel?: ((request: PanelOpenRequest) => void) | undefined;
  /** Offer image attachments; false hides the paperclip and refuses drops and pastes. */
  attachmentsEnabled?: boolean | undefined;
};

const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext =
  createContext<ThreadComponents>(EMPTY_COMPONENTS);

/** The composer's placeholder; the app names the session's gem ("Message Lapis…"). */
export const ComposerPlaceholderContext = createContext("Send a message...");

/** The chat's project folder and the agents on the network's other computers (agent@device), for @. */
export const ComposerHubContext = createContext<{ workspace: string; remote: readonly NetworkAgent[] }>({
  workspace: "",
  remote: [],
});

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  (!s.thread.isLoading || s.threads.isLoading);

export const Thread: FC<ThreadProps> = ({
  components = EMPTY_COMPONENTS,
  agents = [],
  commands = [],
  onOpenPanel,
  attachmentsEnabled = true,
}) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot
        isEmpty={isEmpty}
        agents={agents}
        commands={commands}
        onOpenPanel={onOpenPanel}
        attachmentsEnabled={attachmentsEnabled}
      />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{
  isEmpty: boolean;
  agents: readonly AgentPoolEntry[];
  commands: readonly SlashCommand[];
  onOpenPanel: ((request: PanelOpenRequest) => void) | undefined;
  attachmentsEnabled: boolean;
}> = ({ isEmpty, agents, commands, onOpenPanel, attachmentsEnabled }) => {
  const { Welcome = ThreadWelcome } = useContext(ThreadComponentsContext);

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-background @container flex h-full flex-col"
      style={{
        ["--thread-max-width" as string]: "44rem",
        ["--composer-bg" as string]:
          "color-mix(in oklab, var(--color-muted) 30%, var(--color-background))",
        ["--composer-radius" as string]: "1.75rem",
        ["--composer-padding" as string]: "8px",
      }}
    >
      <ThreadPrimitive.Viewport
        turnAnchor="top"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-scroll scroll-smooth"
      >
        <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-4 pt-4">
          {/* Auto margins on the welcome and the footer center them together
              while the thread is empty, and unlike justify-center they never
              push the top out of reach when a tall hero overflows. */}
          <AuiIf condition={isNewChatView}>
            <div className="mt-auto">
              <Welcome />
            </div>
          </AuiIf>

          <div
            data-slot="aui_message-group"
            className="mb-14 flex flex-col gap-y-6 empty:hidden"
          >
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer bg-background flex flex-col gap-4 overflow-visible pb-4 md:pb-6",
              isEmpty
                ? "mb-auto"
                : "sticky bottom-0 mt-auto rounded-t-(--composer-radius)",
            )}
          >
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            <Composer
              agents={agents}
              commands={commands}
              onOpenPanel={onOpenPanel}
              attachmentsEnabled={attachmentsEnabled}
            />
            <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
              <ThreadSuggestions />
            </AuiIf>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

const ThreadMessage: FC = () => {
  const {
    AssistantMessage: AssistantMessageComponent = AssistantMessage,
    SystemMessage: SystemMessageComponent = SystemMessage,
  } = useContext(ThreadComponentsContext);
  const role = useAuiState((s) => s.message.role);
  const isEditing = useAuiState((s) => s.message.composer.isEditing);

  if (isEditing) return <EditComposer />;
  if (role === "user") return <UserMessage />;
  if (role === "system") return <SystemMessageComponent />;
  return <AssistantMessageComponent />;
};

const SystemMessage: FC = () => (
  <MessagePrimitive.Root
    data-slot="aui_system-message-root"
    data-role="system"
    className="fade-in slide-in-from-bottom-1 animate-in text-muted-foreground px-2 text-center text-sm duration-150"
  >
    <MessagePrimitive.Parts />
  </MessagePrimitive.Root>
);

const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      <TooltipIconButton
        tooltip="Scroll to Bottom"
        variant="outline"
        className="aui-thread-scroll-to-bottom dark:border-border dark:bg-background dark:hover:bg-accent absolute -top-12 z-10 self-center rounded-full p-4 disabled:invisible"
      >
        <ArrowDownIcon />
      </TooltipIconButton>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-6 flex flex-col items-center px-4 text-center">
      <h1 className="aui-thread-welcome-message-inner fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-2xl font-semibold duration-200">
        How can I help you today?
      </h1>
    </div>
  );
};

const ThreadSuggestions: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestions flex w-full flex-wrap items-center justify-center gap-2 px-4">
      <ThreadPrimitive.Suggestions>
        {() => <ThreadSuggestionItem />}
      </ThreadPrimitive.Suggestions>
    </div>
  );
};

const ThreadSuggestionItem: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestion-display fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200">
      <SuggestionPrimitive.Trigger send asChild>
        <Button
          variant="ghost"
          className="aui-thread-welcome-suggestion text-foreground hover:bg-muted border-border/60 h-auto gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-normal whitespace-nowrap transition-colors"
        >
          <SuggestionPrimitive.Title className="aui-thread-welcome-suggestion-text-1" />
          <SuggestionPrimitive.Description className="aui-thread-welcome-suggestion-text-2 empty:hidden" />
        </Button>
      </SuggestionPrimitive.Trigger>
    </div>
  );
};

const Composer: FC<{
  agents: readonly AgentPoolEntry[];
  commands: readonly SlashCommand[];
  onOpenPanel: ((request: PanelOpenRequest) => void) | undefined;
  attachmentsEnabled: boolean;
}> = ({ agents, commands, onOpenPanel, attachmentsEnabled }) => {
  const aui = useAui();
  const voice = useVoiceMode();
  const placeholder = useContext(ComposerPlaceholderContext);
  const { workspace, remote: remoteAgents } = useContext(ComposerHubContext);
  // The mic only exists where the session's live command list has /voicemode.
  const voiceAvailable = commands.some(
    (command) => command.name === "voicemode" && command.enabled !== false,
  );
  const slotMode = useAuiState((s) =>
    composerSlotMode(s, voiceAvailable ? voice : null),
  );

  // A bare /config, /llm, /model, /setup or /connect (also /connect code and
  // /connect knocks) opens its Settings tab and posts no text. Any other line is
  // sent exactly as typed. Runs on form submit (Enter) and on the Send button.
  const openPanelFromComposer = (event: { preventDefault: () => void }) => {
    if (!onOpenPanel) return;
    const { text, attachments } = aui.composer.getState();
    const request =
      attachments.length === 0 ? panelCommandRequest(text, commands) : null;
    if (!request) return;
    event.preventDefault();
    aui.composer.setText("");
    onOpenPanel(request);
  };

  // Picking a panel command in the slash menu (Enter or click) opens its tab
  // instead of leaving "/config" in the box for a second Enter.
  const openPanelForItem = (item: {
    id: string;
    metadata?: Record<string, unknown> | undefined;
  }) => {
    if (!onOpenPanel || aui.composer.getState().attachments.length > 0) return;
    const insert = item.metadata?.insertText;
    const request = panelCommandRequest(
      `/${typeof insert === "string" ? insert : item.id}`,
      commands,
    );
    if (!request) return;
    aui.composer.setText("");
    onOpenPanel(request);
  };

  const commandItems = useMemo<readonly ComposerPaletteItem[]>(
    () => {
      const visibleCommands = [...commands]
        .filter((command) => command.enabled !== false && command.name)
        .sort((left, right) => {
          const categoryCompare = (left.category || "system").localeCompare(
            right.category || "system",
          );
          return categoryCompare || left.name.localeCompare(right.name);
        });

      return visibleCommands.flatMap((command) => {
        const aliases = command.aliases?.filter(Boolean) ?? [];
        const category = command.category || "system";
        const subcommands =
          command.subcommands?.filter((item) => item.name.trim()) ?? [];
        const parameterOptions = (command.parameters ?? []).flatMap(
          (parameter) =>
            (parameter.choices ?? []).filter(Boolean).map((choice) => ({
              choice,
              parameterName: parameter.name,
              description:
                parameter.description ||
                `Choose a ${prettyCommandName(parameter.name).toLowerCase()}`,
            })),
        );
        const nestedCount = subcommands.length + parameterOptions.length;
        const parent: ComposerPaletteItem = {
          id: command.name,
          label: prettyCommandName(command.name),
          description:
            command.description ||
            (nestedCount > 0
              ? `${nestedCount} option${nestedCount === 1 ? "" : "s"}`
              : "Run this command"),
          type: "command",
          searchText: [command.name, ...aliases, command.description]
            .filter(Boolean)
            .join(" "),
          secondary:
            aliases.length > 0
              ? aliases.map((alias) => `/${alias}`).join(" · ")
              : undefined,
          insertText: command.name,
          category,
          depth: 0,
          icon: "command",
        };

        const nestedSubcommands: ComposerPaletteItem[] = subcommands.map(
          (subcommand, index) => ({
            id: `${command.name}::${subcommand.name}::${index}`,
            label: subcommand.name,
            description: subcommand.description || "Run this subcommand",
            type: "command",
            searchText: [
              command.name,
              ...aliases,
              subcommand.name,
              subcommand.args,
              subcommand.description,
            ]
              .filter(Boolean)
              .join(" "),
            insertText: `${command.name} ${subcommand.name}`,
            commandPath: command.name,
            parentId: command.name,
            category,
            depth: 1,
            args: subcommand.args,
            icon: "command",
          }),
        );

        const nestedParameters: ComposerPaletteItem[] = parameterOptions.map(
          ({ choice, parameterName, description }, index) => ({
            id: `${command.name}::${parameterName}::${choice}::${index}`,
            label: choice,
            description,
            type: "command",
            searchText: [
              command.name,
              ...aliases,
              parameterName,
              choice,
              description,
            ]
              .filter(Boolean)
              .join(" "),
            insertText: `${command.name} ${choice}`,
            commandPath: command.name,
            parentId: command.name,
            category,
            depth: 1,
            secondary: parameterName,
            icon: "command",
          }),
        );

        return [parent, ...nestedSubcommands, ...nestedParameters];
      });
    },
    [commands],
  );

  const nestedCommandNames = useMemo(() => {
    const names = new Set<string>();

    for (const command of commands) {
      const hasSubcommands = command.subcommands?.some((item) =>
        item.name.trim(),
      );
      const hasParameterChoices = command.parameters?.some((parameter) =>
        parameter.choices?.some(Boolean),
      );

      if (!hasSubcommands && !hasParameterChoices) continue;

      names.add(command.name.toLowerCase());
      for (const alias of command.aliases ?? []) {
        if (alias) names.add(alias.toLowerCase());
      }
    }

    return names;
  }, [commands]);

  const handleComposerKeyDown = (
    event: KeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (
      event.key !== " " ||
      event.shiftKey ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey
    ) {
      return;
    }

    // assistant-ui stops trigger detection at whitespace. Keep the command
    // trigger active for the one space that means “show this command's
    // subcommands”; ordinary message spaces still go through unchanged.
    const composerText = event.currentTarget.value;
    if (!/^\/[A-Za-z0-9][A-Za-z0-9_-]*$/.test(composerText)) return;
    if (!nestedCommandNames.has(composerText.slice(1).toLowerCase())) return;

    event.preventDefault();
  };

  const agentItems = useMemo<readonly ComposerPaletteItem[]>(() => {
    // A hub mesh is one project folder: a live agent in another folder never hears this chat.
    const folder = (path: string) => path.replace(/\/+$/, "");
    const onlineAgents = agents
      .filter(
        (agent) =>
          agent.name &&
          agent.active &&
          (!workspace || !agent.project || folder(agent.project) === folder(workspace)),
      )
      .map((agent) => ({
        id: agent.name,
        label: agent.name,
        description:
          agent.personality ||
          agent.role_aliases?.filter(Boolean).join(" · ") ||
          agent.agent_type ||
          "Online Kollab agent",
        type: "agent" as const,
        searchText: [
          agent.name,
          agent.identity,
          agent.agent_type,
          agent.personality,
          ...(agent.role_aliases ?? []),
        ]
          .filter(Boolean)
          .join(" "),
        status: agent.state || "online",
        icon: "agent" as const,
      }));
    // agent@device is the address the hub routes across computers.
    const networkAgents = remoteAgents.map((agent) => {
      const handle = agent.handle || `${agent.name}@${agent.device}`;
      return {
        id: handle,
        label: handle,
        description: `On ${agent.device}`,
        type: "agent" as const,
        searchText: [handle, agent.name, agent.device].join(" "),
        status: agent.state || "online",
        icon: "agent" as const,
      };
    });
    return [
      {
        id: "broadcast",
        label: "Broadcast",
        description: "Every agent in this project",
        type: "agent" as const,
        searchText: "broadcast project all everyone",
        icon: "broadcast" as const,
      },
      {
        id: "local-broadcast",
        label: "Local Broadcast",
        description: "Every agent on this computer",
        type: "agent" as const,
        searchText: "local broadcast computer all everyone",
        icon: "broadcast" as const,
      },
      {
        id: "global-broadcast",
        label: "Global Broadcast",
        description: "Every agent on every computer in your network",
        type: "agent" as const,
        searchText: "global broadcast network all everyone",
        icon: "broadcast" as const,
      },
      ...onlineAgents,
      ...networkAgents,
    ];
  }, [agents, remoteAgents, workspace]);

  return (
    <ComposerPrimitive.Unstable_TriggerPopoverRoot>
      <ComposerPrimitive.Root
        className="aui-composer-root group/composer relative flex w-full flex-col"
        data-mode={slotMode}
        onSubmit={openPanelFromComposer}
      >
        <ComposerPalette
          char="/"
          items={commandItems}
          title="Commands"
          emptyMessage="No matching commands"
          emptyHint="Keep typing to refine the command search."
          onInserted={openPanelForItem}
        />
        <ComposerPalette
          char="@"
          items={agentItems}
          title="Message an agent"
          emptyMessage="No online agents"
          emptyHint="Try @broadcast to reach every agent in this project."
        />
        <ComposerPrimitive.AttachmentDropzone
          asChild
          disabled={!attachmentsEnabled}
        >
          <div
            data-slot="aui_composer-shell"
            className="border-border/60 data-[dragging=true]:border-ring focus-within:border-border dark:border-muted-foreground/15 dark:focus-within:border-muted-foreground/30 flex w-full flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.04)] transition-[border-color,box-shadow] focus-within:shadow-[0_6px_24px_-8px_rgba(0,0,0,0.12),0_1px_2px_rgba(0,0,0,0.05)] data-[dragging=true]:border-dashed data-[dragging=true]:bg-[color-mix(in_oklab,var(--color-accent)_50%,var(--color-background))] dark:shadow-none"
          >
            <ComposerAttachments />
            <div className="aui-composer-row flex items-end gap-1.5">
              {attachmentsEnabled ? <ComposerAddAttachment /> : null}
              <ComposerPrimitive.Input
                placeholder={placeholder}
                className="aui-composer-input caret-primary placeholder:text-muted-foreground/80 max-h-32 min-h-9 min-w-0 flex-1 resize-none bg-transparent px-1 py-1.5 text-base leading-6 outline-none"
                rows={1}
                autoFocus
                enterKeyHint="send"
                aria-label="Message input"
                addAttachmentOnPaste={attachmentsEnabled}
                onKeyDown={handleComposerKeyDown}
              />
              <ComposerSlot
                voice={voiceAvailable ? voice : null}
                onBeforeSend={openPanelFromComposer}
              />
            </div>
          </div>
        </ComposerPrimitive.AttachmentDropzone>
      </ComposerPrimitive.Root>
    </ComposerPrimitive.Unstable_TriggerPopoverRoot>
  );
};

function prettyCommandName(name: string): string {
  const words = name
    .replace(/[-_]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  return words
    .map((word) => {
      const lower = word.toLowerCase();
      if (["api", "cli", "id", "mcp", "ui", "url"].includes(lower)) {
        return lower.toUpperCase();
      }
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(" ");
}

type ComposerSlotMode = "mic" | "send" | "listening" | "stop";

// One trailing button slot that morphs: stop while a run is going, send once
// there is something to send, otherwise the /voicemode toggle (a mic, or the
// pulsing listening button while voice is on) where the session offers it.
const composerSlotMode = (
  s: AssistantState,
  voice: VoiceMode | null,
): ComposerSlotMode => {
  // A /voicemode command is itself a chat run; hold the mic still through it
  // rather than flashing stop.
  if (voice?.pending) return voice.on ? "listening" : "mic";
  if (s.thread.isRunning) return "stop";
  if (!s.composer.isEmpty || !voice) return "send";
  return voice.on ? "listening" : "mic";
};

// The slot's buttons stack in one box. Each is hidden (scaled down, faded,
// visibility off so it leaves the tab order) until the composer root's
// data-mode names it; visibility flips at the end of the fade-out.
const SLOT_BUTTON =
  "absolute inset-0 size-9 rounded-full p-1 scale-50 opacity-0 invisible transition-all duration-200 ease-out motion-reduce:transition-none";
const SLOT_SHOWS_MIC =
  "group-data-[mode=mic]/composer:scale-100 group-data-[mode=mic]/composer:opacity-100 group-data-[mode=mic]/composer:visible";
const SLOT_SHOWS_SEND =
  "group-data-[mode=send]/composer:scale-100 group-data-[mode=send]/composer:opacity-100 group-data-[mode=send]/composer:visible";
const SLOT_SHOWS_LISTENING =
  "group-data-[mode=listening]/composer:scale-100 group-data-[mode=listening]/composer:opacity-100 group-data-[mode=listening]/composer:visible";
const SLOT_SHOWS_STOP =
  "group-data-[mode=stop]/composer:scale-100 group-data-[mode=stop]/composer:opacity-100 group-data-[mode=stop]/composer:visible";

const ComposerSlot: FC<{
  voice: VoiceMode | null;
  onBeforeSend: (event: { preventDefault: () => void }) => void;
}> = ({ voice, onBeforeSend }) => {
  return (
    <div className="aui-composer-slot relative size-9 shrink-0">
      {voice && (
        <>
          <TooltipIconButton
            tooltip="Start Voice"
            side="top"
            type="button"
            variant="default"
            disabled={voice.pending}
            onClick={voice.toggle}
            className={cn(SLOT_BUTTON, SLOT_SHOWS_MIC)}
            aria-label="Start voice mode"
          >
            <MicIcon className="size-5" />
          </TooltipIconButton>
          <TooltipIconButton
            tooltip="Stop Listening"
            side="top"
            type="button"
            variant="destructive"
            disabled={voice.pending}
            onClick={voice.toggle}
            className={cn(SLOT_BUTTON, SLOT_SHOWS_LISTENING)}
            aria-label="Stop listening"
          >
            <MicIcon className="size-5 animate-pulse" />
          </TooltipIconButton>
        </>
      )}
      <ComposerPrimitive.Send asChild onClick={onBeforeSend}>
        <TooltipIconButton
          tooltip="Send Message"
          side="top"
          type="button"
          variant="default"
          className={cn(SLOT_BUTTON, SLOT_SHOWS_SEND)}
          aria-label="Send message"
        >
          <ArrowUpIcon className="size-5" />
        </TooltipIconButton>
      </ComposerPrimitive.Send>
      <ComposerPrimitive.Cancel asChild>
        <TooltipIconButton
          tooltip="Stop Generating"
          side="top"
          type="button"
          variant="default"
          className={cn(SLOT_BUTTON, SLOT_SHOWS_STOP)}
          aria-label="Stop generating"
        >
          <SquareIcon className="size-3.5 fill-current" />
        </TooltipIconButton>
      </ComposerPrimitive.Cancel>
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root border-destructive bg-destructive/10 text-destructive dark:bg-destructive/5 mt-2 rounded-md border p-3 text-sm dark:text-red-200">
        <ErrorPrimitive.Message className="aui-message-error-message whitespace-pre-wrap wrap-break-word" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

const AssistantMessage: FC = () => {
  const {
    ToolFallback: ToolFallbackComponent = ToolFallback,
    ToolGroup,
    ReasoningGroup,
  } = useContext(ThreadComponentsContext);

  const ACTION_BAR_PT = "pt-1.5";
  // Keep the action bar inside the contained root's paint box, then cancel its reserved space in flow.
  const ACTION_BAR_HEIGHT = `min-h-7.5 ${ACTION_BAR_PT}`;

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 animate-in relative -mb-7.5 pb-7.5 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <div
        data-slot="aui_assistant-message-content"
        className="text-foreground px-2 leading-relaxed wrap-break-word"
      >
        <MessagePrimitive.GroupedParts
          groupBy={groupPartByType({
            reasoning: ["group-chainOfThought", "group-reasoning"],
            "tool-call": ["group-chainOfThought", "group-tool"],
            "standalone-tool-call": [],
          })}
        >
          {({ part, children }) => {
            switch (part.type) {
              case "group-chainOfThought":
                return <div data-slot="aui_chain-of-thought">{children}</div>;
              case "group-tool":
                if (ToolGroup) {
                  return <ToolGroup group={part}>{children}</ToolGroup>;
                }
                return (
                  <ToolGroupRoot variant="ghost" defaultOpen>
                    <ToolGroupTrigger
                      count={part.indices.length}
                      active={part.status.type === "running"}
                    />
                    <ToolGroupContent>{children}</ToolGroupContent>
                  </ToolGroupRoot>
                );
              case "group-reasoning": {
                if (ReasoningGroup) {
                  return (
                    <ReasoningGroup group={part}>{children}</ReasoningGroup>
                  );
                }
                const running = part.status.type === "running";
                return (
                  <ReasoningRoot streaming={running}>
                    <ReasoningTrigger active={running} />
                    <ReasoningContent aria-busy={running}>
                      <ReasoningText>{children}</ReasoningText>
                    </ReasoningContent>
                  </ReasoningRoot>
                );
              }
              case "text":
                return <MarkdownText />;
              case "reasoning":
                return <Reasoning {...part} />;
              case "tool-call":
                return part.toolUI ?? <ToolFallbackComponent {...part} />;
              case "data":
                return part.dataRendererUI;
              case "indicator":
                return (
                  <span
                    data-slot="aui_assistant-message-indicator"
                    className="animate-pulse font-sans"
                    aria-label="Assistant is working"
                  >
                    {"●"}
                  </span>
                );
              default:
                return null;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessageError />
      </div>

      <div
        data-slot="aui_assistant-message-footer"
        className={cn("ms-2 flex items-center", ACTION_BAR_HEIGHT)}
      >
        <BranchPicker />
        <AssistantActionBar />
      </div>
    </MessagePrimitive.Root>
  );
};

const AssistantActionBar: FC = () => {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-assistant-action-bar-root text-muted-foreground animate-in fade-in col-start-3 row-start-2 -ms-1 flex gap-1 duration-200"
    >
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton tooltip="Copy">
          <AuiIf condition={(s) => s.message.isCopied}>
            <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
          </AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>
            <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
          </AuiIf>
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>
      {/* Reload and Edit throw "Runtime does not support ..." when the runtime
          has no handler, so they render only when the runtime reports it. */}
      <AuiIf condition={(s) => s.thread.capabilities.reload}>
        <ActionBarPrimitive.Reload asChild>
          <TooltipIconButton tooltip="Reload">
            <RefreshCwIcon />
          </TooltipIconButton>
        </ActionBarPrimitive.Reload>
      </AuiIf>
      <MessageTimingBadge />
      <ActionBarMorePrimitive.Root>
        <ActionBarMorePrimitive.Trigger asChild>
          <TooltipIconButton
            tooltip="More"
            className="data-[state=open]:bg-accent"
          >
            <MoreHorizontalIcon />
          </TooltipIconButton>
        </ActionBarMorePrimitive.Trigger>
        <ActionBarMorePrimitive.Content
          side="bottom"
          align="start"
          sideOffset={6}
          className="aui-action-bar-more-content bg-popover/95 text-popover-foreground data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:animate-out data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 min-w-[8rem] overflow-hidden rounded-xl border p-1.5 shadow-lg backdrop-blur-sm"
        >
          <ActionBarPrimitive.ExportMarkdown asChild>
            <ActionBarMorePrimitive.Item className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none">
              <DownloadIcon className="size-4" />
              Export as Markdown
            </ActionBarMorePrimitive.Item>
          </ActionBarPrimitive.ExportMarkdown>
        </ActionBarMorePrimitive.Content>
      </ActionBarMorePrimitive.Root>
    </ActionBarPrimitive.Root>
  );
};

const UserMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto] [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        <div className="aui-user-message-content peer bg-muted text-foreground rounded-xl px-4 py-2 whitespace-pre-wrap wrap-break-word empty:hidden">
          <MessagePrimitive.Parts />
        </div>
        <div className="aui-user-action-bar-wrapper absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 peer-empty:hidden rtl:translate-x-full">
          <UserActionBar />
        </div>
      </div>

      <BranchPicker
        data-slot="aui_user-branch-picker"
        className="col-span-full col-start-1 row-start-3 -me-1 justify-end"
      />
    </MessagePrimitive.Root>
  );
};

const UserActionBar: FC = () => {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-user-action-bar-root text-muted-foreground flex items-center gap-1"
    >
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton tooltip="Copy" className="aui-user-action-copy">
          <AuiIf condition={(s) => s.message.isCopied}>
            <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
          </AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>
            <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
          </AuiIf>
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>
      <AuiIf condition={(s) => s.thread.capabilities.edit}>
        <ActionBarPrimitive.Edit asChild>
          <TooltipIconButton tooltip="Edit" className="aui-user-action-edit">
            <PencilIcon />
          </TooltipIconButton>
        </ActionBarPrimitive.Edit>
      </AuiIf>
    </ActionBarPrimitive.Root>
  );
};

const EditComposer: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_edit-composer-wrapper"
      className="flex flex-col px-2 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <ComposerPrimitive.Root className="aui-edit-composer-root border-border/60 dark:border-muted-foreground/15 ms-auto flex w-full max-w-[85%] flex-col rounded-(--composer-radius) border bg-(--composer-bg) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.04)] dark:shadow-none">
        <ComposerPrimitive.Input
          className="aui-edit-composer-input text-foreground min-h-14 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-base outline-none"
          autoFocus
        />
        <div className="aui-edit-composer-footer mx-2.5 mb-2.5 flex items-center gap-1.5 self-end">
          <ComposerPrimitive.Cancel asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 rounded-full px-3.5"
            >
              Cancel
            </Button>
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send asChild>
            <Button size="sm" className="h-8 rounded-full px-3.5">
              Update
            </Button>
          </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
};

const BranchPicker: FC<BranchPickerPrimitive.Root.Props> = ({
  className,
  ...rest
}) => {
  return (
    <BranchPickerPrimitive.Root
      hideWhenSingleBranch
      className={cn(
        "aui-branch-picker-root text-muted-foreground -ms-2 me-2 inline-flex items-center text-xs",
        className,
      )}
      {...rest}
    >
      <BranchPickerPrimitive.Previous asChild>
        <TooltipIconButton tooltip="Previous">
          <ChevronLeftIcon />
        </TooltipIconButton>
      </BranchPickerPrimitive.Previous>
      <span className="aui-branch-picker-state font-medium">
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next asChild>
        <TooltipIconButton tooltip="Next">
          <ChevronRightIcon />
        </TooltipIconButton>
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
  );
};
