import { useEffect } from "react";
import { Box, FileCode, GitBranch, LayoutDashboard, ListTodo, Plug, Puzzle, Terminal, type LucideIcon } from "lucide-react";
import type { Session } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SessionView } from "@/components/shell/SessionPanel";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { PluginFailure, PluginRenderBoundary, usePluginSnapshot } from "./components";
import type { PluginHost } from "./host";
import { heartbeatAge, stoppingLabel, useHeartbeatClock } from "./heartbeat";
import type { PluginContext, PluginMenu } from "./sdk";

const ICONS: Record<string, LucideIcon> = { Box, FileCode, GitBranch, LayoutDashboard, ListTodo, Plug, Puzzle, Terminal };
const ROW = "h-7 w-full justify-start gap-2 px-2 text-xs any-pointer-coarse:min-h-[44px] any-pointer-coarse:min-w-[44px]";
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
  return <span className={compact ? "absolute -right-2 -top-1 text-[8px] tabular-nums" : "text-muted-foreground ml-auto tabular-nums"} title={data.hint}>{data.text}</span>;
}

function SessionControl({ label, Icon, collapsed, disabled, disabledReason, active, failed, stopping, stopped, noHeartbeat, count, onClick }: {
  label: string; Icon: LucideIcon; collapsed: boolean; disabled?: boolean; disabledReason?: string; active?: boolean; failed?: "load" | "render"; stopping?: string; stopped?: boolean; noHeartbeat?: boolean;
  count?: { context: PluginContext; value: NonNullable<PluginMenu["count"]> }; onClick: () => void;
}) {
  const status = failed ? `Failed to ${failed === "render" ? "Render" : "Load"}` : stopping ? stopping : stopped ? "Stopped" : noHeartbeat ? "No Heartbeat" : undefined;
  const tooltip = `${label}${status ? ` · ${status}` : disabled ? ` · ${disabledReason ?? "Unavailable"}` : ""}`;
  if (collapsed) return <TooltipIconButton tooltip={tooltip} side="left" aria-label={status ? `${label} ${status}` : label} aria-pressed={active}
    disabled={disabled} title={tooltip} className={cn("size-7 p-1.5 any-pointer-coarse:min-h-[44px] any-pointer-coarse:min-w-[44px]", active && "bg-accent text-accent-foreground")} onClick={onClick}>
    <span className="relative"><Icon className="size-4" />{status ? <span aria-label={status} className={cn("absolute -right-1 -top-1 size-1.5 rounded-full", noHeartbeat && !failed && !stopping && !stopped ? "bg-amber-500" : "bg-destructive")} /> : null}
      {!status && count ? <MenuCount context={count.context} count={count.value} compact /> : null}</span>
  </TooltipIconButton>;
  return <Button type="button" variant="ghost" size="sm" disabled={disabled} aria-pressed={active}
    title={status || disabled ? tooltip : undefined} onClick={onClick} className={cn(ROW, active && "bg-accent text-accent-foreground font-medium")}>
    <Icon className="size-3.5" /><span className="min-w-0 truncate">{label}</span>
    {status ? <span className={cn("ml-auto min-w-0 truncate", stopping ? "text-muted-foreground" : noHeartbeat && !failed && !stopped ? "text-amber-700 dark:text-amber-400" : "text-destructive")}>{status}</span> : count ? <MenuCount context={count.context} count={count.value} /> : null}
  </Button>;
}

