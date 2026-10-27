import type { ReactNode } from "react";
import { Info } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Help for one element, shown on hover instead of a link: a tiny info icon
 * appears while the pointer is over the element (or focus is inside it) and,
 * after a short dwell, a small card says what the element does.
 */
export function HoverHelp({
  text,
  children,
  className,
  side = "top",
}: {
  text: string;
  children: ReactNode;
  className?: string;
  side?: "top" | "right" | "bottom" | "left";
}) {
  return (
    <Tooltip delayDuration={600}>
      <TooltipTrigger asChild>
        <span className={cn("group/help inline-flex min-w-0 items-center gap-1", className)}>
          {children}
          <Info
            aria-hidden="true"
            data-slot="hover-help-icon"
            className="text-muted-foreground size-3 shrink-0 opacity-0 transition-opacity group-hover/help:opacity-100 group-focus-within/help:opacity-100"
          />
        </span>
      </TooltipTrigger>
      <TooltipContent side={side} sideOffset={4} className="max-w-64 text-left">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}
