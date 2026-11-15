import { Box, FileCode, GitBranch, LayoutDashboard, ListTodo, Plug, Puzzle, Terminal, type LucideIcon } from "lucide-react";
import type { Session } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SessionView } from "@/components/shell/SessionPanel";
import { PluginFailure, PluginRenderBoundary, usePluginSnapshot } from "./components";
import type { PluginHost } from "./host";
import type { PluginContext, PluginMenu } from "./sdk";

const ICONS: Record<string, LucideIcon> = { Box, FileCode, GitBranch, LayoutDashboard, ListTodo, Plug, Puzzle, Terminal };
const ROW = "h-7 w-full justify-start gap-2 px-2 text-xs";
export function pluginViewKey(pluginId: string, viewId: string): SessionView {
  return `plugin:${encodeURIComponent(pluginId)}:${encodeURIComponent(viewId)}`;
}
export function parsePluginView(view: SessionView): { pluginId: string; viewId: string } | null {
  if (!view.startsWith("plugin:")) return null;
  const parts = view.slice(7).split(":");
  if (parts.length !== 2) return null;
  try { return { pluginId: decodeURIComponent(parts[0]), viewId: decodeURIComponent(parts[1]) }; }
  catch { return null; }
}
function MenuCount({ context, count }: { context: PluginContext; count: NonNullable<PluginMenu["count"]> }) {
  const { data } = context.useApi<{ text?: string; hint?: string }>(count.path, { refreshOn: count.refreshOn });
  if (!data?.text) return null;
  return <Badge variant="secondary" className="ml-auto" title={data.hint}>{data.text}</Badge>;
}

/** Appends plugin entries to the existing session controls, not a separate sidebar. */
export function PluginSessionControls({ host, session, view, onViewChange, onNavigate }: {
  host: PluginHost; session: Session; view: SessionView; onViewChange: (view: SessionView) => void; onNavigate: () => void;
}) {
  const snapshot = usePluginSnapshot(host);
  const plugins = [...snapshot.registry.plugins].sort((a, b) => {
    const name = (id: string) => snapshot.entries.find(entry => entry.id === id)?.name ?? id;
    return name(a.id).localeCompare(name(b.id)) || a.id.localeCompare(b.id);
  });
  return <>
    {plugins.flatMap(plugin => plugin.views.map(item => {
      const key = pluginViewKey(plugin.id, item.id);
      const Icon = ICONS[item.icon ?? ""] ?? Puzzle;
      const disabled = item.needs_folder && !session.workspace;
      return <Button key={`view:${key}`} type="button" variant="ghost" size="sm" disabled={disabled}
        title={disabled ? "Open a chat with a folder to use this view" : undefined} onClick={() => onViewChange(key)}
        className={cn(ROW, view === key && "bg-accent text-accent-foreground font-medium")}>
        <Icon className="size-3.5" /><span className="min-w-0 truncate">{item.label}</span>
      </Button>;
    }))}
    {plugins.flatMap(plugin => plugin.menus.map(menu => {
      const context = host.getContext(plugin.id);
      const linkedView = plugin.views.find(item => item.id === menu.id);
      const page = plugin.pages.find(item => item.id === menu.id);
      const modal = plugin.modals.find(item => item.id === menu.id);
      const label = menu.label ?? linkedView?.label ?? page?.title ?? modal?.title ?? menu.id;
      const Icon = ICONS[menu.icon ?? linkedView?.icon ?? ""] ?? Puzzle;
      const disabled = !context || Boolean(linkedView?.needs_folder && !session.workspace) || (!menu.open && !linkedView && !page && !modal);
      return <Button key={`menu:${plugin.id}:${menu.id}`} type="button" variant="ghost" size="sm" disabled={disabled} className={ROW}
        title={disabled ? "This plugin action is unavailable" : undefined}
        onClick={() => {
          onNavigate();
          if (menu.open) context?.navigate(menu.open);
          else if (linkedView) onViewChange(pluginViewKey(plugin.id, linkedView.id));
          else if (page) context?.navigate({ page: page.id });
          else if (modal) context?.navigate({ modal: modal.id });
        }}>
        <Icon className="size-3.5" /><span className="min-w-0 truncate">{label}</span>
        {context && menu.count ? <MenuCount context={context} count={menu.count} /> : null}
      </Button>;
    }))}
  </>;
}

export function PluginSessionView({ host, view, session, onBack, onTurnOff }: {
  host: PluginHost; view: SessionView; session: Session; onBack: () => void; onTurnOff: (id: string) => void;
}) {
  const snapshot = usePluginSnapshot(host);
  const key = parsePluginView(view);
  if (!key) return null;
  const plugin = snapshot.registry.plugins.find(item => item.id === key.pluginId);
  const item = plugin?.views.find(candidate => candidate.id === key.viewId);
  const context = host.getContext(key.pluginId);
  const error = snapshot.errors[key.pluginId];
  if (error) return <PluginFailure name={key.pluginId} kind={snapshot.renderFailed.includes(key.pluginId) ? "render" : "load"}
    error={error} onRetry={() => { void host.retry(key.pluginId); }} onTurnOff={() => onTurnOff(key.pluginId)} />;
  if (!item || !context || (item.needs_folder && !session.workspace)) return <div data-plugin className="flex flex-col items-start gap-3 p-4">
    <p className="text-sm">This plugin view is unavailable for this chat.</p><Button type="button" variant="outline" onClick={onBack}>Back to Chat</Button>
  </div>;
  const Render = item.render;
  return <div data-plugin className="min-h-0 min-w-0 flex-1 overflow-auto">
    <PluginRenderBoundary name={key.pluginId} onError={error => host.reportRenderError(key.pluginId, error)}
      onRetry={() => { void host.retry(key.pluginId); }} onTurnOff={() => onTurnOff(key.pluginId)}>
      <Render k={context} session={session} />
    </PluginRenderBoundary>
  </div>;
}
