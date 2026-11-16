import { useEffect, useRef, useState } from "react";
import { CircleAlert, Play, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { getLearnMediaSource } from "./phone-media-map";
import { prefersReducedMotion } from "./media";
import { getLearnTopic, isLearnTopicId, LEARN_TOPICS, type LearnRoute, type LearnTopicId, type LearnWorkflow } from "@/learn/catalog";

type LearnPageProps = {
  route: LearnRoute;
  onNavigate: (topicId: LearnTopicId) => void;
  onExit: () => void;
};

function WorkflowMedia({ media, success }: { media: LearnWorkflow["media"]; success: string }) {
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [watching, setWatching] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const mediaSource = media ? getLearnMediaSource(media.src) : null;
  const posterSource = media?.poster ? getLearnMediaSource(media.poster) : null;
  const captionsSource = media?.captions ? getLearnMediaSource(media.captions) : null;

  useEffect(() => {
    setFailed(false);
    setRetry(0);
    setWatching(false);
  }, [media?.src]);

  useEffect(() => {
    if (!media || media.kind !== "video" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const stopMotion = () => {
      if (query.matches) videoRef.current?.pause();
    };
    stopMotion();
    query.addEventListener("change", stopMotion);
    return () => query.removeEventListener("change", stopMotion);
  }, [media]);

  useEffect(() => {
    if (watching && !prefersReducedMotion()) {
      void videoRef.current?.play().catch(() => {});
    }
  }, [watching, retry]);

  if (!media) return null;
  const usePosterFallback = media.kind === "video" && !mediaSource && Boolean(posterSource);
  const showPoster = media.kind === "video" && Boolean(posterSource) && (!watching || usePosterFallback);
  const unavailable = failed || (!mediaSource && !usePosterFallback);
  const imageSource = showPoster ? posterSource : mediaSource;
  const preview = () => (
    <Dialog>
      <DialogTrigger asChild>
        <button type="button" aria-label="Enlarge product preview" className="block w-full cursor-zoom-in rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <img key={`${media.src}-${retry}`} className="max-h-[min(38vh,24rem)] w-full rounded-lg bg-muted/30 object-contain" src={imageSource!} alt={media.alt} loading="lazy" onError={() => setFailed(true)} />
        </button>
      </DialogTrigger>
      <DialogContent data-learn-preview="" className="inset-0 top-0 left-0 h-dvh w-screen max-w-none translate-x-0 translate-y-0 grid-rows-[auto_minmax(0,1fr)] gap-2 rounded-none border-0 p-4 sm:max-w-none">
        <DialogTitle className="pr-8 text-sm">Product preview</DialogTitle>
        <DialogDescription className="sr-only">{media.alt}</DialogDescription>
        <div className="min-h-0 overflow-auto overscroll-contain">
          <img className="max-w-none" src={imageSource!} alt={media.alt} />
        </div>
      </DialogContent>
    </Dialog>
  );
  return (
    <figure className="space-y-2">
      {unavailable ? (
        <div role="group" aria-label="Lesson media recovery" className="flex min-h-40 flex-col items-center justify-center gap-2 rounded-lg bg-muted/35 px-4 py-6 text-center">
          <p role="status" className="text-sm text-muted-foreground">The lesson preview could not be loaded. You can still follow the steps below.</p>
          {imageSource ? <Button type="button" variant="outline" size="sm" onClick={() => { setFailed(false); setRetry((count) => count + 1); }}>Try again</Button> : <p className="text-xs text-muted-foreground">Reconnect to your computer and refresh Kollab to try again.</p>}
        </div>
      ) : showPoster ? (
        preview()
      ) : media.kind === "video" ? (
        <video
          key={`${media.src}-${retry}`}
          ref={videoRef}
          className="max-h-[min(48vh,28rem)] w-full rounded-lg bg-muted/30 object-contain"
          src={mediaSource ?? undefined}
          poster={posterSource ?? undefined}
          controls
          muted
          playsInline
          preload="metadata"
          aria-label={media.alt}
          onError={() => setFailed(true)}
        >
          {captionsSource ? <track kind="captions" src={captionsSource} srcLang="en" label="English captions" default /> : null}
        </video>
      ) : (
        preview()
      )}
      {media.caption || (media.kind === "video" && mediaSource) ? <figcaption className="flex items-start justify-between gap-3 text-xs text-muted-foreground">
        <span>{media.caption?.trim() !== success.trim() ? media.caption : ""}</span>
        {media.kind === "video" && mediaSource ? <Button type="button" variant="outline" size="sm" className="h-7 shrink-0 gap-1.5 px-2 text-xs" onClick={() => setWatching((value) => !value)}>{!watching ? <Play className="size-3" aria-hidden="true" /> : null}{watching ? "Show preview" : "Watch walkthrough"}</Button> : null}
      </figcaption> : null}
    </figure>
  );
}

function WorkflowContent({ workflow, topicSummary }: { workflow: LearnWorkflow; topicSummary: string }) {
  const entryPath = workflow.entry.ui?.join(" → ");
  const distinctSummary = workflow.summary?.trim() !== topicSummary.trim() ? workflow.summary : undefined;
  return (
    <div className="space-y-4 pt-2">
      {distinctSummary && !workflow.media ? <p className="max-w-3xl text-sm text-muted-foreground">{distinctSummary}</p> : null}
      <section aria-label="Where to start" className="space-y-1 text-xs text-muted-foreground">
        {entryPath || workflow.entry.command ? <p className="break-words">{entryPath ? <><span className="font-medium text-foreground">Open:</span> {entryPath}</> : null}{entryPath && workflow.entry.command ? " · or " : null}{workflow.entry.command ? <code>{workflow.entry.command}</code> : null}</p> : null}
      </section>
      <WorkflowMedia media={workflow.media} success={workflow.success} />
      {workflow.entry.prerequisite ? <p className="text-xs text-muted-foreground">{workflow.entry.prerequisite}</p> : null}
      <div className={`grid min-w-0 gap-5 ${workflow.commands?.length ? "lg:grid-cols-[minmax(0,1fr)_minmax(15rem,0.85fr)]" : ""}`}>
        <section aria-label="Steps" className="space-y-3">
          <h2 className="text-sm font-medium">Steps</h2>
          <ol className="list-decimal space-y-3 pl-5 text-sm leading-relaxed marker:text-muted-foreground">
            {workflow.steps.map((step, index) => (
              <li key={`${workflow.id}-${index}`}>
                <span>{step.action}</span>
                {step.expect ? <p className="mt-1 text-xs text-muted-foreground">Expected: {step.expect}</p> : null}
              </li>
            ))}
          </ol>
          <section aria-label="Expected result" className="rounded-md bg-muted/30 px-3 py-2 text-sm"><p><span className="font-medium">Expected result: </span>{workflow.success}</p></section>
          {workflow.recovery.length ? <details className="pt-1 text-sm">
            <summary className="cursor-pointer font-medium">If something goes wrong</summary>
            <ul className="mt-2 space-y-2 pl-4">
              {workflow.recovery.map((item, index) => <li key={`${workflow.id}-recovery-${index}`}><span className="font-medium">{item.symptom}: </span><span className="text-muted-foreground">{item.action}</span></li>)}
            </ul>
          </details> : null}
        </section>
        {workflow.commands?.length ? <section aria-label="Command examples" className="min-w-0 space-y-3">
          <h2 className="text-sm font-medium">Command examples</h2>
          {workflow.commands.map((command) => <div key={command.syntax} className="space-y-1.5">
            <p className="text-sm font-medium">{command.description}</p>
            <p className="break-words text-xs text-muted-foreground">Syntax: <code>{command.syntax}</code></p>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-muted/45 px-3 py-2 text-xs"><code>{command.example}</code></pre>
            {command.expect ? <p className="text-xs text-muted-foreground">Expected: {command.expect}</p> : null}
          </div>)}
        </section> : null}
      </div>
      {workflow.note ? <p className="text-xs text-muted-foreground">{workflow.note}</p> : null}
    </div>
  );
}

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
      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] md:grid-cols-[12rem_minmax(0,1fr)] md:grid-rows-1">
        <nav aria-label="Learn topics" className="min-w-0 overflow-hidden bg-muted/20 pt-9 md:overflow-y-auto md:pt-0">
          <div className="flex gap-1 overflow-x-auto p-2 md:flex-col md:gap-1 md:p-3">
            {LEARN_TOPICS.map((item) => <Button key={item.id} type="button" variant="ghost" size="sm" aria-current={topic?.id === item.id ? "page" : undefined} onClick={() => onNavigate(item.id)} className={`h-auto min-h-9 justify-start px-3 py-2 text-left hover:bg-transparent md:w-full ${topic?.id === item.id ? "font-medium text-foreground underline decoration-highlight decoration-2 underline-offset-8 md:no-underline md:bg-muted/70 md:hover:bg-muted/70" : "font-normal text-muted-foreground hover:text-foreground"}`}>
              <span className="min-w-0 whitespace-nowrap md:whitespace-normal">{item.id === "networks" ? "Connect" : item.title}</span>
            </Button>)}
          </div>
        </nav>
        <div ref={topicScrollRef} className="min-h-0 min-w-0 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-4 py-4 sm:px-6 sm:py-6 md:px-8">
            {route.kind === "invalid" ? <section className="space-y-3" role="alert">
              <CircleAlert className="size-5 text-destructive" aria-hidden="true" />
              <h1 ref={topicHeadingRef} tabIndex={-1} className="text-lg font-medium outline-none">That topic is not available</h1>
              <p className="text-sm text-muted-foreground">“{route.requestedTopic}” is not a Learn topic. Choose a topic from the menu.</p>
              <Button type="button" variant="outline" onClick={() => onNavigate(LEARN_TOPICS[0].id)}>Browse topics</Button>
            </section> : !topic ? <section role="status" className="space-y-3">
              <h1 ref={topicHeadingRef} tabIndex={-1} className="text-lg font-medium outline-none">Topics are not available in this build</h1>
              <p className="text-sm text-muted-foreground">Refresh the Web UI. If topics are still missing, update the Web UI bundle.</p>
              <Button type="button" variant="outline" onClick={() => window.location.reload()}><RefreshCw aria-hidden="true" /> Refresh Learn</Button>
            </section> : <>
              <section className="space-y-2 pr-8">
                <h1 ref={topicHeadingRef} tabIndex={-1} className="text-lg font-medium outline-none">{topic.id === "networks" ? "Connect" : topic.title}</h1>
                <p className="max-w-3xl text-sm text-muted-foreground">{topic.summary}</p>
                {topic.note ? <p className="max-w-3xl text-xs text-muted-foreground">{topic.note}</p> : null}
                {topic.related?.some(isLearnTopicId) ? <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground" aria-label="Related topics">
                  <span>Related:</span>
                  {topic.related.filter(isLearnTopicId).map((relatedId) => {
                    const relatedTopic = getLearnTopic(relatedId);
                    return <Button key={relatedId} type="button" variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => onNavigate(relatedTopic.id)}>{relatedTopic.id === "networks" ? "Connect" : relatedTopic.title}</Button>;
                  })}
                </p> : null}
              </section>
              {topic.workflows.length ? <Tabs key={topic.id} defaultValue={topic.workflows[0].id} className="min-w-0">
                {topic.workflows.length > 1 ? <TabsList aria-label={`${topic.title} workflows`} className="h-auto w-full flex-wrap justify-start rounded-none bg-transparent p-0">
                  {topic.workflows.map((workflow) => <TabsTrigger key={workflow.id} value={workflow.id} className="flex-none px-3 py-2">{workflow.label}</TabsTrigger>)}
                </TabsList> : null}
                {topic.workflows.map((workflow) => <TabsContent key={workflow.id} value={workflow.id} className="min-w-0">
                  <WorkflowContent workflow={workflow} topicSummary={topic.summary} />
                </TabsContent>)}
              </Tabs> : <p role="alert" className="text-sm text-muted-foreground">This lesson has no workflows yet.</p>}
            </>}
          </div>
        </div>
      </div>
    </main>
  );
}
