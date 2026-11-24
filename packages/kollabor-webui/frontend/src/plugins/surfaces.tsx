/**
 * The contribution points kollab draws from the plugin registry: right-hand menu rows (SessionPanel), plugin
 * pages (App's main column), notices (above the chat, NoticeBar), plugin modals and the chat's plugin views.
 * With no plugin loaded each of them draws nothing, so the app looks exactly as it does without plugins.
 */
import { useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from "react";
import { Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { NoticeAction, NoticeBar } from "@/components/shell/NoticeBar";
import { PanelDialogSurface } from "@/components/panels/PanelDialogSurface";
import { cn } from "@/lib/utils";
import { openTarget, retryPlugin } from "./loader";
import { PluginFailure, PluginMount, PluginTabs, pluginIcon } from "./mount";
import {
  parsePluginViewKey,
  pluginRegistry,
  pluginViewKey,
  usePlugins,
  type PluginNotice,
  type PluginRecord,
  type PluginsSnapshot,
  type PluginViewKey,
} from "./registry";
import { isOpenTarget, useRemote, type DeclaredRow } from "./sdk";

/** Kollab knows this plugin is unwell: its code failed to load here, or a part of it failed in kollab. */
const unwell = (record: PluginRecord) => record.status === "failed" || record.entry.state === "error";

const validRow = (row: unknown): row is DeclaredRow =>
  Boolean(row) &&
  typeof (row as DeclaredRow).id === "string" &&
  typeof (row as DeclaredRow).label === "string" &&
  (row as DeclaredRow).label.trim() !== "" &&
  isOpenTarget((row as DeclaredRow).open);

/**
 * A menu row's count: what kollab knows first (Failed to Load, Stopped after the crash limit, Stopping),
 * otherwise the plugin's own `{text, hint}` from its `count.path` route.
 */
export function rowCount(record: PluginRecord, fetched: unknown): { text: string; hint: string } {
  if (record.status === "failed") return { text: "Failed to Load", hint: record.error ?? "" };
  if (record.entry.state === "error") return { text: "Failed to Load", hint: record.entry.error ?? "" };
  if (record.entry.services.some((service) => service.state === "crash-stopped")) return { text: "Stopped", hint: "" };
  if (record.entry.state === "stopping") return { text: "Stopping", hint: "" };
  if (record.entry.state === "stop-failed") {
    return { text: "Stop Failed", hint: record.entry.services.find((service) => service.state === "stop-failed")?.error ?? "" };
  }
  const badge = (fetched ?? {}) as { text?: unknown; hint?: unknown };
  return {
    text: typeof badge.text === "string" ? badge.text : "",
    hint: typeof badge.hint === "string" ? badge.hint : "",
  };
}

function PluginMenuRow({
  record,
  row,
  className,
  onClick,
}: {
  record: PluginRecord;
  row: DeclaredRow;
  className: string;
  onClick: () => void;
}) {
  const count = record.contributions.menus[row.id]?.count;
  const fetched = useRemote(record.status === "ready" ? record.k : null, count?.path, {
    refreshOn: count?.refreshOn,
    everyMs: count?.everyMs,
  });
  const { text, hint } = rowCount(record, fetched.data);
  const Icon = pluginIcon(row.icon);
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn(className, "pointer-coarse:min-h-11")}
      title={hint || undefined}
      onClick={onClick}
    >
      <Icon className="size-3.5" />
      <span className="min-w-0 truncate">{row.label}</span>
      {text ? <span className="text-muted-foreground ml-auto shrink-0 tabular-nums">{text}</span> : null}
    </Button>
  );
}

/**
 * Every enabled plugin's declared rows, sorted by plugin name, for the right-hand menu (after Who Is Online,
 * before Settings; the phone sheet shows the same list). `onOpen` is the menu's own opener, which steps the
 * phone sheet aside first.
 */
