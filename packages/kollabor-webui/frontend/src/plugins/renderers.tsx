import type { ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  MarkdownText,
  defaultComponents,
} from "@/components/assistant-ui/markdown-text";
import { terminalDialectToMarkdown } from "@/utils/terminal-dialect";

import { PluginMount } from "./mount";
import { usePlugins } from "./registry";
import {
  findTagRenderer,
  findToolRenderer,
  tagRendererNames,
} from "./renderer-select";
import { splitPluginTags } from "./tagged-text";

/** The tool-call fields a plugin tool renderer receives. */
type ToolPart = {
  toolName: string;
  args: unknown;
  result?: unknown;
  status?: unknown;
};

/** A tool call. A loaded plugin's renderer for its tool wins; otherwise `fallback`. */
export function PluginToolPart({
  part,
  fallback,
}: {
  part: ToolPart;
  fallback: ReactNode;
}) {
  const { plugins } = usePlugins();
  const found = findToolRenderer(plugins, part.toolName);
  if (!found) return <>{fallback}</>;
  const Render = found.spec.render;
  return (
    <PluginMount record={found.record}>
      <Render
        k={found.k}
        toolCall={{
          name: part.toolName,
          args: part.args,
          result: part.result,
          status: part.status,
        }}
      />
    </PluginMount>
  );
}

/** Message text with plugin tags. With no tag renderers, or no tags in the text, the native markdown part renders it. */
export function PluginTaggedText({ text }: { text: string }) {
  const { plugins } = usePlugins();
  const tags = tagRendererNames(plugins);
  if (tags.length === 0) return <MarkdownText />;
  const parts = splitPluginTags(text, tags);
  if (parts.length === 1 && parts[0]?.type === "text") return <MarkdownText />;
  return (
    <div className="aui-md">
      {parts.map((part, index) => {
        if (part.type === "text") return <Prose key={index} text={part.text} />;
        const found = findTagRenderer(plugins, part.name);
        if (!found) return <Prose key={index} text={part.content} />;
        const Render = found.spec.render;
        return (
          <PluginMount key={index} record={found.record}>
            <Render k={found.k} tag={{ name: part.name, content: part.content }} />
          </PluginMount>
        );
      })}
    </div>
  );
}

/** Text outside plugin tags gets the same markdown rules as the native text part. */
export function Prose({ text }: { text: string }) {
  return (
    <Markdown remarkPlugins={[remarkGfm]} components={defaultComponents}>
      {terminalDialectToMarkdown(text)}
    </Markdown>
  );
}
