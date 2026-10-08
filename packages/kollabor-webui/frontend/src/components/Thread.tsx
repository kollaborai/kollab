import { createContext, useContext, type FC } from "react";
import { MessagePrimitive, useAuiState } from "@assistant-ui/react";
import {
  ComposerPlaceholderContext,
  Thread as AssistantThread,
} from "@/components/assistant-ui/thread";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
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

// Another agent's hub message (runtime.tsx turns the daemon's agent HUD into
// these), drawn as a message from that gem the way the terminal draws its hub
// box. One this agent only saw go by is dimmed, like the terminal's ◇.
const HubNoteMessage: FC = () => {
  const pool = useContext(PoolContext);
  const note = useAuiState((s) => s.message.metadata.custom["hub"]) as HubNote | undefined;
  const sender = note ? pool.find((agent) => agent.name === note.from) : undefined;
  return (
    <MessagePrimitive.Root
      data-slot="kollab_hub-message"
      data-role="system"
      className={cn(
        "fade-in slide-in-from-bottom-1 animate-in flex gap-3 px-2 duration-150",
        note?.observed && "opacity-60",
      )}
    >
      {note ? (
        <GemAvatar
          gem={note.from}
          caste={sender?.caste}
          color={sender?.color}
          state={sender?.active ? sender.state : "idle"}
          size={28}
          className="mt-0.5 shrink-0"
        />
      ) : null}
      <div className="min-w-0 flex-1">
        {note ? (
          <div className="text-muted-foreground mb-1 text-xs font-medium">
            {titleCase(note.from)} → {note.to ? titleCase(note.to) : "All"}
          </div>
        ) : null}
        <div className="text-foreground wrap-break-word">
          <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
};

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
