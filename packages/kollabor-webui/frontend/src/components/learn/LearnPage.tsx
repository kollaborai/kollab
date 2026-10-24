import { useEffect, useRef } from "react";
import { CircleAlert, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { getLearnTopic, LEARN_TOPICS, type LearnRoute, type LearnTopicId } from "@/learn/catalog";

type LearnPageProps = {
  route: LearnRoute;
  onNavigate: (topicId: LearnTopicId) => void;
  onExit: () => void;
};

export function LearnPage({ route, onNavigate, onExit }: LearnPageProps) {
  const topicHeadingRef = useRef<HTMLHeadingElement>(null);
  const topicScrollRef = useRef<HTMLDivElement>(null);
  const topic = route.kind === "topic" ? getLearnTopic(route.topicId) : null;

  useEffect(() => {
    topicScrollRef.current?.scrollTo({ top: 0 });
    topicHeadingRef.current?.focus({ preventScroll: true });
  }, [route]);

  return (
    <main aria-label="Learn" className="relative flex h-full min-h-0 min-w-0 flex-col bg-background text-sm text-foreground">
      <TooltipIconButton tooltip="Close Learn" className="absolute right-2 top-2 z-10 size-8" onClick={onExit}>
        <X className="size-4" aria-hidden="true" />
      </TooltipIconButton>
      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] md:grid-cols-[15rem_minmax(0,1fr)] md:grid-rows-1">
        <nav aria-label="Learn topics" className="min-w-0 overflow-hidden bg-muted/20 pt-9 md:overflow-y-auto md:pt-0">
          <div className="flex gap-1 overflow-x-auto p-2 md:flex-col md:gap-1 md:p-3">
            {LEARN_TOPICS.map((item) => (
              <Button
                key={item.id}
                type="button"
                variant="ghost"
                size="sm"
                aria-current={topic?.id === item.id ? "page" : undefined}
                onClick={() => onNavigate(item.id)}
                className={`h-auto min-h-9 justify-start px-3 py-2 text-left font-normal md:w-full ${
                  topic?.id === item.id ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <span className="min-w-0 whitespace-nowrap md:whitespace-normal">{item.title}</span>
              </Button>
            ))}
          </div>
        </nav>
        <div ref={topicScrollRef} className="min-h-0 min-w-0 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-4 py-4 sm:px-6 sm:py-6 md:px-8">
            {route.kind === "invalid" ? (
              <section className="rounded-2xl bg-destructive/5 p-5 sm:p-7" role="alert">
                <CircleAlert className="mb-3 size-5 text-destructive" aria-hidden="true" />
                <h1 ref={topicHeadingRef} tabIndex={-1} className="text-lg font-semibold outline-none">That topic is not available</h1>
                <p className="mt-2 text-sm text-muted-foreground">“{route.requestedTopic}” is not a Learn topic. Choose a topic from the menu.</p>
                <Button type="button" className="mt-4" onClick={() => onNavigate(LEARN_TOPICS[0].id)}>Browse topics</Button>
              </section>
            ) : !topic ? (
              <section className="rounded-2xl bg-card p-5 sm:p-7" role="status">
                <h1 ref={topicHeadingRef} tabIndex={-1} className="text-lg font-semibold outline-none">Topics are not available in this build</h1>
                <p className="mt-2 text-sm text-muted-foreground">Refresh the Web UI. If topics are still missing, update the Web UI bundle.</p>
                <Button type="button" variant="outline" className="mt-4" onClick={() => window.location.reload()}><RefreshCw className="size-4" aria-hidden="true" /> Refresh Learn</Button>
              </section>
            ) : (
              <>
                <section className="rounded-2xl bg-card/70 p-5 sm:p-7">
                  <h1 ref={topicHeadingRef} tabIndex={-1} className="pr-8 text-lg font-semibold outline-none">{topic.title}</h1>
                  <p className="mt-3 text-sm text-muted-foreground">{topic.summary}</p>
                </section>
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(15rem,0.8fr)]">
                  <section className="rounded-2xl bg-card p-5 sm:p-6" aria-labelledby="learn-howto">
                    <h2 id="learn-howto" className="text-sm font-semibold">How to get started</h2>
                    <ol className="mt-4 space-y-3">
                      {topic.howTo.map((step, index) => (
                        <li key={step} className="flex items-start gap-3 text-sm">
                          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold">{index + 1}</span>
                          <span className="min-w-0 text-foreground/90">{step}</span>
                        </li>
                      ))}
                    </ol>
                  </section>
                  <section className="rounded-2xl bg-primary/[0.04] p-5 sm:p-6" aria-labelledby="learn-example">
                    <h2 id="learn-example" className="text-sm font-semibold">Try an example</h2>
                    <p className="mt-3 whitespace-pre-wrap break-words rounded-xl bg-background/75 p-3.5 text-sm">{topic.example}</p>
                    {topic.note ? <p className="mt-3 rounded-lg bg-amber-500/5 px-3 py-2 text-xs text-muted-foreground"><span className="font-semibold text-foreground">Good to know: </span>{topic.note}</p> : null}
                  </section>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
