import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  CircleAlert,
  ExternalLink,
  Gem,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  getLearnTopic,
  LEARN_TOPICS,
  type LearnRoute,
  type LearnTopicId,
} from "@/learn/catalog";
import {
  loadCanonicalWhatsNew,
  CHANGELOG_URL,
  type WhatsNewResult,
} from "@/learn/changelog";
import {
  applyToggleLearnProgress,
  readLearnProgress,
  writeLearnProgress,
} from "@/learn/progress";

type LearnPageProps = {
  route: LearnRoute;
  onNavigate: (topicId: LearnTopicId) => void;
  onExit: () => void;
};

const CHANGELOG_FALLBACK: WhatsNewResult = { status: "loading" };

export function LearnPage({ route, onNavigate, onExit }: LearnPageProps) {
  const [progress, setProgress] = useState(() => readLearnProgress());
  const [updates, setUpdates] = useState<WhatsNewResult>(CHANGELOG_FALLBACK);

  const topicHeadingRef = useRef<HTMLHeadingElement>(null);
  const topic = route.kind === "topic" ? getLearnTopic(route.topicId) : null;
  const topicIndex = topic ? LEARN_TOPICS.findIndex((item) => item.id === topic.id) : -1;
  const completed = new Set(progress.completed);

  useEffect(() => {
    let active = true;
    void loadCanonicalWhatsNew().then((result) => {
      if (active) setUpdates(result);
    });
    return () => {
      active = false;
    };
  }, []);


  useEffect(() => {
    topicHeadingRef.current?.focus({ preventScroll: true });
  }, [route]);

  const toggleComplete = (topicId: LearnTopicId) => {
    setProgress(applyToggleLearnProgress(progress, topicId));
  };

  const retryUpdates = () => window.location.reload();

  const percent = LEARN_TOPICS.length
    ? Math.round((progress.completed.length / LEARN_TOPICS.length) * 100)
    : 0;

  return (
    <main className="flex h-full min-h-0 min-w-0 flex-col bg-background text-foreground">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border/70 bg-card/70 px-4 py-3 sm:px-6 md:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <SidebarTrigger className="-ml-1 md:hidden" />
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-primary/25 bg-primary/10 text-primary">
            <Gem className="size-5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-[0.65rem] font-semibold uppercase tracking-[0.16em] text-primary">
              <Sparkles className="size-3" aria-hidden="true" />
              The Kollab field guide
            </p>
            <h1 className="truncate text-lg font-semibold tracking-tight sm:text-xl">Learn Kollab</h1>
          </div>
        </div>
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          <div className="min-w-28 flex-1 sm:w-44 sm:flex-none">
            <div className="mb-1 flex items-center justify-between gap-2 text-xs">
              <span className="text-muted-foreground">Your progress</span>
              <span className="font-medium tabular-nums">{progress.completed.length}/{LEARN_TOPICS.length}</span>
            </div>
            <div
              className="h-2 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-label="Learn progress"
              aria-valuemin={0}
              aria-valuemax={LEARN_TOPICS.length}
              aria-valuenow={progress.completed.length}
              aria-valuetext={`${progress.completed.length} of ${LEARN_TOPICS.length} topics complete`}
            >
              <div className="h-full rounded-full bg-primary transition-[width] duration-300" style={{ width: `${percent}%` }} />
            </div>
          </div>
          <Button type="button" variant="outline" size="sm" className="shrink-0" onClick={onExit}>
            <ArrowLeft className="size-4" aria-hidden="true" />
            <span className="hidden sm:inline">Back to chat</span>
            <span className="sm:hidden">Back</span>
          </Button>
        </div>
      </header>

      {progress.warning ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-destructive/30 bg-destructive/5 px-4 py-2 text-sm sm:px-6" role="alert">
          <CircleAlert className="size-4 shrink-0 text-destructive" aria-hidden="true" />
          <span className="min-w-0 flex-1 text-destructive">{progress.warning}</span>
          {progress.canRetry ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setProgress((current) => {
                if (current.retryOperation === "read") return readLearnProgress();
                const warning = writeLearnProgress(current.completed);
                return {
                  ...current,
                  warning,
                  canRetry: Boolean(warning),
                  canPersist: true,
                  retryOperation: warning ? "write" : null,
                };
              })}
            >
              Try again
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] md:grid-cols-[15rem_minmax(0,1fr)] md:grid-rows-1">
        <nav aria-label="Learn topics" className="min-w-0 border-b border-border/70 bg-muted/20 md:overflow-y-auto md:border-r md:border-b-0">
          <div className="flex w-max gap-1.5 overflow-x-auto p-2 md:flex-col md:w-auto md:gap-1 md:p-3">
            {LEARN_TOPICS.map((item, index) => {
              const selected = topic?.id === item.id;
              const isComplete = completed.has(item.id);
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-current={selected ? "page" : undefined}
                  aria-label={`${index + 1}. ${item.title}${isComplete ? ", complete" : ""}`}
                  onClick={() => onNavigate(item.id)}
                  className={`flex min-h-10 shrink-0 items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring md:w-full md:text-sm ${
                    selected
                      ? "border-primary/35 bg-primary/10 text-foreground shadow-sm"
                      : "border-transparent text-muted-foreground hover:border-border hover:bg-accent hover:text-foreground"
                  }`}
                >
                  <span className={`flex size-6 shrink-0 items-center justify-center rounded-full text-[0.65rem] font-semibold tabular-nums ${selected ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`} aria-hidden="true">
                    {isComplete ? <Check className="size-3.5" /> : index + 1}
                  </span>
                  <span className="whitespace-nowrap md:whitespace-normal">{item.title}</span>
                </button>
              );
            })}
          </div>
        </nav>

        <div className="min-h-0 min-w-0 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-5 sm:px-6 sm:py-6 md:px-8 md:py-8">
            <WhatsNewCard result={updates} onRetry={retryUpdates} />

            {route.kind === "invalid" ? (
              <section className="rounded-2xl border border-destructive/30 bg-destructive/5 p-5 sm:p-7" role="alert">
                <div className="mb-3 flex size-10 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
                  <CircleAlert className="size-5" aria-hidden="true" />
                </div>
                <h2 ref={topicHeadingRef} tabIndex={-1} className="text-lg font-semibold">That lesson link is not available</h2>
                <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                  “{route.requestedTopic}” is not a Learn topic. Choose one of the lessons in the list to continue.
                </p>
                <Button type="button" className="mt-4" onClick={() => onNavigate(LEARN_TOPICS[0].id)}>
                  Browse lessons <ArrowRight className="size-4" aria-hidden="true" />
                </Button>
              </section>
            ) : !topic ? (
              <section className="rounded-2xl border border-border bg-card p-5 sm:p-7" role="status">
                <BookOpen className="mb-3 size-6 text-primary" aria-hidden="true" />
                <h2 ref={topicHeadingRef} tabIndex={-1} className="text-lg font-semibold">Lessons are not available in this build</h2>
                <p className="mt-2 text-sm text-muted-foreground">Refresh the Web UI. If the lessons are still missing, ask your Kollab administrator to update the Web UI bundle.</p>
                <Button type="button" variant="outline" className="mt-4" onClick={() => window.location.reload()}>
                  <RefreshCw className="size-4" aria-hidden="true" /> Refresh Learn
                </Button>
              </section>
            ) : (
              <>
                <section className="relative overflow-hidden rounded-2xl border border-primary/20 bg-gradient-to-br from-primary/10 via-card to-card p-5 sm:p-7">
                  <div className="pointer-events-none absolute -right-8 -top-10 size-36 rounded-full bg-primary/5 blur-2xl" aria-hidden="true" />
                  <div className="relative flex flex-wrap items-start justify-between gap-4">
                    <div className="max-w-3xl">
                      <p className="mb-2 text-xs font-semibold uppercase tracking-[0.15em] text-primary">
                        Lesson {topicIndex + 1} of {LEARN_TOPICS.length}
                      </p>
                      <h2 ref={topicHeadingRef} tabIndex={-1} className="text-2xl font-semibold tracking-tight sm:text-3xl">{topic.title}</h2>
                      <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground sm:text-base">{topic.summary}</p>
                    </div>
                    {completed.has(topic.id) ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/25 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary">
                        <Check className="size-3.5" aria-hidden="true" /> Completed
                      </span>
                    ) : null}
                  </div>
                </section>

                <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(15rem,0.8fr)]">
                  <section className="rounded-2xl border border-border bg-card p-5 sm:p-6" aria-labelledby="learn-howto">
                    <h3 id="learn-howto" className="flex items-center gap-2 text-base font-semibold">
                      <span className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
                        <BookOpen className="size-4" aria-hidden="true" />
                      </span>
                      How to get started
                    </h3>
                    <ol className="mt-4 space-y-3">
                      {topic.howTo.map((step, index) => (
                        <li key={step} className="flex items-start gap-3 text-sm leading-6">
                          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{index + 1}</span>
                          <span className="min-w-0 text-foreground/90">{step}</span>
                        </li>
                      ))}
                    </ol>
                  </section>

                  <section className="rounded-2xl border border-primary/20 bg-primary/[0.04] p-5 sm:p-6" aria-labelledby="learn-example">
                    <h3 id="learn-example" className="flex items-center gap-2 text-base font-semibold">
                      <Sparkles className="size-4 text-primary" aria-hidden="true" />
                      Try an example
                    </h3>
                    <p className="mt-3 whitespace-pre-wrap break-words rounded-xl border border-border/70 bg-background/75 p-3.5 text-sm leading-6">{topic.example}</p>
                    {topic.note ? (
                      <p className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs leading-5 text-muted-foreground">
                        <span className="font-semibold text-foreground">Good to know: </span>{topic.note}
                      </p>
                    ) : null}
                  </section>
                </div>

                <section className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-card/70 p-4 sm:px-5">
                  <div className="flex min-w-0 flex-wrap items-center gap-3">
                    <Button
                      type="button"
                      variant="outline"
                      className={
                        completed.has(topic.id)
                          ? "border-primary/35 bg-primary/10 text-primary hover:bg-primary/15"
                          : "border-border bg-background text-foreground hover:border-primary/40"
                      }
                      aria-pressed={completed.has(topic.id)}
                      onClick={() => toggleComplete(topic.id)}
                    >
                      {completed.has(topic.id) ? <Check className="size-4" aria-hidden="true" /> : <Gem className="size-4" aria-hidden="true" />}
                      {completed.has(topic.id) ? "Mark incomplete" : "Mark complete"}
                    </Button>
                    <span className="text-xs text-muted-foreground" aria-live="polite">
                      {progress.warning
                        ? progress.retryOperation === "read"
                          ? "Saved entries are left untouched until storage can be read."
                          : progress.canPersist
                            ? "Progress cannot be saved in this browser yet."
                            : "Saved progress is left untouched until repaired."
                        : progress.completed.length > 0
                          ? "Progress is saved in this browser."
                          : "Progress will be saved in this browser as you complete lessons."}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={topicIndex <= 0}
                      onClick={() => onNavigate(LEARN_TOPICS[topicIndex - 1].id)}
                      aria-label="Previous lesson"
                    >
                      <ArrowLeft className="size-4" aria-hidden="true" /> Previous
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      disabled={topicIndex >= LEARN_TOPICS.length - 1}
                      onClick={() => onNavigate(LEARN_TOPICS[topicIndex + 1].id)}
                      aria-label="Next lesson"
                    >
                      Next <ArrowRight className="size-4" aria-hidden="true" />
                    </Button>
                  </div>
                </section>
              </>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}

