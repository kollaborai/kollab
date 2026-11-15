import { useContext, type ReactNode } from "react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { PluginRenderBoundary, PluginSessionContext, PluginTurnOffContext, useOptionalPluginSnapshot, usePluginHost } from "./components";

/** Preserve the native tool fallback when no successfully loaded plugin claims it. */
export function PluginToolPart({ part, fallback }: { part: ToolCallMessagePartProps; fallback: ReactNode }) {
  const host = usePluginHost();
  const snapshot = useOptionalPluginSnapshot(host);
  const session = useContext(PluginSessionContext);
  const turnOff = useContext(PluginTurnOffContext);
  const match = snapshot.registry.plugins.flatMap(plugin => plugin.renderers.map(renderer => ({ pluginId: plugin.id, renderer })))
    .find(candidate => candidate.renderer.tool === part.toolName);
  const context = match && host?.getContext(match.pluginId);
  if (!match || !host || !context) return fallback;
  const Render = match.renderer.render;
  return <div data-plugin><PluginRenderBoundary name={match.pluginId} onError={error => host.reportRenderError(match.pluginId, error)}
    onRetry={() => { void host.retry(match.pluginId); }} onTurnOff={() => turnOff(match.pluginId)}>
    <Render k={context} session={session} toolCall={{ name: part.toolName, args: part.args, result: part.result, status: part.status }} />
  </PluginRenderBoundary></div>;
}
