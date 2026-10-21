import type { MouseEvent } from "react";
import { ArrowUpRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { getLearnTopic, learnHref, navigateToLearn, type LearnTopicId } from "@/learn/catalog";

type LearnMoreLinkProps = {
  topicId: LearnTopicId;
  label?: string;
  className?: string;
  onNavigate?: () => void;
};

export function LearnMoreLink({ topicId, label, className, onNavigate }: LearnMoreLinkProps) {
  const topic = getLearnTopic(topicId);
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    onNavigate?.();
    navigateToLearn(topicId);
  };

  return (
    <a
      href={learnHref(topicId)}
      onClick={handleClick}
      className={cn(
        "inline-flex min-h-7 items-center gap-1 rounded-sm text-sm text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        className,
      )}
    >
      {label ?? `Learn more about ${topic.title.toLowerCase()}`}
      <ArrowUpRight className="size-3.5 shrink-0" aria-hidden="true" />
    </a>
  );
}
