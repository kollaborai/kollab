import { type ComponentProps, type ComponentType, type ReactNode, useCallback, useId, useMemo, useRef } from "react";

import {
  MarkdownText,
  defaultComponents,
} from "@/components/assistant-ui/markdown-text";
import type { MarkdownTextPrimitiveProps } from "@assistant-ui/react-markdown";
import type { ExtraProps } from "react-markdown";
import { terminalDialectToMarkdown } from "@/utils/terminal-dialect";

import { PluginMount } from "./mount";
import { usePlugins } from "./registry";
import {
  findTagRenderer,
  findToolRenderer,
  tagRendererNames,
} from "./renderer-select";
import { splitPluginTags } from "./tagged-text";

const PLUGIN_TAG_SLOT_HOST = "plugin-tag.kollab.invalid";

function pluginTagSlotHref(owner: string, index: number, name: string, content: string): string {
  const params = new URLSearchParams({ owner, index: String(index), name, content });
  return `https://${PLUGIN_TAG_SLOT_HOST}/?${params.toString()}`;
}

function pluginTagFromSlotHref(
  href: string | undefined,
  owner: string,
): { name: string; content: string } | null {
  if (!href) return null;
  try {
    const url = new URL(href);
    if (url.hostname !== PLUGIN_TAG_SLOT_HOST || url.searchParams.get("owner") !== owner) return null;
    const name = url.searchParams.get("name");
    const content = url.searchParams.get("content");
    return name && content !== null ? { name, content } : null;
  } catch {
    return null;
  }
}

/** The tool-call fields a plugin tool renderer receives. */
type ToolPart = {
  toolName: string;
  args: unknown;
  result?: unknown;
  status?: unknown;
  toolUI?: ReactNode;
};

function toolRendererResetKey(part: ToolPart): string {
  const status = part.status;
  const statusType = status && typeof status === "object" && "type" in status
    ? String(status.type)
    : String(status ?? "");
  return `${statusType}:${part.result === undefined ? "pending" : "complete"}`;
}

/** A registered host UI wins; otherwise a loaded plugin renderer, then `fallback`. */
export function PluginToolPart({
  part,
  fallback,
}: {
  part: ToolPart;
  fallback: ReactNode;
}) {
  if (part.toolUI != null) return <>{fallback}</>;
  const { plugins } = usePlugins();
  const found = findToolRenderer(plugins, part.toolName);
  if (!found) return <>{fallback}</>;
  const Render = found.spec.render;
  return (
    <PluginMount
      record={found.record}
      renderer={`tool:${part.toolName}`}
      resetKey={toolRendererResetKey(part)}
      errorFallback={fallback}
    >
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
  const pluginsRef = useRef(plugins);
  pluginsRef.current = plugins;
  const tags = tagRendererNames(plugins);
  const owner = useId();
  const parts = tags.length === 0 ? [] : splitPluginTags(text, tags);
  const hasPluginTags = parts.some((part) => part.type === "tag");
  const components = useMemo<NonNullable<MarkdownTextPrimitiveProps["components"]>>(() => {
    const NativeAnchor = defaultComponents.a as
      | ComponentType<ComponentProps<"a"> & ExtraProps>
      | undefined;
    return {
      ...defaultComponents,
      a: ({ href, ...props }) => {
        const tag = pluginTagFromSlotHref(href, owner);
        if (!tag) {
          return NativeAnchor ? (
            <NativeAnchor href={href} {...props} />
          ) : (
            <a href={href} target="_blank" rel="noopener noreferrer" {...props} />
          );
        }
        const found = findTagRenderer(pluginsRef.current, tag.name);
        if (!found) return <>{`<${tag.name}>${tag.content}</${tag.name}>`}</>;
        const Render = found.spec.render;
        return (
          <PluginMount record={found.record} renderer={`tag:${tag.name}`} resetKey={tag.content} inline>
            <Render k={found.k} tag={tag} />
          </PluginMount>
        );
      },
    };
  }, [owner]);
  const preprocess = useCallback(
    (source: string) => {
      const sourceParts = source === text ? parts : splitPluginTags(source, tags);
      const markdown = sourceParts
        .map((part, index) =>
          part.type === "text"
            ? part.text
            : `[plugin](${pluginTagSlotHref(owner, index, part.name, part.content)})`,
        )
        .join("");
      return terminalDialectToMarkdown(markdown);
    },
    [owner, parts, tags, text],
  );
  if (tags.length === 0) return <MarkdownText />;
  if (!hasPluginTags) return <MarkdownText />;
  return <MarkdownText components={components} preprocess={preprocess} />;
}
