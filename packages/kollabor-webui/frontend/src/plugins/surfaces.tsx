import { useEffect, useRef } from "react";
import type { Session } from "@/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PluginFailure, PluginRenderBoundary, usePluginSnapshot } from "./components";
import type { PluginHost } from "./host";
import type { PluginContext, PluginTab } from "./sdk";

function PluginTabs({ host, pluginId, tabs, context, session, onTurnOff }: {
  host: PluginHost; pluginId: string; tabs: PluginTab[]; context: PluginContext;
  session?: Session; onTurnOff: (id: string) => void;
}) {
  if (!tabs.length) return <p className="text-muted-foreground p-4 text-sm">This plugin has no tabs.</p>;
  return <Tabs defaultValue={tabs[0].id} className="min-h-0 flex-1 gap-0">
    <div className="shrink-0 overflow-x-auto px-4"><TabsList className="min-w-max">
      {tabs.map(tab => <TabsTrigger key={tab.id} value={tab.id}>{tab.label}</TabsTrigger>)}
    </TabsList></div>
    {tabs.map(tab => {
      const Render = tab.render;
      return <TabsContent key={tab.id} value={tab.id} className="min-h-0 overflow-auto px-4 py-4">
        <PluginRenderBoundary name={pluginId} onError={error => host.reportRenderError(pluginId, error)}
          onRetry={() => { void host.retry(pluginId); }} onTurnOff={() => onTurnOff(pluginId)}>
          <Render k={context} session={session} />
        </PluginRenderBoundary>
      </TabsContent>;
    })}
  </Tabs>;
}

function PluginUnavailable({ host, pluginId, onClose, onTurnOff }: {
  host: PluginHost; pluginId: string; onClose: () => void; onTurnOff: (id: string) => void;
}) {
  const snapshot = usePluginSnapshot(host);
  const entry = snapshot.entries.find(item => item.id === pluginId);
  const error = snapshot.errors[pluginId];
  if (error) return <><PluginFailure name={entry?.name ?? pluginId} kind={snapshot.renderFailed.includes(pluginId) ? "render" : "load"}
    error={error} onRetry={() => { void host.retry(pluginId); }} onTurnOff={() => onTurnOff(pluginId)} />
    <div className="px-4 pb-4"><Button type="button" variant="outline" onClick={onClose}>Back to Chat</Button></div></>;
  return <div data-plugin className="flex flex-col items-start gap-3 p-4">
    <p className="text-sm font-semibold">{entry?.name ?? pluginId} {entry && !entry.enabled ? "is off" : "is unavailable"}</p>
    <Button type="button" variant="outline" onClick={onClose}>Back to Chat</Button>
  </div>;
}

export function PluginPageSurface({ host, pluginId, pageId, session, onClose, onTurnOff }: {
  host: PluginHost; pluginId: string; pageId: string; session?: Session; onClose: () => void; onTurnOff: (id: string) => void;
}) {
  const snapshot = usePluginSnapshot(host);
  const page = snapshot.registry.plugins.find(item => item.id === pluginId)?.pages.find(item => item.id === pageId);
  const context = host.getContext(pluginId);
  const titleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { titleRef.current?.focus({ preventScroll: true }); }, [pluginId, pageId]);
  if (!page || !context) return <main aria-label="Plugin page" className="flex h-full min-h-0 flex-col">
    <PluginUnavailable host={host} pluginId={pluginId} onClose={onClose} onTurnOff={onTurnOff} />
  </main>;
  return <main data-plugin aria-label={page.title} className="flex h-full min-h-0 min-w-0 flex-col bg-background text-sm">
    <header className="flex shrink-0 items-center gap-3 px-4 py-3 sm:px-6">
      <SidebarTrigger className="-ml-1 lg:hidden" />
      <h1 ref={titleRef} tabIndex={-1} className="min-w-0 flex-1 text-lg font-semibold outline-none">{page.title}</h1>
      <Button type="button" variant="outline" size="sm" onClick={onClose}>Back to Chat</Button>
    </header>
    <PluginTabs key={`${pluginId}:${pageId}`} host={host} pluginId={pluginId} tabs={page.tabs} context={context} session={session} onTurnOff={onTurnOff} />
  </main>;
}

export function PluginModalSurface({ host, pluginId, modalId, session, onClose, onTurnOff }: {
  host: PluginHost; pluginId: string; modalId: string; session?: Session; onClose: () => void; onTurnOff: (id: string) => void;
}) {
  const snapshot = usePluginSnapshot(host);
  const modal = snapshot.registry.plugins.find(item => item.id === pluginId)?.modals.find(item => item.id === modalId);
  const context = host.getContext(pluginId);
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent data-plugin className="flex max-h-[100dvh] min-h-0 flex-col overflow-hidden max-sm:h-[100dvh] max-sm:max-w-none max-sm:rounded-none">
      <DialogHeader><DialogTitle>{modal?.title ?? pluginId}</DialogTitle><DialogDescription className="sr-only">Plugin dialog</DialogDescription></DialogHeader>
      {modal && context ? <PluginTabs key={`${pluginId}:${modalId}`} host={host} pluginId={pluginId} tabs={modal.tabs} context={context} session={session} onTurnOff={onTurnOff} /> :
        <PluginUnavailable host={host} pluginId={pluginId} onClose={onClose} onTurnOff={onTurnOff} />}
    </DialogContent>
  </Dialog>;
}