function WhatsNewCard({ result, onRetry }: { result: WhatsNewResult; onRetry: () => void }) {
  return (
    <section className="rounded-2xl border border-border bg-card/70 p-4 sm:p-5" aria-labelledby="whats-new-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="whats-new-heading" className="flex items-center gap-2 text-sm font-semibold sm:text-base">
          <Sparkles className="size-4 text-primary" aria-hidden="true" /> What’s new
        </h2>
        <a className="inline-flex min-h-7 items-center gap-1 rounded-sm text-xs text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring" href={CHANGELOG_URL} target="_blank" rel="noreferrer">
          Full changelog <ExternalLink className="size-3.5" aria-hidden="true" />
        </a>
      </div>

      {result.status === "loading" ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2" role="status" aria-live="polite">
          {[0, 1].map((index) => (
            <div
              key={index}
              aria-hidden={index === 1}
              className="min-h-48 rounded-xl border border-border/70 bg-background/60 p-3"
            >
              {index === 0 ? (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading release notes…
                </p>
              ) : null}
              <div className="mt-4 h-2 w-2/3 animate-pulse rounded bg-muted" aria-hidden="true" />
              <div className="mt-3 h-2 w-full animate-pulse rounded bg-muted" aria-hidden="true" />
              <div className="mt-2 h-2 w-4/5 animate-pulse rounded bg-muted" aria-hidden="true" />
            </div>
          ))}
        </div>
      ) : result.status === "error" ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm" role="alert">
          <CircleAlert className="size-4 shrink-0 text-destructive" aria-hidden="true" />
          <span className="min-w-0 flex-1 text-muted-foreground">{result.message}</span>
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw className="size-3.5" aria-hidden="true" /> Retry
          </Button>
        </div>
      ) : result.status === "empty" ? (
        <p className="mt-3 text-sm text-muted-foreground" role="status">
          There are no published changelog entries yet. Check the full changelog for the latest notes.
        </p>
      ) : (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {result.sections.map((section) => (
            <div key={`${section.version}-${section.date ?? ""}`} className="min-h-48 min-w-0 rounded-xl border border-border/70 bg-background/60 p-3">
              <p className="text-xs font-semibold text-primary">
                {section.version === "Unreleased" ? "Unreleased" : `Kollab ${section.version}`}
                {section.date ? <span className="ml-1 font-normal text-muted-foreground">· {section.date}</span> : null}
              </p>
              <ul className="mt-2 space-y-2">
                {section.groups
                  .flatMap((group) => group.items.map((item) => ({ category: group.category, item })))
                  .slice(0, 3)
                  .map(({ category, item }) => (
                    <li key={`${category}-${item}`} className="flex items-start gap-2 text-xs leading-5 text-muted-foreground">
                      <span className="mt-1 size-1.5 shrink-0 rounded-full bg-primary/70" aria-hidden="true" />
                      <span className="line-clamp-2"><span className="font-medium text-foreground">{category}: </span>{item}</span>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
