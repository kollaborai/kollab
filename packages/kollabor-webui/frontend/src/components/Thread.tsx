import { createContext, useContext, type FC, type ReactNode } from "react";
import {
  makeAssistantToolUI,
  MessagePrimitive,
  useAuiState,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import {
  ComposerPlaceholderContext,
  Thread as AssistantThread,
} from "@/components/assistant-ui/thread";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { ToolFallback, toolErrorLine } from "@/components/assistant-ui/tool-fallback";
import { ToolGroup } from "@/components/assistant-ui/tool-group";
import type { AgentPoolEntry, SlashCommand } from "@/api";
import { GemAvatar } from "@/components/gems/GemAvatar";
import type { HubNote } from "@/hub-notes";
import type { PanelOpenRequest } from "@/components/panels/panel-model";
import { titleCase } from "@/components/panels/panel-model";
import { cn } from "@/lib/utils";

type WelcomeGem = { name: string; pool?: AgentPoolEntry };

const WelcomeContext = createContext<WelcomeGem | null>(null);
const PoolContext = createContext<readonly AgentPoolEntry[]>([]);

// kollab-branded welcome screen. Replaces the kit's generic ThreadWelcome via
// the `components.Welcome` slot; everything else (messages, tool calls,
// reasoning, composer) renders exactly as the kit ships it. A session with a
// gem greets as that gem, live and watching the pointer.
const Welcome: FC = () => {
  const gem = useContext(WelcomeContext);
  return (
    <div className="mb-6 flex flex-col items-center gap-3 px-4 text-center">
      {gem ? (
        <GemAvatar
          gem={gem.name}
          caste={gem.pool?.caste}
          color={gem.pool?.color}
          state={gem.pool?.active ? gem.pool.state : "idle"}
          live
          season="auto"
          follow
          size={112}
          label={titleCase(gem.name)}
        />
      ) : null}
      <h1 className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-2xl font-semibold duration-200">
        {gem ? titleCase(gem.name) : "kollab"}
      </h1>
      <p className="text-muted-foreground fade-in slide-in-from-bottom-1 animate-in fill-mode-both max-w-sm text-sm text-balance duration-200">
        Everything has hooks. Send a message to begin.
      </p>
    </div>
  );
};

// A hub message, sent or received: the sender's gem, "From → To" and the
// words, the way the terminal draws its hub box.
const HubNoteView: FC<{ from: string; to: string; children: ReactNode; footer?: ReactNode }> = ({
  from,
  to,
  children,
  footer,
}) => {
  const sender = useContext(PoolContext).find((agent) => agent.name === from);
  return (
    <div className="flex gap-3">
      <GemAvatar
        gem={from}
        caste={sender?.caste}
        color={sender?.color}
        state={sender?.active ? sender.state : "idle"}
        size={28}
        className="mt-0.5 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="text-muted-foreground mb-1 text-xs font-medium">
          {titleCase(from)} → {to ? titleCase(to) : "All"}
        </div>
        <div className="text-foreground wrap-break-word">{children}</div>
        {footer}
      </div>
    </div>
  );
};

// Another agent's hub message (runtime.tsx turns the daemon's agent HUD into
// these). One this agent only saw go by is dimmed, like the terminal's ◇.
const HubNoteMessage: FC = () => {
  const note = useAuiState((s) => s.message.metadata.custom["hub"]) as HubNote | undefined;
  return (
    <MessagePrimitive.Root
      data-slot="kollab_hub-message"
      data-role="system"
      className={cn(
        "fade-in slide-in-from-bottom-1 animate-in px-2 duration-150",
        note?.observed && "opacity-60",
      )}
    >
      <HubNoteView from={note?.from ?? ""} to={note?.to ?? ""}>
        <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
      </HubNoteView>
    </MessagePrimitive.Root>
  );
};

type HubSendArgs = { to?: string; target?: string; message?: string; content?: string };

// What this agent sent over the hub (native calls pass to/message, the XML
// tags target/content), drawn like the messages it receives instead of as a
// tool row; standalone keeps it out of the collapsed tool group.
const HubSent: ToolCallMessagePartComponent<HubSendArgs, unknown> = (props) => {
  const gem = useContext(WelcomeContext);
  const text = String(props.args?.message ?? props.args?.content ?? "").trim();
  if (!gem || !text) return <ToolFallback {...props} />;
  const to = props.toolName === "hub_broadcast" ? "" : String(props.args?.to ?? props.args?.target ?? "");
  return (
    <div
      data-slot="kollab_hub-sent"
      className={cn("my-3", props.status.type === "running" && "opacity-70")}
    >
      <HubNoteView
        from={gem.name}
        to={to}
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
}> = ({ agents = [], commands = [], onOpenPanel, attachmentsEnabled = true, identity }) => (
  <PoolContext.Provider value={agents}>
    <WelcomeContext.Provider
      value={identity ? { name: identity, pool: agents.find((agent) => agent.name === identity) } : null}
    >
      <ComposerPlaceholderContext.Provider
        value={identity ? `Message ${titleCase(identity)}…` : "Send a message..."}
      >
        {HUB_SEND_TOOL_UIS.map((HubSendToolUI, index) => (
          <HubSendToolUI key={index} />
        ))}
        <AssistantThread
          components={{ Welcome, ToolGroup, SystemMessage: HubNoteMessage }}
          agents={agents}
          commands={commands}
          onOpenPanel={onOpenPanel}
          attachmentsEnabled={attachmentsEnabled}
        />
      </ComposerPlaceholderContext.Provider>
    </WelcomeContext.Provider>
  </PoolContext.Provider>
);
