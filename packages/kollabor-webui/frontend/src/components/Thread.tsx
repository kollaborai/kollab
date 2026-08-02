import type { FC } from "react";
import { Thread as AssistantThread } from "@/components/assistant-ui/thread";

// kollab-branded welcome screen. Replaces the kit's generic ThreadWelcome via
// the `components.Welcome` slot; everything else (messages, tool calls,
// reasoning, composer) renders exactly as the kit ships it.
const Welcome: FC = () => (
  <div className="mb-6 flex flex-col items-center gap-2 px-4 text-center">
    <h1 className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-2xl font-semibold duration-200">
      kollab
    </h1>
    <p className="text-muted-foreground fade-in slide-in-from-bottom-1 animate-in fill-mode-both max-w-sm text-sm duration-200">
      Everything has hooks. Send a message to start this session.
    </p>
  </div>
);

// Thin adapter over the assistant-ui kit's Thread. Registered tool UIs
// (PermissionToolUI, mounted in runtime.tsx) resolve via `part.toolUI` inside
// the kit's own AssistantMessage and take precedence over ToolFallback — see
// components/assistant-ui/thread.tsx line ~398.
export const Thread: FC = () => <AssistantThread components={{ Welcome }} />;