export function PluginMenuRows({ rowClassName, onOpen }: { rowClassName: string; onOpen: (open: () => void) => void }) {
  const { plugins } = usePlugins();
  const [failure, setFailure] = useState<string | null>(null);
  const failed = plugins.find((record) => record.id === failure);
  const rows = plugins.flatMap((record) =>
    (record.entry.contributes.menu ?? []).filter(validRow).map((row) => ({ record, row })),
  );
  if (!rows.length && !failed) return null;
  return (
    <>
      {rows.map(({ record, row }) => (
        <PluginMenuRow
          key={`${record.id}:${row.id}`}
          record={record}
          row={row}
          className={rowClassName}
          onClick={() => onOpen(() => (unwell(record) ? setFailure(record.id) : openTarget(record.id, row.open)))}
        />
      ))}
      <Dialog open={Boolean(failed)} onOpenChange={(open) => !open && setFailure(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{failed ? `${failed.name} Failed to Load` : "Failed to Load"}</DialogTitle>
            <DialogDescription>Its code did not load. The rest of kollab keeps working.</DialogDescription>
          </DialogHeader>
          {failed ? (
            <PluginFailure
              inset={false}
              error={failed.error ?? failed.entry.error ?? ""}
              onRetry={() => {
                retryPlugin(failed.id);
                setFailure(null);
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

function PageMessage({ children, onExit }: { children: ReactNode; onExit: () => void }) {
  return (
    <div role="status" className="flex flex-col items-start gap-3 px-4 py-6 sm:px-6">
      <p className="text-muted-foreground">{children}</p>
      <Button type="button" variant="outline" size="sm" className="pointer-coarse:min-h-11" onClick={onExit}>
        Back to Chat
      </Button>
    </div>
  );
}

/** A plugin's page: the whole main area with its own tab bar. */
export function PluginPage({ pluginId, pageId, onExit }: { pluginId: string; pageId: string; onExit: () => void }) {
  const snapshot = usePlugins();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const record = snapshot.plugins.find((item) => item.id === pluginId);
  const page = record?.contributions.pages.find((item) => item.id === pageId);
  const name = record?.name ?? snapshot.names[pluginId] ?? "This plugin";
  const declared = (record?.entry.contributes.pages ?? []).find(
    (item) => typeof item === "object" && item !== null && item.id === pageId,
  );
  const title =
    page?.title ?? (typeof declared === "object" && typeof declared?.title === "string" ? declared.title : name);
  useEffect(() => {
    titleRef.current?.focus({ preventScroll: true });
  }, [pluginId, pageId]);

  let body: ReactNode;
  if (snapshot.off) body = <PageMessage onExit={onExit}>Plugins are off for this page.</PageMessage>;
  else if (!snapshot.listed || record?.status === "loading")
    body = (
      <p role="status" className="text-muted-foreground flex items-center gap-2 px-4 py-6 sm:px-6">
        <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading…
      </p>
    );
  else if (!record) body = <PageMessage onExit={onExit}>{snapshot.listError ?? `${name} is off`}</PageMessage>;
  else if (record.status === "failed")
    body = <PluginFailure title={`${name} Failed to Load`} error={record.error ?? ""} onRetry={() => retryPlugin(record.id)} />;
  else if (!page) body = <PageMessage onExit={onExit}>{`${name} has no page to show here.`}</PageMessage>;
  else body = <PluginTabs record={record} tabs={page.tabs} />;

  return (
    <main aria-label={title} className="bg-background flex h-full min-h-0 min-w-0 flex-col text-sm">
      <header className="flex shrink-0 items-center gap-3 px-4 py-3 sm:px-6">
        <SidebarTrigger className="-ml-1 lg:hidden" />
        <h1 ref={titleRef} tabIndex={-1} className="min-w-0 flex-1 truncate text-lg font-semibold outline-none">
          {title}
        </h1>
        <TooltipIconButton tooltip={`Close ${title}`} className="size-8 pointer-coarse:size-11" onClick={onExit}>
          <X className="size-4" aria-hidden="true" />
        </TooltipIconButton>
      </header>
      {body}
    </main>
  );
}

function dismissNotice(pluginId: string, key: number) {
  pluginRegistry.contribute(pluginId, (current) => ({
    ...current,
    notices: current.notices.filter((item) => item.key !== key),
  }));
}

function pressNotice(record: PluginRecord, notice: PluginNotice) {
  const button = notice.button;
  if (!button) return;
  dismissNotice(record.id, notice.key);
  if (button.open) return openTarget(record.id, button.open);
  try {
    void Promise.resolve(button.run?.()).catch((error: unknown) => console.error(`${record.name} notice action failed`, error));
  } catch (error) {
    console.error(`${record.name} notice action failed`, error);
  }
}

/** Plugin notices: one line and at most one button each, above the chat like kollab's own. */
export function PluginNotices() {
  const { plugins } = usePlugins();
  const notices = plugins.flatMap((record) => record.contributions.notices.map((notice) => ({ record, notice })));
  if (!notices.length) return null;
  return (
    <>
      {notices.map(({ record, notice }) => (
        <NoticeBar
          key={`${record.id}:${notice.key}`}
          actions={
            notice.button ? (
              <NoticeAction onClick={() => pressNotice(record, notice)}>{notice.button.label}</NoticeAction>
            ) : undefined
          }
          onDismiss={() => dismissNotice(record.id, notice.key)}
        >
          {notice.text}
        </NoticeBar>
      ))}
    </>
  );
}

/** The open plugin modal: the plugin's tabs in the Settings dialog shape. */
export function PluginModalHost() {
  const { modal, plugins } = usePlugins();
  const record = modal ? plugins.find((item) => item.id === modal.plugin) : undefined;
  const spec = record?.contributions.modals.find((item) => item.id === modal?.modal);
  if (!record || !spec) return null;
  return (
    <Dialog open onOpenChange={(open) => !open && pluginRegistry.closeModal()}>
      <PanelDialogSurface>
        <DialogHeader className="shrink-0 px-4 pt-4 pb-2 sm:px-6">
          <DialogTitle>{spec.title}</DialogTitle>
          <DialogDescription className="sr-only">{record.name}</DialogDescription>
        </DialogHeader>
        <PluginTabs record={record} tabs={spec.tabs} />
      </PanelDialogSurface>
    </Dialog>
  );
}

export type PluginViewTab = readonly [PluginViewKey, string, ComponentType<{ className?: string }>];

/** The plugin tabs for the chat's view list; one that needs a folder hides for a chat with none. */
export function pluginViews(snapshot: PluginsSnapshot, hasFolder: boolean): PluginViewTab[] {
  return snapshot.plugins.flatMap((record) =>
    record.status !== "ready"
      ? []
      : record.contributions.views
          .filter((view) => hasFolder || !view.needs_folder)
          .map((view) => [pluginViewKey(record.id, view.id), view.label, pluginIcon(view.icon)] as const),
  );
}

/** Whether a chat can show this plugin view now; a chat on a removed or unavailable one shows Chat. */
export function pluginViewAvailable(snapshot: PluginsSnapshot, key: string, hasFolder: boolean): boolean {
  const parsed = parsePluginViewKey(key);
  const record = parsed ? snapshot.plugins.find((item) => item.id === parsed.pluginId) : undefined;
  const view = record?.status === "ready" ? record.contributions.views.find((item) => item.id === parsed?.viewId) : undefined;
  return Boolean(view && (hasFolder || !view.needs_folder));
}

/**
 * Whether a chat's plugin tab is gone for good: its plugin is off, failed, or no longer adds that view. Such a
 * chat goes to Chat. A plugin still loading (an update) or a tab that needs a folder this chat lacks is not gone.
 */
export function pluginViewGone(snapshot: PluginsSnapshot, key: string): boolean {
  const parsed = parsePluginViewKey(key);
  if (!parsed) return false;
  const record = snapshot.plugins.find((item) => item.id === parsed.pluginId);
  if (record?.status === "loading") return false;
  return !record || record.status === "failed" || !record.contributions.views.some((item) => item.id === parsed.viewId);
}

/** A plugin view in the chat's main area; it receives the open chat's session and folder. */
export function PluginViewHost({
  viewKey,
  session,
}: {
  viewKey: string;
  session: { session_id: string; workspace?: string | null; name?: string; identity?: string };
}) {
  const snapshot = usePlugins();
  const { session_id, workspace, name, identity } = session;
  const pluginSession = useMemo(
    () => ({ session_id, workspace: workspace ?? "", name, identity }),
    [session_id, workspace, name, identity],
  );
  const parsed = parsePluginViewKey(viewKey);
  const record = parsed ? snapshot.plugins.find((item) => item.id === parsed.pluginId) : undefined;
  const view = record?.contributions.views.find((item) => item.id === parsed?.viewId);
  if (!record?.k || !view) return null;
  const View = view.render;
  return (
    <PluginMount record={record} renderer={`view:${view.id}`} resetKey={session_id}>
      <View k={record.k} session={pluginSession} />
    </PluginMount>
  );
}
