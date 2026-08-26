"use client";

import type {
  Unstable_DirectiveFormatter,
  Unstable_TriggerAdapter,
  Unstable_TriggerItem,
} from "@assistant-ui/core";
import {
  ComposerPrimitive,
  unstable_useTriggerPopoverScopeContext,
} from "@assistant-ui/react";
import {
  BotIcon,
  ChevronRightIcon,
  CommandIcon,
  CornerDownRightIcon,
  MegaphoneIcon,
  SearchIcon,
} from "lucide-react";
import { Fragment, useEffect, useMemo, type FC } from "react";
import { cn } from "@/lib/utils";

export type ComposerPaletteItem = {
  id: string;
  label: string;
  description?: string;
  type: "command" | "agent";
  searchText?: string;
  secondary?: string;
  status?: string;
  icon?: "command" | "agent" | "broadcast";
  insertText?: string;
  commandPath?: string;
  category?: string;
  parentId?: string;
  depth?: number;
  args?: string;
};

type ComposerPaletteProps = {
  char: "/" | "@";
  items: readonly ComposerPaletteItem[];
  title: string;
  emptyMessage: string;
  emptyHint: string;
};

/**
 * Shared trigger palette for slash commands and agent mentions.
 *
 * The trigger primitive owns cursor-aware detection, filtering, keyboard
 * navigation, ARIA state, and replacing the active token. This component only
 * supplies the catalog and visual treatment, so another trigger can reuse the
 * same interaction contract without another bespoke composer.
 */
export const ComposerPalette: FC<ComposerPaletteProps> = ({
  char,
  items,
  title,
  emptyMessage,
  emptyHint,
}) => {
  const triggerItems = useMemo<readonly Unstable_TriggerItem[]>(
    () =>
      items.map((item) => ({
        id: item.id,
        type: item.type,
        label: item.label,
        ...(item.description ? { description: item.description } : {}),
        metadata: {
          ...(item.searchText ? { searchText: item.searchText } : {}),
          ...(item.secondary ? { secondary: item.secondary } : {}),
          ...(item.status ? { status: item.status } : {}),
          ...(item.insertText ? { insertText: item.insertText } : {}),
          ...(item.commandPath ? { commandPath: item.commandPath } : {}),
          ...(item.category ? { category: item.category } : {}),
          ...(item.parentId ? { parentId: item.parentId } : {}),
          ...(item.depth !== undefined ? { depth: item.depth } : {}),
          ...(item.args ? { args: item.args } : {}),
          icon: item.icon ?? item.type,
        },
      })),
    [items],
  );

  const adapter = useMemo<Unstable_TriggerAdapter>(
    () => ({
      categories: () => [],
      categoryItems: () => [],
      search: (query) => {
        if (char === "/") return searchCommandItems(triggerItems, query);
        return searchTriggerItems(triggerItems, query);
      },
    }),
    [char, triggerItems],
  );

  const formatter = useMemo<Unstable_DirectiveFormatter>(
    () => ({
      // Keep the inserted value in Kollab's native input syntax. The selected
      // token remains editable, and the existing send path handles `/...` and
      // `@...` exactly as if the user had typed it themselves.
      serialize: (item) =>
        `${char}${readMetadata(item, "insertText") ?? item.id}`,
      parse: (text) => [{ kind: "text", text }],
    }),
    [char],
  );

  return (
    <ComposerPrimitive.Unstable_TriggerPopover
      char={char}
      adapter={adapter}
      aria-label={title}
      className="bg-popover/98 border-border/80 absolute inset-x-0 bottom-full z-30 mb-2 overflow-hidden rounded-2xl border p-1.5 shadow-[0_20px_50px_-24px_rgba(0,0,0,0.8)] backdrop-blur-md"
    >
      <ComposerPrimitive.Unstable_TriggerPopover.Directive
        formatter={formatter}
      />
      <PaletteContent
        char={char}
        title={title}
        emptyMessage={emptyMessage}
        emptyHint={emptyHint}
      />
    </ComposerPrimitive.Unstable_TriggerPopover>
  );
};

