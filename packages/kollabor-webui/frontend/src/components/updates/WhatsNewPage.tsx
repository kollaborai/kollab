import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { CHANGELOG_URL, loadCanonicalWhatsNew, type WhatsNewResult } from "@/learn/changelog";

export function WhatsNewPage({ onExit }: { onExit: () => void }) {
  const [result, setResult] = useState<WhatsNewResult>({ status: "loading" });
  const titleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    titleRef.current?.focus({ preventScroll: true });
    let active = true;
    void loadCanonicalWhatsNew().then((next) => { if (active) setResult(next); });
    return () => { active = false; };
  }, []);
  // A failed module import is cached by the browser; reload to retry it.
  const retry = () => window.location.reload();

  return (
    <main aria-label="What’s New" className="flex h-full min-h-0 min-w-0 flex-col bg-background text-sm">
      <header className="flex shrink-0 items-center gap-3 px-4 py-3 sm:px-6">
        <SidebarTrigger className="-ml-1 lg:hidden" />
        <h1 ref={titleRef} tabIndex={-1} className="min-w-0 flex-1 text-lg font-semibold outline-none">What’s New</h1>
        <TooltipIconButton tooltip="Close What’s New" className="size-8" onClick={onExit}><X className="size-4" aria-hidden="true" /></TooltipIconButton>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-4xl flex-col gap-4 px-4 pb-6 sm:px-6">
          <a className="inline-flex min-h-8 items-center gap-1 self-start rounded-sm text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring" href={CHANGELOG_URL} target="_blank" rel="noreferrer">Full changelog <ExternalLink className="size-3.5" aria-hidden="true" /></a>
          {result.status === "loading" ? (
            <p role="status" className="flex items-center gap-2 py-6 text-muted-foreground"><Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading release notes…</p>
          ) : result.status === "error" ? (
            <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl bg-card p-4"><p className="min-w-0 flex-1 text-muted-foreground">{result.message}</p><Button variant="outline" size="sm" onClick={retry}><RefreshCw className="size-4" aria-hidden="true" /> Retry</Button></div>
          ) : result.status === "empty" ? (
            <p role="status" className="py-6 text-muted-foreground">There are no published changelog entries yet.</p>
          ) : result.sections.map((section) => (
            <section key={`${section.version}-${section.date ?? ""}`} className="min-w-0 rounded-xl bg-card/70 p-4 sm:p-5">
              <div className="flex flex-wrap items-baseline gap-2"><h2 className="text-sm font-semibold">{section.version === "Unreleased" ? "Unreleased" : `Kollab ${section.version}`}</h2>{section.date ? <span className="text-xs text-muted-foreground">{section.date}</span> : null}</div>
              {section.groups.map((group) => (
                <div key={group.category} className="mt-4"><h3 className="text-xs font-medium text-muted-foreground">{group.category}</h3><ul className="mt-2 list-disc space-y-2 pl-4">{group.items.map((item, index) => <li key={`${index}-${item}`} className="break-words text-sm text-foreground/90">{item}</li>)}</ul></div>
              ))}
            </section>
          ))}
        </div>
      </div>
    </main>
  );
}
