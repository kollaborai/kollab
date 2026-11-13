import { X } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * One quiet line above the chat for something the person can act on: plain words, the action beside
 * them, and a way to dismiss it. Versions, build ids and error codes belong in `detail`, a hover
 * away, never in the line. `urgent` is for what needs reading now: it is announced at once and reads
 * in full contrast.
 */
export function NoticeBar({
  children,
  detail,
  details,
  urgent = false,
  actions,
  onDismiss,
}: {
  children: ReactNode;
  detail?: string;
  details?: ReactNode;
  urgent?: boolean;
  actions?: ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div
      role={urgent ? "alert" : "status"}
      aria-live={urgent ? "assertive" : "polite"}
      title={detail}
      className="text-muted-foreground flex min-h-7 flex-wrap items-center gap-x-2 gap-y-1 border-b px-3 py-1 text-xs"
    >
      <span className={cn("min-w-0 flex-1", urgent && "text-foreground")}>{children}</span>
      {actions}
      {details}
      {onDismiss ? (
        <Button type="button" variant="ghost" size="icon-xs" aria-label="Dismiss" onClick={onDismiss}>
          <X />
        </Button>
      ) : null}
    </div>
  );
}

/** A notice's button: the way out of what the line says. */
export function NoticeAction(props: Omit<ComponentProps<typeof Button>, "variant" | "size">) {
  return <Button type="button" variant="outline" size="xs" className="text-foreground" {...props} />;
}
