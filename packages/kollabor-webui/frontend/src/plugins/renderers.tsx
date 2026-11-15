import { useContext, type ReactNode } from "react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { terminalDialectToMarkdown } from "@/utils/terminal-dialect";
import { splitPluginTags } from "./tagged-text";
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

/** Only declared, complete tags are replaced; all other text uses native Markdown. */
export function PluginTaggedText({ text }: { text: string }) {
  const host = usePluginHost();
  const snapshot = useOptionalPluginSnapshot(host);
  const session = useContext(PluginSessionContext);
  const turnOff = useContext(PluginTurnOffContext);
  const renderers = snapshot.registry.plugins.flatMap(plugin => plugin.renderers
    .filter(renderer => renderer.tag)
    .map(renderer => ({ pluginId: plugin.id, renderer })));
  if (!host || !renderers.length) return <MarkdownText />;
  const parts = splitPluginTags(text, renderers.map(item => item.renderer.tag!));
  if (!parts.some(part => part.type === "tag")) return <MarkdownText />;
  return <>{parts.map((part, index) => {
    if (part.type === "text") return <MarkdownTextPrimitive key={index} className="aui-md" remarkPlugins={[remarkGfm]}
      preprocess={() => terminalDialectToMarkdown(part.text)} />;
    const match = renderers.find(item => item.renderer.tag === part.name);
    const context = match && host.getContext(match.pluginId);
    if (!match || !context) return null;
    const Render = match.renderer.render;
    return <div data-plugin key={index}><PluginRenderBoundary name={match.pluginId} onError={error => host.reportRenderError(match.pluginId, error)}
      onRetry={() => { void host.retry(match.pluginId); }} onTurnOff={() => turnOff(match.pluginId)}>
      <Render k={context} session={session} tag={{ name: part.name, content: part.content }} />
    </PluginRenderBoundary></div>;
  })}</>;
}