/** Appends plugin entries to the existing session controls, not a separate sidebar. */
export function PluginSessionControls({ host, session, view, onViewChange, onNavigate, collapsed = false, section = "all" }: {
  host: PluginHost; session: Session; view: SessionView; onViewChange: (view: SessionView) => void; onNavigate: () => void; collapsed?: boolean; section?: "all" | "views" | "menus";
}) {
  const snapshot = usePluginSnapshot(host);
  const monitorHeartbeat = section !== "views" && snapshot.entries.some(entry => entry.services?.some(service =>
    typeof service === "object" && service !== null && "state" in service && service.state === "running" &&
    "heartbeat_s" in service && typeof service.heartbeat_s === "number" && service.heartbeat_s > 0));
  useHeartbeatClock(monitorHeartbeat);
  const now = host.engineTime();
  const plugins = [...snapshot.registry.plugins].sort((a, b) => {
    const name = (id: string) => snapshot.entries.find(entry => entry.id === id)?.name ?? id;
    return name(a.id).localeCompare(name(b.id)) || a.id.localeCompare(b.id);
  });
  return <>
    {section !== "menus" && <>{plugins.flatMap(plugin => plugin.views.filter(item => !item.needs_folder || session.workspace).map(item => {
      const key = pluginViewKey(plugin.id, item.id);
      const Icon = ICONS[item.icon ?? ""] ?? Puzzle;
      return <SessionControl key={`view:${key}`} label={item.label} Icon={Icon} collapsed={collapsed}
        active={view === key} onClick={() => onViewChange(key)} />;
    }))}
    {snapshot.entries.filter(entry => entry.enabled && !entry.shadowed && snapshot.errors[entry.id]).flatMap(entry => {
      const declared = Array.isArray(entry.contributes?.views) ? entry.contributes.views : [];
      return declared.flatMap((raw: unknown, index: number) => {
        const item = typeof raw === "string" ? { id: raw } : raw && typeof raw === "object" ? raw as { id?: unknown; label?: unknown } : null;
        if (!item || typeof item.id !== "string") return [];
        const label = typeof item.label === "string" && item.label ? item.label : `${entry.name}${index ? ` View ${index + 1}` : ""}`;
        const key = pluginViewKey(entry.id, item.id);
        return [<SessionControl key={`failed-view:${key}`} label={label} Icon={Puzzle} collapsed={collapsed} failed={snapshot.renderFailed.includes(entry.id) ? "render" : "load"}
          active={view === key} onClick={() => onViewChange(key)} />];
      });
    })}</>}
    {section !== "views" && [...snapshot.entries.filter(entry => entry.enabled && !entry.shadowed && snapshot.errors[entry.id]).map(entry => {
      const declared = Array.isArray(entry.contributes?.menu) ? entry.contributes.menu : [];
      return { id: entry.id, name: entry.name, rows: declared.flatMap((raw: unknown) => {
        if (!raw || typeof raw !== "object") return [];
        const menu = raw as PluginMenu;
        if (typeof menu.id !== "string") return [];
        return [<SessionControl key={`failed:${entry.id}:${menu.id}`} label={menu.label ?? entry.name} Icon={Puzzle} collapsed={collapsed} failed={snapshot.renderFailed.includes(entry.id) ? "render" : "load"}
          onClick={() => { onNavigate(); host.navigate(entry.id, menu.open ?? { page: menu.id }); }} />];
      }) };
    }), ...plugins.map(plugin => ({ id: plugin.id, name: snapshot.entries.find(entry => entry.id === plugin.id)?.name ?? plugin.id, rows: plugin.menus.map(menu => {
      const context = host.getContext(plugin.id);
      const linkedView = plugin.views.find(item => item.id === menu.id);
      const page = plugin.pages.find(item => item.id === menu.id);
      const modal = plugin.modals.find(item => item.id === menu.id);
      const label = menu.label ?? linkedView?.label ?? page?.title ?? modal?.title ?? menu.id;
      const Icon = ICONS[menu.icon ?? linkedView?.icon ?? ""] ?? Puzzle;
      const disabled = !context || Boolean(linkedView?.needs_folder && !session.workspace) || (!menu.open && !linkedView && !page && !modal);
      const entry = snapshot.entries.find(item => item.id === plugin.id);
      const stopped = entry?.services?.some(service =>
        typeof service === "object" && service !== null && "state" in service && service.state === "crash-stopped");
      const noHeartbeat = entry?.services?.some(service => heartbeatAge(service, now) !== null);
      return <SessionControl key={`menu:${plugin.id}:${menu.id}`} label={label} Icon={Icon} collapsed={collapsed} disabled={disabled} stopping={entry?.state === "stopping" ? stoppingLabel(entry.services ?? [], now) : undefined} stopped={stopped} noHeartbeat={noHeartbeat}
        disabledReason="This plugin action is unavailable" count={context && menu.count ? { context, value: menu.count } : undefined}
        onClick={() => {
          onNavigate();
          if (menu.open) context?.navigate(menu.open);
          else if (linkedView) onViewChange(pluginViewKey(plugin.id, linkedView.id));
          else if (page) context?.navigate({ page: page.id });
          else if (modal) context?.navigate({ modal: modal.id });
        }} />;
    }) }))].sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)).flatMap(group => group.rows)}
  </>;
}

export function PluginSessionView({ host, view, session, onBack, onTurnOff }: {
  host: PluginHost; view: SessionView; session: Session; onBack: () => void; onTurnOff: (id: string) => void;
}) {
  const snapshot = usePluginSnapshot(host);
  const key = parsePluginView(view);
  const entry = snapshot.entries.find(item => item.id === key?.pluginId);
  useEffect(() => {
    if (key && !snapshot.loading && !snapshot.inventoryError && (!entry || (!entry.enabled && entry.state !== "stopping"))) onBack();
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
