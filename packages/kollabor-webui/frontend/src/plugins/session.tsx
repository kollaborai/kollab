import { useEffect } from "react";
import { Box, FileCode, GitBranch, LayoutDashboard, ListTodo, Plug, Puzzle, Terminal, type LucideIcon } from "lucide-react";
import type { Session } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SessionView } from "@/components/shell/SessionPanel";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
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
function MenuCount({ context, count, compact = false }: { context: PluginContext; count: NonNullable<PluginMenu["count"]>; compact?: boolean }) {
  const { data } = context.useApi<{ text?: string; hint?: string }>(count.path, { refreshOn: count.refreshOn });
  if (!data?.text) return null;
  return <Badge variant="secondary" className={compact ? "absolute -right-2 -top-1 h-3 min-w-3 px-0.5 text-[8px]" : "ml-auto"} title={data.hint}>{data.text}</Badge>;
}

function SessionControl({ label, Icon, collapsed, disabled, disabledReason, active, failed, count, onClick }: {
  label: string; Icon: LucideIcon; collapsed: boolean; disabled?: boolean; disabledReason?: string; active?: boolean; failed?: boolean;
  count?: { context: PluginContext; value: NonNullable<PluginMenu["count"]> }; onClick: () => void;
}) {
  const tooltip = `${label}${failed ? " · Failed" : disabled ? ` · ${disabledReason ?? "Unavailable"}` : ""}`;
  if (collapsed) return <TooltipIconButton tooltip={tooltip} side="left" aria-label={label} aria-pressed={active}
    disabled={disabled} title={tooltip} className={cn("size-7 p-1.5", active && "bg-accent text-accent-foreground")} onClick={onClick}>
    <span className="relative"><Icon className="size-4" />{failed ? <span aria-label="Failed" className="bg-destructive absolute -right-1 -top-1 size-1.5 rounded-full" /> : null}
      {count ? <MenuCount context={count.context} count={count.value} compact /> : null}</span>
  </TooltipIconButton>;
  return <Button type="button" variant="ghost" size="sm" disabled={disabled} aria-pressed={active}
    title={disabled ? tooltip : undefined} onClick={onClick} className={cn(ROW, active && "bg-accent text-accent-foreground font-medium")}>
    <Icon className="size-3.5" /><span className="min-w-0 truncate">{label}</span>
    {failed ? <Badge variant="destructive" className="ml-auto">Failed</Badge> : count ? <MenuCount context={count.context} count={count.value} /> : null}
  </Button>;
}