const PaletteContent: FC<{
  char: "/" | "@";
  title: string;
  emptyMessage: string;
  emptyHint: string;
}> = ({ char, title, emptyMessage, emptyHint }) => {
  const scope = unstable_useTriggerPopoverScopeContext();

  useEffect(() => {
    if (!scope.open || !scope.highlightedItemId) return;

    const highlightedItem = document.getElementById(scope.highlightedItemId);
    const list = highlightedItem?.closest<HTMLElement>("[data-palette-list]");
    if (!highlightedItem || !list) return;

    // The assistant-ui primitive owns the highlight index but deliberately
    // leaves scrolling to the consumer. Adjust only this list's scrollTop so
    // arrow navigation never moves the page or the composer itself.
    const itemRect = highlightedItem.getBoundingClientRect();
    const listRect = list.getBoundingClientRect();
    if (itemRect.top < listRect.top) {
      list.scrollTop -= listRect.top - itemRect.top;
    } else if (itemRect.bottom > listRect.bottom) {
      list.scrollTop += itemRect.bottom - listRect.bottom;
    }
  }, [scope.highlightedItemId, scope.open]);

  // TriggerPopover intentionally renders its children in place while closed
  // so the Directive can register. Keep the visual catalog out of that closed
  // branch; otherwise the palette header leaks above the composer.
  if (!scope.open) return null;

  return (
    <>
      <div className="text-muted-foreground flex items-center justify-between px-3 py-2 text-[11px] font-medium tracking-[0.12em] uppercase">
        <span>{title}</span>
        <span className="border-border/70 bg-muted/60 text-muted-foreground rounded-md border px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal">
          {char} to search
        </span>
      </div>
      <ComposerPrimitive.Unstable_TriggerPopoverItems
        data-palette-list="true"
        className="max-h-[min(22rem,45vh)] overflow-y-auto overscroll-contain pr-0.5"
      >
        {(visibleItems) =>
          visibleItems.length === 0 ? (
            <div className="text-muted-foreground flex items-center gap-3 px-3 py-5 text-sm">
              <SearchIcon className="size-4 shrink-0 opacity-60" />
              <div className="min-w-0">
                <p className="text-foreground/80 font-medium">{emptyMessage}</p>
                <p className="mt-0.5 text-xs">{emptyHint}</p>
              </div>
            </div>
          ) : (
            (() => {
              let lastCategory: string | undefined;
              return visibleItems.map((item) => {
                const depth = readMetadataNumber(item, "depth");
                const category = readMetadata(item, "category");
                const showCategory =
                  char === "/" &&
                  depth === 0 &&
                  Boolean(category) &&
                  category !== lastCategory;
                if (showCategory) lastCategory = category;

                return (
                  <Fragment key={item.id}>
                    {showCategory ? (
                      <div className="text-muted-foreground px-3 pb-1 pt-2 text-[10px] font-semibold tracking-[0.16em] uppercase first:pt-1">
                        {prettyCategoryName(category!)}
                      </div>
                    ) : null}
                    <ComposerPrimitive.Unstable_TriggerPopoverItem
                      item={item}
                      className={cn(
                        "data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground group flex w-full items-center gap-3 text-left transition-colors hover:bg-accent/70",
                        depth > 0
                          ? "ml-2 rounded-lg border-l border-border/60 py-1.5 pl-2 pr-2 sm:ml-7"
                          : "rounded-xl px-2.5 py-2",
                      )}
                    >
                      <PaletteIcon item={item} />
                      <span className="min-w-0 flex-1">
                        <span className="flex min-w-0 items-center gap-2">
                          <span
                            className={cn(
                              "truncate font-medium",
                              depth > 0 ? "text-[13px]" : "text-sm",
                            )}
                          >
                            {item.label}
                          </span>
                          {item.type === "command" ? (
                            <span className="text-muted-foreground/75 shrink-0 font-mono text-[10px]">
                              /
                              {depth > 0
                                ? (readMetadata(item, "commandPath") ??
                                  item.id)
                                : item.id}
                            </span>
                          ) : null}
                          {readMetadata(item, "args") ? (
                            <span className="text-muted-foreground/70 min-w-0 truncate font-mono text-[10px]">
                              {readMetadata(item, "args")}
                            </span>
                          ) : null}
                        </span>
                        {item.description ? (
                          <span className="text-muted-foreground group-data-[highlighted]:text-accent-foreground/70 mt-0.5 block truncate text-xs">
                            {item.description}
                          </span>
                        ) : null}
                      </span>
                      {readMetadata(item, "secondary") ? (
                        <span className="text-muted-foreground group-data-[highlighted]:text-accent-foreground/70 hidden max-w-36 truncate text-[11px] sm:block">
                          {readMetadata(item, "secondary")}
                        </span>
                      ) : null}
                      {readMetadata(item, "status") ? (
                        <span className="flex shrink-0 items-center gap-1.5 text-[10px] font-medium">
                          <span className="size-1.5 rounded-full bg-emerald-500" />
                          <span className="text-muted-foreground group-data-[highlighted]:text-accent-foreground/70">
                            {readMetadata(item, "status")}
                          </span>
                        </span>
                      ) : null}
                      <ChevronRightIcon className="text-muted-foreground/60 group-data-[highlighted]:text-accent-foreground/70 size-4 shrink-0" />
                    </ComposerPrimitive.Unstable_TriggerPopoverItem>
                  </Fragment>
                );
              });
            })()
          )
        }
      </ComposerPrimitive.Unstable_TriggerPopoverItems>
      <div className="text-muted-foreground/70 flex items-center gap-3 border-t border-border/50 px-3 py-1.5 text-[10px]">
        <span>
          <kbd className="mr-0.5 rounded border border-border/70 px-1 font-mono">
            ↑↓
          </kbd>{" "}
          navigate
        </span>
        <span>
          <kbd className="mr-0.5 rounded border border-border/70 px-1 font-mono">
            ↵
          </kbd>{" "}
          select
        </span>
        <span>
          <kbd className="mr-0.5 rounded border border-border/70 px-1 font-mono">
            esc
          </kbd>{" "}
          close
        </span>
      </div>
    </>
  );
};

