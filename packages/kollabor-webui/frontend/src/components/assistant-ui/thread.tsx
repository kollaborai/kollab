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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { resolveMediaSegment, splitMediaSegments } from "@/components/media/media-tokens";
import { MediaThumb, useMediaViewer, useSessionMedia } from "@/components/media/media-view";
import { agentStatus, bundleName } from "@/components/panels/panel-model";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  loadComposerAttachments,
  loadComposerDraft,
  loadPersistedComposerAttachments,
  queueComposerDraftSubmission,
  saveComposerAttachments,
  saveComposerDraft,
} from "@/composer-draft";
import {
  collectImageAttachments,
  orphanImageNotice,
  stripImageTokens,
  stripLeadingImageTokens,
} from "@/composer-restore";
import { useShallow } from "zustand/shallow";
import type { ThreadUserMessagePart } from "@assistant-ui/core";
import {
  useVoiceMode,
  type VoiceActivity,
  type VoiceMode,
} from "@/voice-mode";
import { useRoomVoiceMode, useVoiceRoomLive, VoiceRoomBar } from "@/voice/voice-room";
import {
  imageAttachmentAdapter,
  usePendingPermissionCancellation,
  useSendWaitingNow,
} from "@/runtime";
import type { AgentPoolEntry, MediaEntry, NetworkAgent, SlashCommand } from "@/api";
import {
  matchPluginCommand,
  pluginCommandConflicts,
  pluginCommandEntries,
  pluginCommandFailure,
  pluginCommandItems,
  reservedCommandKeys,
  warnPluginCommandConflicts,
} from "@/plugins/composer-commands";
import { PluginTaggedText, PluginToolPart } from "@/plugins/renderers";
import { slashCatalogNotice } from "./slash-catalog-notice";
import { usePlugins } from "@/plugins/registry";
import {
  panelCommandRequest,
  type PanelOpenRequest,
} from "@/components/panels/panel-model";
import {
  ActionBarMorePrimitive,
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  ComposerPrimitive,
  ErrorPrimitive,
  groupPartByType,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type PendingAttachment,
  type ToolCallMessagePartComponent,
  useAui,
  useAssistantTransportSendCommand,
  useAuiState,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
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
  useEffect,
  useMemo,
  useLayoutEffect,
  useRef,
  useState,
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
  /** Companion docked beside the input once the conversation has started. */
  ComposerLeading?: ComponentType | undefined;
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
  commandCatalogStatus?: "loading" | "ready" | "empty" | "unavailable" | "fallback" | undefined;
  onRetryCommands?: (() => void) | undefined;
  /** Open a Settings tab; called for a bare panel command typed in the composer. */
  onOpenPanel?: ((request: PanelOpenRequest) => void) | undefined;
  onOpenWelcome?: (() => void) | undefined;
  /** Offer image attachments; false hides the paperclip and refuses drops and pastes. */
  attachmentsEnabled?: boolean | undefined;
  /** Visible status while a bounded history page is being fetched. */
  historyStatus?: { kind: string; message: string } | undefined;
  /** Loads the page immediately older than the current window. */
  onLoadOlder?: (() => Promise<boolean | void>) | undefined;
  onRetryHistory?: (() => void) | undefined;
  historyStartIndex?: number | undefined;
  /** Per-agent/conversation localStorage key for the unsent composer text. */
  draftKey?: string | null | undefined;
};


const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext =
  createContext<ThreadComponents>(EMPTY_COMPONENTS);

/** The composer's placeholder; the app names the session's gem ("Message Lapis…"). */
export const ComposerPlaceholderContext = createContext("Send a message...");