/** Appends plugin entries to the existing session controls, not a separate sidebar. */
export function PluginSessionControls({ host, session, view, onViewChange, onNavigate, collapsed = false }: {
  host: PluginHost; session: Session; view: SessionView; onViewChange: (view: SessionView) => void; onNavigate: () => void; collapsed?: boolean;
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
      return <SessionControl key={`view:${key}`} label={item.label} Icon={Icon} collapsed={collapsed} disabled={disabled}
        disabledReason="Open a chat with a folder to use this view" active={view === key} onClick={() => onViewChange(key)} />;
    }))}
    {snapshot.entries.filter(entry => entry.enabled && !entry.shadowed && snapshot.errors[entry.id]).flatMap(entry => {
      const declared = Array.isArray(entry.contributes?.views) ? entry.contributes.views : [];
      return declared.flatMap((raw: unknown, index: number) => {
        const item = typeof raw === "string" ? { id: raw } : raw && typeof raw === "object" ? raw as { id?: unknown; label?: unknown } : null;
        if (!item || typeof item.id !== "string") return [];
        const label = typeof item.label === "string" && item.label ? item.label : `${entry.name}${index ? ` View ${index + 1}` : ""}`;
        const key = pluginViewKey(entry.id, item.id);
        return [<SessionControl key={`failed-view:${key}`} label={label} Icon={Puzzle} collapsed={collapsed} failed
          active={view === key} onClick={() => onViewChange(key)} />];
      });
    })}
    {snapshot.entries.filter(entry => entry.enabled && !entry.shadowed && snapshot.errors[entry.id]).flatMap(entry => {
      const declared = Array.isArray(entry.contributes?.menu) ? entry.contributes.menu : [];
      return declared.flatMap((raw: unknown) => {
        if (!raw || typeof raw !== "object") return [];
        const menu = raw as PluginMenu;
        if (typeof menu.id !== "string") return [];
        return [<SessionControl key={`failed:${entry.id}:${menu.id}`} label={menu.label ?? entry.name} Icon={Puzzle} collapsed={collapsed} failed
          onClick={() => { onNavigate(); host.navigate(entry.id, menu.open ?? { page: menu.id }); }} />];
      });
    })}
    {plugins.flatMap(plugin => plugin.menus.map(menu => {
      const context = host.getContext(plugin.id);
      const linkedView = plugin.views.find(item => item.id === menu.id);
      const page = plugin.pages.find(item => item.id === menu.id);
      const modal = plugin.modals.find(item => item.id === menu.id);
      const label = menu.label ?? linkedView?.label ?? page?.title ?? modal?.title ?? menu.id;
      const Icon = ICONS[menu.icon ?? linkedView?.icon ?? ""] ?? Puzzle;
      const disabled = !context || Boolean(linkedView?.needs_folder && !session.workspace) || (!menu.open && !linkedView && !page && !modal);
      return <SessionControl key={`menu:${plugin.id}:${menu.id}`} label={label} Icon={Icon} collapsed={collapsed} disabled={disabled}
        disabledReason="This plugin action is unavailable" count={context && menu.count ? { context, value: menu.count } : undefined}
        onClick={() => {
          onNavigate();
          if (menu.open) context?.navigate(menu.open);
          else if (linkedView) onViewChange(pluginViewKey(plugin.id, linkedView.id));
          else if (page) context?.navigate({ page: page.id });
          else if (modal) context?.navigate({ modal: modal.id });
        }} />;
    }))}
  </>;
}

export function PluginSessionView({ host, view, session, onBack, onTurnOff }: {
  host: PluginHost; view: SessionView; session: Session; onBack: () => void; onTurnOff: (id: string) => void;
}) {
  const snapshot = usePluginSnapshot(host);
  const key = parsePluginView(view);
  const entry = snapshot.entries.find(item => item.id === key?.pluginId);
  useEffect(() => {
    if (key && !snapshot.loading && !snapshot.inventoryError && (!entry || !entry.enabled)) onBack();
  }, [key?.pluginId, entry, snapshot.loading, snapshot.inventoryError, onBack]);
  if (!key) return null;
  const plugin = snapshot.registry.plugins.find(item => item.id === key.pluginId);
  const item = plugin?.views.find(candidate => candidate.id === key.viewId);
  const context = host.getContext(key.pluginId);
  const error = snapshot.errors[key.pluginId];
  if (error) return <PluginFailure name={entry?.name ?? "Plugin"} kind={snapshot.renderFailed.includes(key.pluginId) ? "render" : "load"}
    error={error} onRetry={() => { void host.retry(key.pluginId); }} onTurnOff={() => onTurnOff(key.pluginId)} />;
  if (!item || !context || (item.needs_folder && !session.workspace)) return <div data-plugin className="flex flex-col items-start gap-3 p-4">
    <p className="text-sm">This plugin view is unavailable for this chat.</p><Button type="button" variant="outline" onClick={onBack}>Back to Chat</Button>
  </div>;
  const Render = item.render;
  return <div data-plugin={key.pluginId} className="min-h-0 min-w-0 flex-1 overflow-auto">
    <PluginRenderBoundary name={entry?.name ?? "Plugin"} onError={error => host.reportRenderError(key.pluginId, error)}
      onRetry={() => { void host.retry(key.pluginId); }} onTurnOff={() => onTurnOff(key.pluginId)}>
      <Render k={context} session={session} />
    </PluginRenderBoundary>
  </div>;
}
