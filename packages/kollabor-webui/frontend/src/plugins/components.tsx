import { Component, createContext, useContext, useSyncExternalStore, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { NoticeBar } from "@/components/shell/NoticeBar";
import type { PluginHost, PluginHostSnapshot } from "./host";

export const PluginHostContext = createContext<PluginHost | null>(null);

/** Components outside the provider (for example the PhoneLink loader) stay usable. */
export function usePluginHost() { return useContext(PluginHostContext); }
export function usePluginSnapshot(host: PluginHost): PluginHostSnapshot {
  return useSyncExternalStore(host.subscribe, host.getSnapshot, host.getSnapshot);
}

export function PluginFailure({ name, kind, error, onRetry, onTurnOff }: {
  name: string; kind: "load" | "render"; error: string; onRetry: () => void; onTurnOff: () => void;
}) {
  return (
    <div data-plugin className="flex min-w-0 flex-col items-start gap-3 p-4" role="alert">
      <p className="text-sm font-semibold">{name} · Failed to {kind === "load" ? "Load" : "Render"}</p>
      <p className="text-muted-foreground text-sm">The rest of Kollab is still available.</p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={onRetry}>Retry</Button>
        <Button type="button" size="sm" variant="outline" onClick={onTurnOff}>Turn Off</Button>
      </div>
      <details className="max-w-full text-sm"><summary className="cursor-pointer">View Error</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words">{error}</pre></details>
    </div>
  );
}

export function PluginRecoveryNotices({ disabled, skipped, onTurnOnAgain }: {
  disabled: boolean; skipped: string[]; onTurnOnAgain: (id: string) => void;
}) {
  return <>
    {disabled ? <NoticeBar>Plugins are off for this page. Remove <code>?plugins=off</code> from the URL and reload.</NoticeBar> : null}
    {skipped.map(id => <NoticeBar key={id} actions={<Button type="button" size="sm" variant="outline" onClick={() => onTurnOnAgain(id)}>Turn On Again</Button>}>
      {id} was skipped: it froze the page last time.
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

export function PluginNotices({ host }: { host: PluginHost }) {
  const snapshot = usePluginSnapshot(host);
  return <>
    <PluginRecoveryNotices disabled={snapshot.disabled} skipped={snapshot.skipped} onTurnOnAgain={id => { void host.turnOnAgain(id); }} />
    {snapshot.inventoryError ? <NoticeBar urgent>Plugins could not be loaded: {snapshot.inventoryError}</NoticeBar> : null}
    {snapshot.registry.plugins.flatMap(plugin => plugin.notices.map((notice, index) =>
      <NoticeBar key={`${plugin.id}:${index}`} actions={notice.button ? <Button type="button" size="sm" variant="outline"
        onClick={() => { if (notice.button) host.getContext(plugin.id)?.navigate(notice.button.open); }}>{notice.button.label}</Button> : null}>
        {notice.text}
      </NoticeBar>,
    ))}
  </>;
}
