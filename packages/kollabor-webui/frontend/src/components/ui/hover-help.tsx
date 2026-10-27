import { useState, type ReactNode } from "react";
import { Info } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Help for one element, shown on hover instead of a link: a tiny info icon
 * appears while the pointer is over the element (or focus is inside it) and,
 * after a short dwell, a small card says what the element does. On a touch
 * screen the icon is always visible and a tap on it opens or closes the card.
 */
export function HoverHelp({
  text,
  children,
  className,
  side = "top",
  align = "center",
}: {
  text: string;
  children: ReactNode;
  className?: string;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
}) {
  const [open, setOpen] = useState(false);
  // A tap on the icon pins the card: a touch pointer "leaves" on release, which would close it at once.
  const [pinned, setPinned] = useState(false);
  const dismiss = () => {
    setPinned(false);
    setOpen(false);
  };
  return (
    <Tooltip delayDuration={600} open={open} onOpenChange={(next) => (next || !pinned) && setOpen(next)}>
      <TooltipTrigger asChild>
        <span className={cn("group/help inline-flex min-w-0 items-center gap-1", className)}>
          {children}
          <button
            type="button"
            tabIndex={-1}
            aria-label="What is this?"
            data-slot="hover-help-icon"
            // The trigger closes the card on click and pointer-down; a tap on the icon toggles it instead.
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              if (pinned) return dismiss();
              setPinned(true);
              setOpen(true);
            }}
            className="text-muted-foreground relative shrink-0 opacity-0 transition-opacity group-hover/help:opacity-100 group-focus-within/help:opacity-100 [@media(pointer:coarse)]:opacity-100 after:absolute after:-inset-3 after:content-['']"
          >
            <Info aria-hidden="true" className="size-3" />
          </button>
        </span>
      </TooltipTrigger>
      <TooltipContent
        side={side}
        align={align}
        sideOffset={4}
        className="max-w-64 text-left"
        onPointerDownOutside={dismiss}
        onEscapeKeyDown={dismiss}
      >
        {text}
      </TooltipContent>
    </Tooltip>
  );
}