/** Why the composer is closed while another chat starts or loads (shown as its placeholder); empty when open. */
export const ComposerLockContext = createContext("");

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
  commandCatalogStatus = "ready",
  onRetryCommands,
  onOpenPanel,
  onOpenWelcome,
  attachmentsEnabled = true,
  historyStatus,
  onLoadOlder,
  onRetryHistory,
  historyStartIndex = 0,
  draftKey,
}) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot
        isEmpty={isEmpty}
        agents={agents}
        commands={commands}
        commandCatalogStatus={commandCatalogStatus}
        onRetryCommands={onRetryCommands}
        onOpenPanel={onOpenPanel}
        onOpenWelcome={onOpenWelcome}
        attachmentsEnabled={attachmentsEnabled}
        historyStatus={historyStatus}
        onLoadOlder={onLoadOlder}
        onRetryHistory={onRetryHistory}
        historyStartIndex={historyStartIndex}
        draftKey={draftKey}
      />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{
  isEmpty: boolean;
  agents: readonly AgentPoolEntry[];
  commands: readonly SlashCommand[];
  commandCatalogStatus: NonNullable<ThreadProps["commandCatalogStatus"]>;
  onRetryCommands: ThreadProps["onRetryCommands"];
  onOpenPanel: ((request: PanelOpenRequest) => void) | undefined;
  onOpenWelcome: (() => void) | undefined;
  attachmentsEnabled: boolean;
  historyStatus: ThreadProps["historyStatus"];
  onLoadOlder: ThreadProps["onLoadOlder"];
  onRetryHistory: ThreadProps["onRetryHistory"];
  historyStartIndex: number;
  draftKey: ThreadProps["draftKey"];
}> = ({
  isEmpty,
  agents,
  commands,
  commandCatalogStatus,
  onRetryCommands,
  onOpenPanel,
  onOpenWelcome,
  attachmentsEnabled,
  historyStatus,
  onLoadOlder,
  onRetryHistory,
  historyStartIndex,
  draftKey,
}) => {
  const { Welcome = ThreadWelcome, ComposerLeading } = useContext(ThreadComponentsContext);
  const viewportRef = useRef<HTMLDivElement>(null);
  const scrollAnchorRef = useRef<{ start: number } | null>(null);

  const userScrollRef = useRef(false);
  const touchStartYRef = useRef<number | null>(null);

  // A voice room's turns arrive from the room, not from this composer's runs,
  // so the top anchor never moves for them: while the room is live the chat
  // follows the bottom, from the latest turn when the room starts.
  const voiceLive = useVoiceRoomLive();
  useEffect(() => {
    const viewport = viewportRef.current;
    if (voiceLive && viewport) viewport.scrollTo({ top: viewport.scrollHeight, behavior: "instant" });
  }, [voiceLive]);

  // Shared load-older trigger: only real user scroll intent, only at (or
  // near) the top of the viewport, and only when no older load is already
  // anchored or in flight.
  const requestLoadOlder = (viewport: HTMLElement) => {
    if (
      viewport.scrollTop > 64 ||
      !userScrollRef.current ||
      !onLoadOlder ||
      scrollAnchorRef.current ||
      historyStatus?.kind === "loading" ||
      historyStatus?.kind === "loading-older"
    ) {
      return;
    }
    const anchor = { start: historyStartIndex };
    scrollAnchorRef.current = anchor;
    void onLoadOlder()
      .then((merged) => {
        // A load that did not extend this window — a no-op because no older
        // history exists, a merge dropped because a refresh replaced the
        // window mid-flight, or a cursor-rejected rebuild — must not leave
        // the anchor set. A dangling anchor would block every later trigger,
        // stranding the view at the top of a window whose older history
        // becomes available again (e.g. a byte-capped refresh replacing an
        // exhausted window with one that has older pages).
        if (!merged) {
          if (scrollAnchorRef.current === anchor) scrollAnchorRef.current = null;
        } else {
          // A concurrent refresh can replace the extended window before
          // React publishes its start index. In that case the layout effect
          // cannot consume the anchor; release it so the next up gesture
          // can retry instead of leaving paging blocked indefinitely.
          window.setTimeout(() => {
            if (scrollAnchorRef.current === anchor) scrollAnchorRef.current = null;
          }, 600);
        }
      })
      .catch(() => {
        if (scrollAnchorRef.current === anchor) scrollAnchorRef.current = null;
      })
      .finally(() => {
        userScrollRef.current = false;
      });
  };

  useLayoutEffect(() => {
    const anchor = scrollAnchorRef.current;
    if (!anchor) return;
    if (historyStatus?.kind === "error" || (
      historyStatus?.kind === "ready" && historyStartIndex === anchor.start
    )) {
      scrollAnchorRef.current = null;
      return;
    }
    if (historyStartIndex < anchor.start) scrollAnchorRef.current = null;
  }, [historyStartIndex, historyStatus?.kind]);

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
        ref={viewportRef}
        onWheel={(event) => {
          if (event.deltaY >= 0) return;
          userScrollRef.current = true;
          // A wheel-up while already parked at scrollTop 0 cannot change
          // scrollTop, so no scroll event ever fires and the trigger below
          // would never run. That strands the view at the top of a window
          // whose older pages still exist — e.g. after a byte-capped
          // refresh replaced the window while the view sat at the top — so
          // the wheel-up itself has to count as the trigger.
          const viewport = viewportRef.current;
          if (viewport && viewport.scrollTop <= 64) requestLoadOlder(viewport);
        }}
        onTouchStart={(event) => {
          touchStartYRef.current = event.touches[0]?.clientY ?? null;
        }}
        onTouchMove={(event) => {
          userScrollRef.current = true;
          // Same strand as the wheel above: a finger dragged down at
          // scrollTop 0 cannot move the viewport, so no scroll event fires
          // and the drag itself has to count as the up-intent trigger.
          const startY = touchStartYRef.current;
          const y = event.touches[0]?.clientY;
          if (startY == null || y == null || y <= startY) return;
          const viewport = viewportRef.current;
          if (viewport && viewport.scrollTop <= 64) requestLoadOlder(viewport);
        }}
        onKeyDown={(event) => {
          if (!["ArrowUp", "PageUp", "Home"].includes(event.key)) return;
          userScrollRef.current = true;
          // Same strand as the wheel above: at scrollTop 0 these keys
          // cannot move the viewport, so no scroll event ever fires.
          const viewport = viewportRef.current;
          if (viewport && viewport.scrollTop <= 64) requestLoadOlder(viewport);
        }}
        onScroll={(event) => requestLoadOlder(event.currentTarget)}
        turnAnchor="top"
        autoScroll={voiceLive || undefined}
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-scroll"
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
            {/* Keep transient status in the sticky footer so its mount/unmount
                does not change the positions of transcript rows above it. */}
            {historyStatus ? (
              <div
                data-slot="history-status"
                role={historyStatus.kind === "error" ? "alert" : "status"}
                aria-live={historyStatus.kind === "error" ? "assertive" : "polite"}
                className={cn(
                  "mx-auto flex w-full max-w-(--thread-max-width) items-center justify-between gap-3 rounded-md px-3 py-2 text-xs",
                  historyStatus.kind === "error"
                    ? "bg-destructive/10 text-destructive"
                    : "text-muted-foreground bg-muted/50",
                )}
              >
                <span>{historyStatus.message}</span>
                {historyStatus.kind === "error" && onRetryHistory ? (
                  <Button type="button" size="sm" variant="outline" onClick={onRetryHistory}>
                    Retry
                  </Button>
                ) : null}
              </div>
            ) : null}
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            <div data-slot="aui_composer-dock" className="flex min-w-0 items-end gap-2 sm:gap-3">
              {!isEmpty && ComposerLeading ? <ComposerLeading /> : null}
              <Composer
                agents={agents}
                commands={commands}
                commandCatalogStatus={commandCatalogStatus}
                onRetryCommands={onRetryCommands}
                onOpenPanel={onOpenPanel}
                onOpenWelcome={onOpenWelcome}
                attachmentsEnabled={attachmentsEnabled}
                draftKey={draftKey}
              />
            </div>
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
        className="aui-thread-scroll-to-bottom z-10 self-center rounded-md p-4 disabled:invisible"
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
          className="aui-thread-welcome-suggestion text-muted-foreground hover:text-foreground hover:bg-muted/50 h-auto gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-normal whitespace-nowrap transition-colors"
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
  commandCatalogStatus: NonNullable<ThreadProps["commandCatalogStatus"]>;
  onRetryCommands: ThreadProps["onRetryCommands"];
  onOpenPanel: ((request: PanelOpenRequest) => void) | undefined;
  onOpenWelcome: (() => void) | undefined;
  attachmentsEnabled: boolean;
  draftKey: ThreadProps["draftKey"];
}> = ({ agents, commands, commandCatalogStatus, onRetryCommands, onOpenPanel, onOpenWelcome, attachmentsEnabled, draftKey }) => {
  const aui = useAui();
  const sendTransportCommand = useAssistantTransportSendCommand();
  // The mic starts the LiveKit room in this browser; /voice (the computer
  // running the agent listens) stays a typed command, and its status line shows.
  const terminalVoice = useVoiceMode();
  const voice = useRoomVoiceMode() ?? terminalVoice;
  const placeholder = useContext(ComposerPlaceholderContext);
  // Text typed into a chat that is about to be swapped out would be lost, or
  // sent to the chat being left.
  const lock = useContext(ComposerLockContext);
  const { workspace, remote: remoteAgents } = useContext(ComposerHubContext);
  // The mic only exists where the session's live command list has /voice.
  const voiceAvailable = commands.some(
    (command) => command.name === "voice" && command.enabled !== false,
  );
  const permissionStop = usePendingPermissionCancellation();
  const sendWaitingNow = useSendWaitingNow();
  // The submit under way came from Cmd/Ctrl+Enter: the message goes at once.
  const submitSendsNow = useRef(false);
  const slotMode = useAuiState((s) =>
    composerSlotMode(s, voiceAvailable ? voice : null, Boolean(permissionStop)),
  );
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const composerText = useAuiState((s) => s.composer.text);
  const composerImages = useAuiState(
    useShallow((s) => s.composer.attachments),
  );
  const [orphanTokenNotice, setOrphanTokenNotice] = useState<string | null>(
    null,
  );
  const restoredDraft = useRef<{ key: string | null; text: string } | null>(null);
  const lastRestoredDraftKey = useRef<string | null | undefined>(undefined);
  const skipEmptyDraftPersist = useRef(false);
  const [busySubmitError, setBusySubmitError] = useState<string | null>(null);

  useLayoutEffect(() => {
    let cancelled = false;
    const key = draftKey ?? null;
    // Capability changes can rerun this effect after the attachment adapter
    // has already rewritten the image tokens. Restore each key once so a
    // single retained image cannot be attached twice.
    if (lastRestoredDraftKey.current === key) return;
    // Token lines belong to attachments, and attachments do not live in the
    // text draft. Strip them first, then restore what the same tab kept.
    const restored = stripImageTokens(loadComposerDraft(key) ?? "");
    restoredDraft.current = { key, text: restored.text };
    skipEmptyDraftPersist.current = false;
    if (aui.composer.getState().text !== restored.text) {
      aui.composer.setText(restored.text);
    }
    const restoreImages = async () => {
      const keptImages = loadComposerAttachments(key);
      if (keptImages.length === 0) {
        keptImages.push(...(await loadPersistedComposerAttachments(key)));
      }
      if (cancelled) return;
      if (attachmentsEnabled || keptImages.length === 0) {
        lastRestoredDraftKey.current = key;
      }
      setOrphanTokenNotice(
        orphanImageNotice(restored.tokenCount, attachmentsEnabled ? keptImages.length : 0),
      );
      if (keptImages.length === 0 || !attachmentsEnabled) return;

      // The runtime can put its own copy of the text back after the strip
      // above, and text typed next to a bare token shares its line. Let the
      // runtime finish its swap, then take the stale tokens off the text as
      // it is right now.
      await Promise.resolve();
      if (cancelled) return;
      const live = stripImageTokens(aui.composer.getState().text);
      const inline = stripLeadingImageTokens(live.text, keptImages.length - live.tokenCount);
      if (live.tokenCount + inline.count > 0) aui.composer.setText(inline.text);

      // Re-add each kept image; the token synchronizer rewrites fresh
      // [imageN] tokens, so text and attachments never disagree.
      let restoredImageCount = 0;
      for (const image of keptImages) {
        try {
          // Pending attachments restore through the adapter exactly as a
          // manual attach does; complete ones come back as descriptors.
          if (image.file instanceof File) {
            await aui.composer.addAttachment(image.file);
          } else {
            await aui.composer.addAttachment({
              type: "image",
              name: image.name,
              contentType: image.contentType,
              content: [...(image.content ?? [])] as ThreadUserMessagePart[],
            });
          }
          restoredImageCount += 1;
        } catch {
          // Continue restoring the remaining images after one fails.
        }
      }
      if (!cancelled) {
        setOrphanTokenNotice(
          orphanImageNotice(restored.tokenCount, restoredImageCount),
        );
      }
    };
    void restoreImages();
    return () => {
      cancelled = true;
    };
  }, [aui, draftKey, attachmentsEnabled]);

  // Keep the per-key image retention in step with the composer; a session
  // switch drops the runtime, the next mount reads this back.
  useEffect(() => {
    // Do not let the initial empty composer erase IndexedDB while restoration
    // is still reading this draft's stored attachments.
    if (restoredDraft.current?.key === (draftKey ?? null)) return;
    void saveComposerAttachments(
      draftKey ?? null,
      collectImageAttachments(composerImages),
    );
  }, [composerImages, draftKey]);

  // The orphan notice describes the restored draft; the first user edit or
  // send after the restore retires it.
  useEffect(() => {
    if (restoredDraft.current !== null) return;
    setOrphanTokenNotice(null);
  }, [composerText]);

  useEffect(() => {
    const key = draftKey ?? null;
    const restore = restoredDraft.current;
    if (restore?.key === key) {
      // setText publishes through the external store; don't let this effect's
      // stale render write an empty value over a draft being restored.
      if (composerText !== restore.text) return;
      restoredDraft.current = null;
    }
    if (!key) return;
    if (composerText.length === 0 && skipEmptyDraftPersist.current) {
      // assistant-ui clears the input as soon as submit starts. Keep the
      // durable copy until the transport confirms that add-message succeeded.
      skipEmptyDraftPersist.current = false;
      return;
    }
    if (composerText.length > 0) skipEmptyDraftPersist.current = false;
    saveComposerDraft(key, composerText);
  }, [composerText, draftKey]);

  const lastBusySubmit = useRef<{
    fingerprint: string;
    at: number;
    inFlight: Set<string>;
  } | null>(null);

  // A bare /welcome opens the local guide; panel commands open Settings and post
  // no text. Any other line is sent exactly as typed. Runs on form submit and Send.
  const openPanelFromComposer = (event: { preventDefault: () => void }) => {
    const { text, attachments } = aui.composer.getState();
    if (
      onOpenWelcome &&
      attachments.length === 0 &&
      text.trim().toLowerCase() === "/welcome"
    ) {
      event.preventDefault();
      aui.composer.setText("");
      onOpenWelcome();
      return;
    }
    if (!onOpenPanel) return;
    const request =
      attachments.length === 0 ? panelCommandRequest(text, commands) : null;
    if (!request) return;
    event.preventDefault();
    aui.composer.setText("");
    onOpenPanel(request);
  };

  const { plugins } = usePlugins();
  const pluginEntries = useMemo(
    () => pluginCommandEntries(plugins, reservedCommandKeys(commands)),
    [plugins, commands],
  );
  const pluginConflicts = useMemo(
    () => pluginCommandConflicts(plugins, reservedCommandKeys(commands)),
    [plugins, commands],
  );
  const reportedPluginConflicts = useRef(new Set<string>());
  useEffect(() => {
    const current = new Set<string>();
    const newConflicts = pluginConflicts.filter((conflict) => {
      const key = JSON.stringify([
        conflict.commandName.toLowerCase(),
        conflict.winnerPluginId,
        conflict.pluginId,
      ]);
      current.add(key);
      return !reportedPluginConflicts.current.has(key);
    });
    reportedPluginConflicts.current = current;
    warnPluginCommandConflicts(newConflicts);
  }, [pluginConflicts]);

  // A loaded plugin's /command typed in the composer runs here instead of
  // going to the agent. Returns true when it handled the submit.
  const runPluginCommand = (event: { preventDefault: () => void }) => {
    if (aui.composer.getState().attachments.length > 0) return false;
    const draft = aui.composer.getState().text;
    const match = matchPluginCommand(draft, pluginEntries);
    if (!match) return false;
    event.preventDefault();
    aui.composer.setText("");
    const fail = (error: unknown) => {
      if (!aui.composer.getState().text) aui.composer.setText(draft);
      match.entry.k.toast(pluginCommandFailure(match.entry.name, error));
    };
    try {
      const result = match.entry.run(match.args);
      if (result instanceof Promise) result.catch(fail);
    } catch (error) {
      fail(error);
    }
    return true;
  };

  // Picking a panel command in the slash menu (Enter or click) opens its tab
  // instead of leaving "/config" in the box for a second Enter.
  const sendBusyMessage = async (event: {
    preventDefault: () => void;
    defaultPrevented?: boolean;
  }) => {
    if (!isRunning || event.defaultPrevented) return;
    const sendsNow = submitSendsNow.current;
    const state = aui.composer.getState();
    if (!state.text.trim() && state.attachments.length === 0) return;

    const fingerprint = `${state.text}\u0000${state.attachments
      .map((attachment) => attachment.id)
      .join(",")}`;
    const previous = lastBusySubmit.current;
    const inFlight = previous?.inFlight ?? new Set();
    if (inFlight.has(fingerprint)) {
      event.preventDefault();
      return;
    }
    const now = Date.now();
    if (
      previous &&
      previous.fingerprint === fingerprint &&
      now - previous.at < 450
    ) {
      event.preventDefault();
      return;
    }
    lastBusySubmit.current = { fingerprint, at: now, inFlight };
    inFlight.add(fingerprint);
    event.preventDefault();
    setBusySubmitError(null);
    try {
      const attachments = await Promise.all(
        state.attachments.map(async (attachment) => {
          if (attachment.content) return attachment;
          if (
            attachment.type === "image" &&
            attachment.status.type === "requires-action"
          ) {
            return imageAttachmentAdapter.send(attachment as PendingAttachment);
          }
          throw new Error("Attachment is not ready to send");
        }),
      );
      const parts: Array<
        | { type: "text"; text: string }
        | { type: "image"; image: string }
      > = [];
      if (state.text.trim()) parts.push({ type: "text", text: state.text });
      for (const attachment of attachments) {
        for (const part of attachment.content ?? []) {
          if (part.type === "text") parts.push({ type: "text", text: part.text });
          if (part.type === "image") parts.push({ type: "image", image: part.image });
        }
      }
      if (parts.length === 0) throw new Error("Message has no sendable content");
      queueComposerDraftSubmission(draftKey, state.text);
      if (state.text.length > 0) skipEmptyDraftPersist.current = true;
      // The message waits for the turn to end, as a queued message (#1102).
      sendTransportCommand({
        type: "add-message",
        message: { role: "user", parts },
        parentId: null,
        sourceId: null,
      });
      if (sendsNow) sendWaitingNow?.();
      const currentState = aui.composer.getState();
      const currentFingerprint = `${currentState.text}\u0000${currentState.attachments
        .map((attachment) => attachment.id)
        .join(",")}`;
      if (currentFingerprint === fingerprint) {
        void aui.composer.reset();
      }
    } catch {
      if (lastBusySubmit.current?.fingerprint === fingerprint) {
        lastBusySubmit.current = {
          ...lastBusySubmit.current,
          at: Number.NEGATIVE_INFINITY,
        };
      }
      setBusySubmitError("Could not send the message. Retry or remove the attachment.");
    } finally {
      inFlight.delete(fingerprint);
    }
  };

  const submitBusyMessage = (event: {
    preventDefault: () => void;
    defaultPrevented?: boolean;
  }) => {
    if (runPluginCommand(event)) return;
    openPanelFromComposer(event);
    sendBusyMessage(event);
  };

  const handleComposerSubmit = (event: {
    preventDefault: () => void;
    defaultPrevented?: boolean;
  }) => {
    if (runPluginCommand(event)) return;
    // Keep panel commands on their existing path, then bypass assistant-ui's
    // disabled-send predicate while a turn is running. The transport already
    // queues this append message behind the active run.
    openPanelFromComposer(event);
    if (event.defaultPrevented) return;
    if (isRunning) {
      sendBusyMessage(event);
      return;
    }
    const text = aui.composer.getState().text;
    if (text.length > 0) {
      queueComposerDraftSubmission(draftKey, text);
      skipEmptyDraftPersist.current = true;
    }
  };

  const openPanelForItem = (item: {
    id: string;
    metadata?: Record<string, unknown> | undefined;
  }) => {
    // Plugin commands are inserted as text and run on submit, not from here.
    if (item.metadata?.category === "plugins") return;
    const insert = item.metadata?.insertText;
    const commandText = `/${typeof insert === "string" ? insert : item.id}`;
    if (
      onOpenWelcome &&
      aui.composer.getState().attachments.length === 0 &&
      commandText.trim().toLowerCase() === "/welcome"
    ) {
      aui.composer.setText("");
      onOpenWelcome();
      return;
    }
    if (!onOpenPanel || aui.composer.getState().attachments.length > 0) return;
    const request = panelCommandRequest(
      commandText,
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

  const pluginItems = useMemo(
    () => pluginCommandItems(pluginEntries, prettyCommandName),
    [pluginEntries],
  );
  const paletteItems = useMemo<readonly ComposerPaletteItem[]>(
    () => [...commandItems, ...pluginItems],
    [commandItems, pluginItems],
  );
  const slashNotice = slashCatalogNotice(commandCatalogStatus, pluginItems.length > 0);

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

  // While a turn runs the kit leaves Enter alone (it has no queue to put the
  // message in), and the message box starts a new line. Send the message as
  // the Send button does (#1365): it waits for the turn to end. Cmd+Enter
  // (Ctrl+Enter off a Mac) sends it, and every message waiting before it, at
  // once (#1102). This runs after the message box's own handler: an Enter the
  // slash or @ menu took has its default prevented, and Shift+Enter still
  // starts a new line.
  const handleBusyEnter = (event: KeyboardEvent<HTMLDivElement>) => {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      event.defaultPrevented ||
      !isRunning ||
      event.nativeEvent.isComposing ||
      event.nativeEvent.keyCode === 229 ||
      !(event.target instanceof HTMLTextAreaElement)
    ) {
      return;
    }
    event.preventDefault();
    // The submit handler runs inside requestSubmit and reads this at once.
    submitSendsNow.current = sendWaitingNow !== null && (event.metaKey || event.ctrlKey);
    event.target.form?.requestSubmit();
    submitSendsNow.current = false;
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
          (agent.agent_type && bundleName(agent.agent_type)) ||
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
        status: agentStatus(agent.state),
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
        status: agentStatus(agent.state),
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
        className="aui-composer-root group/composer relative flex min-w-0 flex-1 flex-col"
        data-mode={slotMode}
        onSubmit={handleComposerSubmit}
      >
        <ComposerPalette
          char="/"
          items={paletteItems}
          title="Commands"
          emptyMessage="No matching commands"
          emptyHint="Keep typing to refine the command search."
          onInserted={openPanelForItem}
        />
        {slashNotice && composerText.trimStart().startsWith("/") ? (
          <div role="status" className="bg-muted/50 text-muted-foreground flex items-center justify-between gap-3 px-3 py-2 text-xs">
            <span className="min-w-0">{slashNotice}</span>
            {(commandCatalogStatus === "fallback" || commandCatalogStatus === "unavailable") && onRetryCommands ? (
              <Button type="button" size="sm" variant="outline" onClick={onRetryCommands}>
                Try Again
              </Button>
            ) : null}
          </div>
        ) : null}
        <ComposerPalette
          char="@"
          items={agentItems}
          title="Message an agent"
          emptyMessage="No online agents"
          emptyHint="Try @broadcast to reach every agent in this project."
        />
        {busySubmitError ? (
          <p role="alert" className="text-destructive px-2 text-xs">
            {busySubmitError}
          </p>
        ) : null}
        <ComposerPrimitive.AttachmentDropzone
          asChild
          disabled={!attachmentsEnabled}
        >
          <div
            data-slot="aui_composer-shell"
            className="flex w-full flex-col gap-2 rounded-(--composer-radius) bg-(--composer-bg) focus-within:bg-[color-mix(in_oklab,var(--color-muted)_45%,var(--color-background))] p-(--composer-padding) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.04)] transition-[background-color,box-shadow] focus-within:shadow-[0_6px_24px_-8px_rgba(0,0,0,0.12),0_1px_2px_rgba(0,0,0,0.05)] data-[dragging=true]:bg-[color-mix(in_oklab,var(--color-accent)_50%,var(--color-background))] dark:shadow-none"
          >
            {orphanTokenNotice ? (
              <p
                role="status"
                className="aui-composer-orphan-notice text-muted-foreground/90 px-1 text-xs leading-4"
              >
                {orphanTokenNotice}
              </p>
            ) : null}
            <ComposerAttachments />
            {voiceAvailable && terminalVoice.on ? (
              <VoiceStatus activity={terminalVoice.activity} />
            ) : null}
            {voiceAvailable ? <VoiceRoomBar /> : null}
            <div
              className="aui-composer-row flex items-end gap-1.5"
              onKeyDown={handleBusyEnter}
            >
              {attachmentsEnabled ? <ComposerAddAttachment /> : null}
              <ComposerPrimitive.Input
                placeholder={lock || placeholder}
                disabled={Boolean(lock)}
                className="aui-composer-input caret-primary placeholder:text-muted-foreground/80 max-h-32 min-h-9 min-w-0 flex-1 resize-none bg-transparent px-1 py-1.5 text-base leading-6 outline-none pointer-fine:text-sm"
                rows={1}
                autoFocus
                enterKeyHint="send"
                aria-label="Message input"
                addAttachmentOnPaste={attachmentsEnabled}
                onKeyDown={handleComposerKeyDown}
              />
              <ComposerSlot
                voice={voiceAvailable ? voice : null}
                running={isRunning}
                permissionStop={permissionStop}
                onBeforeSend={(event) => {
                  if (runPluginCommand(event)) return;
                  openPanelFromComposer(event);
                }}
                onSteer={submitBusyMessage}
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
    .join(" ") || name;
}

type ComposerSlotMode = "mic" | "send" | "listening" | "stop";

// The keys that send a message into the running turn at once (#1102).
const SEND_NOW_KEYS = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform)
  ? "⌘+Enter" : "Ctrl+Enter";

// One trailing button slot that morphs: stop while a run is going, send once
// there is something to send, otherwise the voice mic (a mic, or the
// pulsing listening button while the voice room is up) where the session offers it.
const composerSlotMode = (
  s: AssistantState,
  voice: VoiceMode | null,
  awaitingPermission = false,
): ComposerSlotMode => {
  // A room starting or stopping (or a /voice command run); hold the mic still through it
  // rather than flashing stop.
  if (voice?.pending) return voice.on ? "listening" : "mic";
  if (!s.composer.isEmpty || !voice) return "send";
  if (s.thread.isRunning || awaitingPermission) return "stop";
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

// Mic loudness (0..1) at which each meter bar lights.
const VOICE_METER = [0.1, 0.3, 0.55, 0.8];

/** Voice mode at a glance: the mic level, what voice is doing, the last line. */
const VoiceStatus: FC<{ activity: VoiceActivity | null }> = ({ activity }) => {
  const level = activity?.level ?? 0;
  const line = activity?.heard ? `Heard “${activity.heard}”` : "";
  return (
    <div
      data-slot="aui_voice-status"
      className="flex min-w-0 items-center gap-2 px-1 text-xs text-muted-foreground"
    >
      <span className="flex h-3 shrink-0 items-end gap-0.5" aria-hidden>
        {VOICE_METER.map((at, index) => (
          <span
            key={at}
            className={cn(
              "w-0.5 rounded-sm bg-current transition-opacity duration-150",
              level >= at ? "opacity-100" : "opacity-25",
            )}
            style={{ height: `${(index + 1) * 25}%` }}
          />
        ))}
      </span>
      <span className="shrink-0 font-medium text-foreground capitalize">
        {activity?.stage ?? (activity ? "Connecting…" : "Voice ready")}
      </span>
      {line ? <span className="min-w-0 truncate">{line}</span> : null}
    </div>
  );
};

const ComposerSlot: FC<{
  voice: VoiceMode | null;
  running: boolean;
  permissionStop: { cancel: () => Promise<void>; cancelling: boolean } | null;
  onBeforeSend: (event: { preventDefault: () => void }) => void;
  onSteer: (event: { preventDefault: () => void }) => void;
}> = ({ voice, running, permissionStop, onBeforeSend, onSteer }) => {
  const sendsNow = useSendWaitingNow() !== null;
  return (
    <div
      className={cn(
        "aui-composer-slot relative h-9 shrink-0",
        running || permissionStop ? "w-20" : "w-9",
      )}
    >
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
      {running ? (
        <TooltipIconButton
          tooltip={sendsNow ? `Queue Message (${SEND_NOW_KEYS} Sends Now)` : "Queue Message"}
          side="top"
          type="button"
          variant="default"
          className={cn(SLOT_BUTTON, SLOT_SHOWS_SEND, "pointer-events-auto")}
          aria-label="Send message while generating"
          onClick={onSteer}
        >
          <ArrowUpIcon className="size-5" />
        </TooltipIconButton>
      ) : (
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
      )}
      {running ? (
        <ComposerPrimitive.Cancel asChild>
          <TooltipIconButton
            tooltip="Stop Generating"
            side="top"
            type="button"
            variant="default"
            className={cn(
              SLOT_BUTTON,
              "!left-auto !right-1 scale-100 opacity-100 visible",
            )}
            aria-label="Stop generating"
          >
            <SquareIcon className="size-3.5 fill-current" />
          </TooltipIconButton>
        </ComposerPrimitive.Cancel>
      ) : permissionStop ? (
        <TooltipIconButton
          tooltip="Stop Generating"
          side="top"
          type="button"
          variant="default"
          disabled={permissionStop.cancelling}
          onClick={() => { void permissionStop.cancel(); }}
          className={cn(
            SLOT_BUTTON,
            "!left-auto !right-1 scale-100 opacity-100 visible",
          )}
          aria-label="Stop generating"
        >
          <SquareIcon className="size-3.5 fill-current" />
        </TooltipIconButton>
      ) : null}
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root bg-destructive/10 text-destructive mt-2 rounded-md p-3 text-sm dark:text-red-200">
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

  // Older-page tool reconciliation can update this row in place; keep its
  // geometry in normal layout so the viewport can preserve its id anchor.
  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 animate-in relative -mb-7.5 pb-7.5 duration-150"
    >
      <div
        data-slot="aui_assistant-message-content"
        className="text-foreground px-2 leading-relaxed wrap-break-word pointer-fine:text-sm"
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
                  <ToolGroupRoot variant="ghost">
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
                return <PluginTaggedText text={part.text} />;
              case "reasoning":
                return <Reasoning {...part} />;
              case "tool-call":
                return (
                  <PluginToolPart
                    part={part}
                    fallback={part.toolUI ?? <ToolFallbackComponent {...part} />}
                  />
                );
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

/**
 * A user bubble's text: each [imageN] token becomes the thumbnail of the row's
 * Nth stored image (#1088) and opens the viewer at it. Without a stored copy
 * (an older row, or a picture the engine could not keep) the token stays text.
 * A live row carries its pictures as image parts, so there the tokens render
 * as nothing instead of duplicating the picture next to them.
 */
export const UserText: FC<{ text?: string }> = ({ text }) => {
  const media = useAuiState(
    (s) => s.message.metadata?.custom?.media,
  ) as MediaEntry[] | undefined;
  const imageParts = useAuiState(
    (s) => s.message.parts.filter((part) => part.type === "image").length,
  );
  const openViewer = useMediaViewer();
  const sessionMedia = useSessionMedia();
  if (!text) return null;
  const segments = splitMediaSegments(text);
  return (
    <>
      {segments.map((segment, index) => {
        // A live row shows its pictures as image parts, so its token
        // segments render as nothing instead of duplicating the attached
        // picture next to them (#1140 review). Typed [[imageN]] escapes
        // keep their literal text. The check runs before resolving: a live
        // row carries no media list, so resolving would misclassify.
        if (imageParts > 0 && segment.kind === "token") return null;
        const resolved = resolveMediaSegment(segment, media);
        if (resolved.kind === "text") return <span key={index}>{resolved.text}</span>;
        // Literal [imageN] text: the escaped form, or a slot whose picture
        // is missing or failed - it never requests a ref and never opens
        // the viewer (#1124 P2a).
        if (resolved.kind === "literal") return <span key={index}>{resolved.text}</span>;
        const { entry } = resolved;
        // The session list may lag behind a new message. Include valid refs
        // from this row so selection and navigation resolve by identity.
        const seenRefs = new Set<string>();
        const viewerItems = [...(sessionMedia ?? []), ...(media ?? [])].filter((item) => {
          if (!item.ref || item.failed || seenRefs.has(item.ref)) return false;
          seenRefs.add(item.ref);
          return true;
        });
        const viewerIndex = viewerItems.findIndex((item) => item.ref === entry.ref);
        return (
          <MediaThumb
            key={index}
            ref_={entry.ref}
            className="my-1 align-middle"
            onOpen={openViewer && viewerIndex >= 0 ? () => openViewer(viewerItems, viewerIndex) : undefined}
          />
        );
      })}
    </>
  );
};

// The row under a message that waits for the running turn to end. The thread's
// rows clip what hangs outside them, so the note has a row of its own. On a
// touch screen the row is taller: Send Now's 44px target reaches 14px above
// the line and 14px under it.
const QUEUED_NOTE_ROW = "col-start-2 h-4 any-pointer-coarse:h-[30px]";

// What the message is waiting for, and the way to send it into the turn at
// once (#1102). A session that cannot take a message that way (a harness
// session) gets the word alone.
const QueuedMessageNote: FC = () => {
  const sendWaitingNow = useSendWaitingNow();
  return (
    <TooltipProvider delayDuration={600}>
      <div className={cn("aui-user-message-queued text-muted-foreground flex items-start justify-end gap-2 text-xs leading-4", QUEUED_NOTE_ROW)}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span>Queued</span>
          </TooltipTrigger>
          <TooltipContent side="bottom">Sent when the agent finishes this turn</TooltipContent>
        </Tooltip>
        {sendWaitingNow ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="link"
                className="text-foreground h-4 p-0 text-xs leading-4 any-pointer-coarse:-mt-[14px] any-pointer-coarse:h-[44px] any-pointer-coarse:min-w-[44px]"
                onClick={sendWaitingNow}
              >
                Send Now
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">The agent reads it at its next step</TooltipContent>
          </Tooltip>
        ) : null}
      </div>
    </TooltipProvider>
  );
};

const UserMessage: FC = () => {
  const queued = useAuiState((s) => s.message.metadata.custom?.queued === true);
  // A message that was queued keeps the note's row, empty, once it is sent:
  // the rows under it stay where they are.
  const [wasQueued, setWasQueued] = useState(queued);
  if (queued && !wasQueued) setWasQueued(true);
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto] [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        <div className="aui-user-message-content peer bg-muted text-foreground rounded-xl px-4 py-2 whitespace-pre-wrap wrap-break-word empty:hidden pointer-fine:text-sm">
          <MessagePrimitive.Parts components={{ Text: UserText }} />
        </div>
        <div className="aui-user-action-bar-wrapper absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 peer-empty:hidden rtl:translate-x-full">
          <UserActionBar />
        </div>
      </div>
      {queued ? <QueuedMessageNote /> : wasQueued ? <div aria-hidden className={QUEUED_NOTE_ROW} /> : null}
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
      <ComposerPrimitive.Root className="aui-edit-composer-root ms-auto flex w-full max-w-[85%] flex-col rounded-(--composer-radius) bg-(--composer-bg) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.04)] dark:shadow-none">
        <ComposerPrimitive.Input
          className="aui-edit-composer-input text-foreground min-h-14 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-base outline-none pointer-fine:text-sm"
          autoFocus
        />
        <div className="aui-edit-composer-footer mx-2.5 mb-2.5 flex items-center gap-1.5 self-end">
          <ComposerPrimitive.Cancel asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 rounded-lg px-3.5"
            >
              Cancel
            </Button>
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send asChild>
            <Button size="sm" className="h-8 rounded-lg px-3.5">
              Update
            </Button>
          </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
};
