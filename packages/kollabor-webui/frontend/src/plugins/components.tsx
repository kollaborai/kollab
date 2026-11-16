import { Component, createContext, useContext, useState, useSyncExternalStore, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { NoticeAction, NoticeBar } from "@/components/shell/NoticeBar";
import type { PluginHost, PluginHostSnapshot } from "./host";
import type { PluginNotice } from "./sdk";
import type { Session } from "@/api";

export const PluginHostContext = createContext<PluginHost | null>(null);
export const PluginSessionContext = createContext<Session | undefined>(undefined);
export const PluginTurnOffContext = createContext<(id: string) => void>(() => {});

/** Components outside the provider (for example the PhoneLink loader) stay usable. */
export function usePluginHost() { return useContext(PluginHostContext); }
export function usePluginSnapshot(host: PluginHost): PluginHostSnapshot {
  return useSyncExternalStore(host.subscribe, host.getSnapshot, host.getSnapshot);
}
const EMPTY_SNAPSHOT: PluginHostSnapshot = { phoneLink: false, disabled: true, loading: false, inventoryError: null, entries: [],
  registry: { plugins: [], conflicts: [] }, errors: {}, skipped: [], renderFailed: [], failedRenderers: [] };
const emptySnapshot = () => EMPTY_SNAPSHOT;
const emptySubscribe = () => () => {};
export function useOptionalPluginSnapshot(host: PluginHost | null): PluginHostSnapshot {
  return useSyncExternalStore(host?.subscribe ?? emptySubscribe, host?.getSnapshot ?? emptySnapshot, host?.getSnapshot ?? emptySnapshot);
}

export function PluginErrorDisclosure({ error }: { error: string }) {
  const [showError, setShowError] = useState(false);
  return <div>
    <Button type="button" size="sm" variant="secondary" aria-expanded={showError} onClick={() => setShowError(value => !value)}>View Error</Button>
    {showError ? <pre className="max-w-full overflow-auto whitespace-pre-wrap break-words">{error}</pre> : null}
  </div>;
}

export function PluginFailure({ name, kind, error, onRetry, onTurnOff }: {
  name: string; kind: "load" | "render"; error: string; onRetry: () => void; onTurnOff: () => void;
}) {
  return (
    <div className="min-w-0 p-4" role="alert">
      <section className="bg-card flex w-full max-w-[460px] min-w-0 flex-col items-start gap-3 rounded-xl p-4 text-sm">
        <p className="font-semibold">{name} · Failed to {kind === "load" ? "Load" : "Render"}</p>
        <p>{name} {kind === "load" ? "could not load." : "could not draw this page."}</p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" onClick={onRetry}>Retry</Button>
          <PluginErrorDisclosure error={error} />
          <Button type="button" size="sm" variant="ghost" onClick={onTurnOff}>Turn Off</Button>
        </div>
      </section>
    </div>
  );
}

export function PluginRecoveryNotices({ disabled, skipped, names, onTurnOnAgain }: {
  disabled: boolean; skipped: string[]; names: Record<string, string>; onTurnOnAgain: (id: string) => void;
}) {
  return <>
    {disabled ? <NoticeBar>Plugins are off for this page. Remove <code>?plugins=off</code> from the URL and reload.</NoticeBar> : null}
    {skipped.map(id => <NoticeBar key={id} actions={<NoticeAction className="text-foreground any-pointer-coarse:min-h-[44px] any-pointer-coarse:min-w-[44px]" onClick={() => onTurnOnAgain(id)}>Turn On Again</NoticeAction>}>
      {names[id] ?? "A plugin"} was skipped: it froze the page last time.
    </NoticeBar>)}
  </>;
}

type BoundaryProps = {
  name: string; children: ReactNode; onError: (error: Error) => void;
  onRetry: () => void; onTurnOff: () => void;
};
type BoundaryState = { error: Error | null };

/** A plugin render failure must not bring down chat or another plugin. */
export class PluginRenderBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null };
  static getDerivedStateFromError(error: Error): BoundaryState { return { error }; }
  componentDidCatch(error: Error, _info: ErrorInfo) { this.props.onError(error); }
  render() {
    if (this.state.error) return <PluginFailure name={this.props.name} kind="render" error={this.state.error.message}
      onTurnOff={this.props.onTurnOff} onRetry={() => { this.setState({ error: null }); this.props.onRetry(); }} />;
    return this.props.children;
  }
}

const noticeKeys = new WeakMap<PluginNotice, number>();
let nextNoticeKey = 0;
function noticeKey(notice: PluginNotice): number {
  let key = noticeKeys.get(notice);
  if (key === undefined) { key = nextNoticeKey++; noticeKeys.set(notice, key); }
  return key;
}

function ContributedNotice({ host, pluginId, name, notice }: { host: PluginHost; pluginId: string; name: string; notice: PluginNotice }) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  return <NoticeBar onDismiss={() => setDismissed(true)} actions={notice.button ? <NoticeAction className="text-foreground any-pointer-coarse:min-h-[44px] any-pointer-coarse:min-w-[44px]"
    onClick={() => { if (notice.button) host.getContext(pluginId)?.navigate(notice.button.open); }}>{notice.button.label}</NoticeAction> : null}>
    <strong>{name}:</strong> <span>{notice.text}</span>
  </NoticeBar>;
}

export function PluginNotices({ host }: { host: PluginHost }) {
  const snapshot = usePluginSnapshot(host);
  return <>
    <PluginRecoveryNotices disabled={snapshot.disabled} skipped={snapshot.skipped} names={Object.fromEntries(snapshot.entries.map(entry => [entry.id, entry.name]))} onTurnOnAgain={id => { void host.turnOnAgain(id); }} />
    {snapshot.inventoryError ? <NoticeBar urgent>Plugins could not be loaded: {snapshot.inventoryError}</NoticeBar> : null}
    {snapshot.registry.conflicts.map(({ pluginId, command, owner }, index) => {
      const name = snapshot.entries.find(entry => entry.id === pluginId)?.name ?? pluginId;
      const ownerName = owner === "built-in" ? "a built-in command" : snapshot.entries.find(entry => entry.id === owner)?.name ?? owner;
      return <NoticeBar key={`command-conflict:${pluginId}:${command}:${owner}:${index}`}>
        {`${name} could not register /${command}: already used by ${ownerName}.`}
      </NoticeBar>;
    })}
    {snapshot.registry.plugins.flatMap(plugin => plugin.notices.map(notice =>
      <ContributedNotice key={`${plugin.id}:${noticeKey(notice)}`} host={host} pluginId={plugin.id}
        name={snapshot.entries.find(entry => entry.id === plugin.id)?.name ?? plugin.id} notice={notice} />,
    ))}
  </>;
}
