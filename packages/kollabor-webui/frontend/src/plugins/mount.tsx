/**
 * Where plugin UI mounts: inside `<div data-plugin="<id>">` (plugin CSS scopes to it, and on a touch screen
 * every control in it is at least 44 px tall, styles.css) and an error boundary, so a plugin that throws while
 * drawing shows Failed to Render and the rest of the app keeps working.
 */
import { Component, useState, type ComponentType, type ErrorInfo, type ReactNode } from "react";
import {
  Bell,
  Bot,
  Box,
  ChartColumn,
  GitBranch,
  GitPullRequest,
  Hand,
  LayoutDashboard,
  ListChecks,
  Plug,
  Puzzle,
  Rocket,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import type { PluginRecord } from "./registry";
import type { PluginSession, PluginTab } from "./sdk";

// ponytail: a short list of lucide names a manifest's `icon` can use; importing lucide's whole set by name
// would ship every icon. Add names here as plugins need them.
const ICONS: Readonly<Record<string, LucideIcon>> = {
  bell: Bell,
  bot: Bot,
  box: Box,
  "chart-column": ChartColumn,
  "git-branch": GitBranch,
  "git-pull-request": GitPullRequest,
  hand: Hand,
  "layout-dashboard": LayoutDashboard,
  "list-checks": ListChecks,
  plug: Plug,
  puzzle: Puzzle,
  rocket: Rocket,
};

/** The icon for a manifest icon name (a puzzle piece for an unknown one), or a component a plugin passed. */
export function pluginIcon(icon: unknown): ComponentType<{ className?: string }> {
  if (typeof icon === "function") return icon as ComponentType<{ className?: string }>;
  return (typeof icon === "string" && ICONS[icon]) || Puzzle;
}

/** Kollab's own panel for a plugin that failed: Retry, and its error text a click away. */
export function PluginFailure({
  title,
  error,
  onRetry,
  initiallyOpen = false,
  inset = true,
}: {
  title?: string;
  error: string;
  onRetry: () => void;
  initiallyOpen?: boolean;
  /** Its own padding; off inside a dialog, which pads its body already. */
  inset?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <div role="alert" className={cn("flex min-w-0 flex-col gap-3 text-sm", inset && "px-4 py-4 sm:px-6")}>
      {title ? <p className="font-medium">{title}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" className="pointer-coarse:min-h-11" onClick={onRetry}>
          Retry
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="pointer-coarse:min-h-11"
          aria-expanded={open}
          onClick={() => setOpen((shown) => !shown)}
        >
          View Error
        </Button>
      </div>
      {open ? (
        <pre className="bg-muted/40 text-muted-foreground max-h-60 overflow-auto rounded-md p-3 text-xs break-words whitespace-pre-wrap">
          {error || "No error text was given."}
        </pre>
      ) : null}
    </div>
  );
}

/** Catches an error a plugin throws while drawing; Retry draws it again. */
export class PluginBoundary extends Component<{ name: string; children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: unknown): { error: Error } {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(`${this.props.name} failed to render`, error, info.componentStack);
  }

  retry = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return <PluginFailure title={`${this.props.name} Failed to Render`} error={this.state.error.message} onRetry={this.retry} />;
  }
}

/** Every plugin view, page tab and modal tab mounts through this. */
export function PluginMount({ record, children }: { record: Pick<PluginRecord, "id" | "name">; children: ReactNode }) {
  return (
    <div data-plugin={record.id} className="flex min-h-0 min-w-0 flex-1 flex-col">
      <PluginBoundary name={record.name}>{children}</PluginBoundary>
    </div>
  );
}

/** A plugin page's or modal's tab bar (it scrolls sideways on a phone) and the open tab. */
export function PluginTabs({
  record,
  tabs,
  session,
}: {
  record: PluginRecord;
  tabs: readonly PluginTab[];
  session?: PluginSession;
}) {
  const [active, setActive] = useState(tabs[0]?.id ?? "");
  const shown = tabs.some((tab) => tab.id === active) ? active : (tabs[0]?.id ?? "");
  return (
    <Tabs value={shown} onValueChange={setActive} className="min-h-0 flex-1 gap-0">
      <TabsList className="w-full shrink-0 justify-start overflow-x-auto px-2 sm:px-4">
        {tabs.map((tab) => (
          <TabsTrigger key={tab.id} value={tab.id} className="flex-none pointer-coarse:min-h-11">
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((tab) => (
        <TabsContent key={tab.id} value={tab.id} className="flex min-h-0 flex-col overflow-y-auto px-4 py-3 sm:px-6">
          <PluginMount record={record}>{record.k ? <tab.render k={record.k} session={session} /> : null}</PluginMount>
        </TabsContent>
      ))}
    </Tabs>
  );
}