const PaletteIcon: FC<{ item: Unstable_TriggerItem }> = ({ item }) => {
  const depth = readMetadataNumber(item, "depth");
  if (depth > 0) {
    return (
      <span className="text-muted-foreground/60 flex size-6 shrink-0 items-center justify-center">
        <CornerDownRightIcon className="size-3.5" />
      </span>
    );
  }

  const icon = readMetadata(item, "icon");
  const Icon =
    icon === "broadcast"
      ? MegaphoneIcon
      : item.type === "agent"
        ? item.id === "broadcast"
          ? MegaphoneIcon
          : BotIcon
        : icon === "command"
          ? CommandIcon
          : CommandIcon;

  return (
    <span
      className={cn(
        "bg-muted/70 text-muted-foreground group-data-[highlighted]:bg-background/60 group-data-[highlighted]:text-foreground flex size-8 shrink-0 items-center justify-center rounded-lg",
        item.type === "agent" && "text-sky-500",
        item.id === "broadcast" && "text-amber-500",
      )}
    >
      <Icon className="size-4" />
    </span>
  );
};

function readMetadata(
  item: Unstable_TriggerItem,
  key: string,
): string | undefined {
  const value = item.metadata?.[key];
  return typeof value === "string" ? value : undefined;
}

function readMetadataNumber(
  item: Unstable_TriggerItem,
  key: string,
): number {
  const value = item.metadata?.[key];
  return typeof value === "number" ? value : 0;
}

function prettyCategoryName(category: string): string {
  return category
    .replace(/[-_]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

function searchTriggerItems(
  items: readonly Unstable_TriggerItem[],
  query: string,
): readonly Unstable_TriggerItem[] {
  const lower = query.trim().toLowerCase();
  if (!lower) return items;
  return items.filter((item) => matchesTriggerItem(item, lower));
}

/**
 * Mirror the terminal command menu's useful hierarchy in a single search
 * result: a matching parent stays visible, and its named subcommands appear
 * immediately below it. A direct subcommand match also brings its parent
 * back so the inserted command is unambiguous.
 */
function searchCommandItems(
  items: readonly Unstable_TriggerItem[],
  query: string,
): readonly Unstable_TriggerItem[] {
  const normalized = query.trim().toLowerCase();
  const parents = items.filter((item) => readMetadataNumber(item, "depth") === 0);
  const children = items.filter((item) => readMetadataNumber(item, "depth") > 0);
  if (!normalized) return parents;

  const parts = normalized.split(/\s+/).filter(Boolean);
  const parentQuery = parts[0] ?? normalized;
  const childQuery = parts.slice(1).join(" ");
  const matchingParents = parents.filter((item) =>
    matchesTriggerItem(item, childQuery ? parentQuery : normalized),
  );
  const parentIds = new Set(matchingParents.map((item) => item.id));
  const matchingChildren = children.filter((item) => {
    const parentId = readMetadata(item, "parentId");
    if (!parentId) return false;
    if (childQuery) {
      return (
        parentIds.has(parentId) && matchesTriggerItem(item, childQuery)
      );
    }
    return matchesTriggerItem(item, normalized) || parentIds.has(parentId);
  });
  for (const child of matchingChildren) {
    const parentId = readMetadata(child, "parentId");
    if (parentId) parentIds.add(parentId);
  }

  const matchingChildIds = new Set(matchingChildren.map((item) => item.id));
  return items.filter((item) => {
    if (readMetadataNumber(item, "depth") > 0) {
      return matchingChildIds.has(item.id);
    }
    return parentIds.has(item.id);
  });
}

function matchesTriggerItem(
  item: Unstable_TriggerItem,
  lower: string,
): boolean {
  const metadataSearch = item.metadata?.searchText;
  const searchText =
    typeof metadataSearch === "string" ? metadataSearch : "";
  return [item.id, item.label, item.description, searchText]
    .filter(Boolean)
    .some((value) => value!.toLowerCase().includes(lower));
}
