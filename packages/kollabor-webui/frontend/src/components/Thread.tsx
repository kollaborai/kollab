import { createContext, useContext, type FC } from "react";
import {
  ComposerPlaceholderContext,
  Thread as AssistantThread,
} from "@/components/assistant-ui/thread";
import { ToolGroup } from "@/components/assistant-ui/tool-group";
import type { AgentPoolEntry, SlashCommand } from "@/api";
import { GemAvatar } from "@/components/gems/GemAvatar";
import type { PanelOpenRequest } from "@/components/panels/panel-model";
import { titleCase } from "@/components/panels/panel-model";

type WelcomeGem = { name: string; pool?: AgentPoolEntry };

const WelcomeContext = createContext<WelcomeGem | null>(null);

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
  <WelcomeContext.Provider
    value={identity ? { name: identity, pool: agents.find((agent) => agent.name === identity) } : null}
  >
    <ComposerPlaceholderContext.Provider
      value={identity ? `Message ${titleCase(identity)}…` : "Send a message..."}
    >
      <AssistantThread
        components={{ Welcome, ToolGroup }}
        agents={agents}
        commands={commands}
        onOpenPanel={onOpenPanel}
        attachmentsEnabled={attachmentsEnabled}
      />
    </ComposerPlaceholderContext.Provider>
  </WelcomeContext.Provider>
);
