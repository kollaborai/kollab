import { createContext, useContext, type FC, type ReactNode } from "react";
import { ChevronDownIcon } from "lucide-react";
import {
  makeAssistantToolUI,
  MessagePrimitive,
  useAuiState,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import {
  ComposerPlaceholderContext,
  ComposerLockContext,
  ComposerHubContext,
  Thread as AssistantThread,
} from "@/components/assistant-ui/thread";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { ToolFallback, toolErrorLine } from "@/components/assistant-ui/tool-fallback";
import { ToolGroup } from "@/components/assistant-ui/tool-group";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import type { AgentPoolEntry, NetworkAgent, SlashCommand } from "@/api";
import { Avatar } from "@/components/agents/Avatar";
import type { Activity } from "@/components/gems/gem-face";
import { resolveAgentSender, type HubNote } from "@/hub-notes";
import type { PanelOpenRequest } from "@/components/panels/panel-model";
import { agentName } from "@/utils/session-display";
import { cn } from "@/lib/utils";

type WelcomeGem = { name: string; pool?: AgentPoolEntry; activity?: Activity | null; onOpen?: () => void };

const WelcomeContext = createContext<WelcomeGem | null>(null);
const PoolContext = createContext<readonly AgentPoolEntry[]>([]);
/** Where the session's agent runs (its device, else its folder): a bare hub name is its neighbor there. */
const WhereContext = createContext("");

// kollab-branded welcome screen. Replaces the kit's generic ThreadWelcome via
// the `components.Welcome` slot; everything else (messages, tool calls,
// reasoning, composer) renders exactly as the kit ships it. A session with a
// gem greets as that gem, live and watching the pointer; double-click it for
// the session's Properties.
const Welcome: FC = () => {
  const gem = useContext(WelcomeContext);
  const where = useContext(WhereContext);
  return (
    <div className="mb-6 flex flex-col items-center gap-3 px-4 text-center">
      {gem ? (
        <span className="contents" onDoubleClick={gem.onOpen}>
          <Avatar
            gem={gem.name}
            avatar={gem.pool?.avatar}
            displayName={gem.pool?.display_name}
            where={where}
            caste={gem.pool?.caste}
            color={gem.pool?.color}
            state={gem.pool?.active ? gem.pool.state : "idle"}
            live
            season="auto"
            follow
            size={112}
            label={agentName(gem.name, gem.pool?.display_name)}
          />
        </span>
      ) : null}
      <h1 className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-2xl font-semibold duration-200">
        {gem ? agentName(gem.name, gem.pool?.display_name) : "kollab"}
      </h1>
      <p className="text-muted-foreground fade-in slide-in-from-bottom-1 animate-in fill-mode-both max-w-sm text-sm text-balance duration-200">
        Everything has hooks. Send a message to begin.
      </p>
    </div>
  );
};

const ComposerGem: FC = () => {
  const gem = useContext(WelcomeContext);
  const where = useContext(WhereContext);
  if (!gem) return null;

  return (
    <button
      type="button"
      data-slot="kollab_composer-gem"
      data-activity={gem.activity ?? undefined}
      className="inline-flex shrink-0 items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      aria-label={`Open ${agentName(gem.name, gem.pool?.display_name)} Properties`}
      title={`${agentName(gem.name, gem.pool?.display_name)} · Properties`}
      onClick={gem.onOpen}
      disabled={!gem.onOpen}
    >
      <Avatar
        gem={gem.name}
        avatar={gem.pool?.avatar}
        displayName={gem.pool?.display_name}
        where={where}
        caste={gem.pool?.caste}
        color={gem.pool?.color}
        state={gem.pool?.active ? gem.pool.state : "idle"}
        live
        activity={gem.activity}
        season="auto"
        follow
        size={56}
      />
    </button>
  );
};

// "lapis@devbox" is lapis on another computer.
const agentLabel = (id: string, pool: readonly AgentPoolEntry[]) => {
  const [name, device] = id.split("@");
  const label = agentName(name, pool.find((agent) => agent.name === name)?.display_name);
  return device ? `${label} on ${device}` : label;
};

// A hub message, sent or received: the sender's gem, "From → To" and the
// words, the way the terminal draws its hub box.
const HubNoteView: FC<{
  from: string;
  to: string;
  threadId?: string;
  replyTo?: string;
  isReply?: boolean;
  children: ReactNode;
  footer?: ReactNode;
}> = ({
  from,
  to,
  threadId,
  replyTo,
  isReply = false,
  children,
  footer,
}) => {
  const pool = useContext(PoolContext);
  const { remote: remoteAgents } = useContext(ComposerHubContext);
  const where = useContext(WhereContext);
  const gem = useContext(WelcomeContext);

  const resolvedFrom = resolveAgentSender(from, { agents: pool, remoteAgents });
  const [name, device] = resolvedFrom.split("@");
  const sender = pool.find((agent) => agent.name === name);

  const resolvedTo = to ? resolveAgentSender(to, { agents: pool, remoteAgents }) : "";
  const recipient = resolvedTo
    ? agentLabel(resolvedTo, pool)
    : gem?.name
      ? agentLabel(gem.name, pool)
      : "All";

  const label = isReply
    ? `Replied to ${recipient}`
    : `${agentLabel(resolvedFrom, pool)} → ${recipient}`;

  return (
    <div className="flex gap-3">
      <Avatar
        gem={name}
        avatar={sender?.avatar}
        displayName={sender?.display_name}
        where={device || where}
        caste={sender?.caste}
        color={sender?.color}
        state={sender?.active ? sender.state : "idle"}
        size={28}
        className="mt-0.5 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="text-muted-foreground mb-1 flex flex-wrap items-center gap-1 text-xs font-medium">
          <span>{label}</span>
        </div>
        <div className="text-foreground leading-relaxed wrap-break-word pointer-fine:text-sm">{children}</div>
        {footer}
      </div>
    </div>
  );
};

// Presence announcements (came online, went offline) read as quiet activity;
// details remain available on demand.
const PresenceChip: FC<{ note: HubNote }> = ({ note }) => {
  const pool = useContext(PoolContext);
  const { remote: remoteAgents } = useContext(ComposerHubContext);
  const where = useContext(WhereContext);
  const resolvedFrom = resolveAgentSender(
    note.from,
    { agents: pool, remoteAgents },
    note.rawText || note.text,
  );
  const [name, device] = resolvedFrom.split("@");
  const sender = pool.find((agent) => agent.name === name);

  const lines = note.text.trim().split("\n");
  const details = lines.slice(1).join("\n").trim();
  const summary = (
    <>
      <Avatar
        gem={name}
        avatar={sender?.avatar}
        displayName={sender?.display_name}
        where={device || where}
        caste={sender?.caste}
        color={sender?.color}
        state={sender?.active ? sender.state : "idle"}
        size={18}
        className="shrink-0"
      />
      <span className="min-w-0 truncate text-start">{agentName(name, sender?.display_name)} {note.presence === "offline" ? "went offline" : "came online"}</span>
      {details ? <ChevronDownIcon className="size-3 shrink-0 transition-transform group-data-[state=open]/chip:rotate-180" /> : null}
    </>
  );

  return (
    <div data-slot="kollab_online-notice" className="w-full">
      <Collapsible defaultOpen={false}>
        <CollapsibleTrigger
          data-slot="kollab_online-chip-trigger"
          title={agentLabel(resolvedFrom, pool)}
          className="group/chip inline-flex max-w-full cursor-pointer items-center gap-2 rounded-sm py-1 text-xs text-muted-foreground transition-colors select-none hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {summary}
        </CollapsibleTrigger>
        {details ? (
          <CollapsibleContent
            data-slot="kollab_online-chip-content"
            className="mt-1 max-h-48 overflow-y-auto pl-6 text-xs whitespace-pre-wrap text-muted-foreground"
          >
            {details}
          </CollapsibleContent>
        ) : null}
      </Collapsible>
    </div>
  );
};

// Another agent's hub message (runtime.tsx turns the daemon's agent HUD into
// these). One this agent only saw go by is dimmed, like the terminal's ◇.
const HubNoteMessage: FC = () => {
  const note = useAuiState((s) => s.message.metadata.custom["hub"]) as HubNote | undefined;
  if (note?.presence) {
    // A run of presence chips stacks tight instead of taking a thread gap each.
    return (
      <MessagePrimitive.Root
        data-slot="kollab_hub-message"
        data-role="system"
        data-presence={note.presence}
        className="fade-in slide-in-from-bottom-1 animate-in px-2 duration-150 [[data-presence]+&]:-mt-5"
      >
        <PresenceChip note={note} />
      </MessagePrimitive.Root>
    );
  }
  return (
    <MessagePrimitive.Root
      data-slot="kollab_hub-message"
      data-role="system"
      className={cn(
        "fade-in slide-in-from-bottom-1 animate-in px-2 duration-150",
        note?.observed && "opacity-60",
      )}
    >
      <HubNoteView from={note?.from ?? ""} to={note?.to ?? ""} threadId={note?.threadId} replyTo={note?.replyTo}>
        <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
      </HubNoteView>
    </MessagePrimitive.Root>
  );
};

type HubSendArgs = { to?: string; target?: string; message?: string; content?: string; thread?: string; thread_id?: string; reply_to?: string; replyTo?: string };

// What this agent sent over the hub (native calls pass to/message, the XML
// tags target/content), drawn like the messages it receives instead of as a
// tool row; standalone keeps it out of the collapsed tool group.
const HubSent: ToolCallMessagePartComponent<HubSendArgs, unknown> = (props) => {
  const gem = useContext(WelcomeContext);
  const text = String(props.args?.message ?? props.args?.content ?? "").trim();
  if (!gem || !text) return <ToolFallback {...props} />;
  const to = props.toolName === "hub_broadcast" ? "" : String(props.args?.to ?? props.args?.target ?? "");
  const isReply = props.toolName === "hub_reply";
  const threadId = String(props.args?.thread_id ?? props.args?.thread ?? "") || undefined;
  const replyTo = String(props.args?.reply_to ?? props.args?.replyTo ?? "") || undefined;
  return (
    <div
      data-slot="kollab_hub-sent"
      className={cn("my-3", props.status.type === "running" && "opacity-70")}
    >
      <HubNoteView
        from={gem.name}
        to={to}
        threadId={threadId}
        replyTo={replyTo}
        isReply={isReply}
        footer={
          props.isError ? (
            <p className="text-destructive mt-1 text-xs">Not delivered: {toolErrorLine(props.result)}</p>
          ) : null
        }
      >
        <p className="whitespace-pre-wrap">{text}</p>
      </HubNoteView>
    </div>
  );
};

const HUB_SEND_TOOL_UIS = ["hub_msg", "hub_reply", "hub_broadcast"].map((toolName) =>
  makeAssistantToolUI<HubSendArgs, unknown>({ toolName, display: "standalone", render: HubSent }),
);

// Thin adapter over the assistant-ui kit's Thread. Registered tool UIs
// (PermissionToolUI, mounted in runtime.tsx) resolve via `part.toolUI` inside
// the kit's own AssistantMessage and take precedence over ToolFallback — see
// components/assistant-ui/thread.tsx line ~398.
export const Thread: FC<{
  agents?: readonly AgentPoolEntry[];
  commands?: readonly SlashCommand[];
  onOpenPanel?: (request: PanelOpenRequest) => void;
  /** False when the session's model cannot read images. */
  attachmentsEnabled?: boolean;
  /** The session's gem, which greets on the empty thread. */
  identity?: string;
  /** The selected session's live activity, shared with its sidebar gem. */
  activity?: Activity | null;
  /** Opens the session's Properties (double-click on the greeting gem). */
  onOpenGem?: () => void;
  /** Agents on the network's other computers, for the @ picker. */
  remoteAgents?: readonly NetworkAgent[];
  /** The session's project folder: @ offers only the live agents in it. */
  workspace?: string;
  /** The computer the session's agent runs on, when it is another one. */
  device?: string;
  /** Why the composer is closed while another chat starts or loads; empty when open. */
  locked?: string;
}> = ({ agents = [], commands = [], onOpenPanel, attachmentsEnabled = true, identity, activity, onOpenGem, remoteAgents = [], workspace = "", device = "", locked = "" }) => (
  <PoolContext.Provider value={agents}>
  <WhereContext.Provider value={device || workspace}>
  <ComposerHubContext.Provider value={{ workspace, remote: remoteAgents }}>
    <WelcomeContext.Provider
      value={
        identity
          ? { name: identity, pool: agents.find((agent) => agent.name === identity), activity, onOpen: onOpenGem }
          : null
      }
    >
      <ComposerPlaceholderContext.Provider
        value={identity ? `Message ${agentName(identity, agents.find((agent) => agent.name === identity)?.display_name)}…` : "Send a message..."}
      >
        {HUB_SEND_TOOL_UIS.map((HubSendToolUI, index) => (
          <HubSendToolUI key={index} />
        ))}
        <ComposerLockContext.Provider value={locked}>
          <AssistantThread
            components={{ Welcome, ComposerLeading: ComposerGem, ToolGroup, SystemMessage: HubNoteMessage }}
            agents={agents}
            commands={commands}
            onOpenPanel={onOpenPanel}
            attachmentsEnabled={attachmentsEnabled}
          />
        </ComposerLockContext.Provider>
      </ComposerPlaceholderContext.Provider>
    </WelcomeContext.Provider>
  </ComposerHubContext.Provider>
  </WhereContext.Provider>
  </PoolContext.Provider>
);
