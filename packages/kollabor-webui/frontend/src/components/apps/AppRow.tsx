import { useEffect, useState, type ReactNode } from "react";
import { Check, Loader2 } from "lucide-react";
import type { AppRow } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { AppIcon } from "./AppIcon";

const PENDING_LABEL: Record<string, string> = {
  add: "Adding…",
  enable: "Turning on…",
  remove: "Removing…",
};
/** A Remove click waits this long for its confirming second click. */
const CONFIRM_MS = 4000;

/**
 * One app: icon, name, badges, a one-line description, and the action for its
 * state on the right. Clicking the text toggles the detail under the row.
 */
export function AppRowItem({
  row,
  pending,
  onAction,
}: {
  row: AppRow;
  /** The action running on this row. A CLI install can take minutes. */
  pending?: string;
  onAction: (action: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(false), CONFIRM_MS);
    return () => window.clearTimeout(timer);
  }, [armed]);

  const control = (): ReactNode => {
    if (pending) {
      return (
        <Button type="button" size="sm" variant="secondary" className="rounded-full" disabled>
          <Loader2 className="animate-spin" />
          {PENDING_LABEL[pending] ?? "Working…"}
        </Button>
      );
    }
    if (row.state === "available") {
      return (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="rounded-full"
          aria-label={`Add ${row.label}`}
          onClick={() => onAction("add")}
        >
          Add
        </Button>
      );
    }
    if (row.state === "off") {
      return (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="rounded-full"
          aria-label={`Turn on ${row.label}`}
          onClick={() => onAction("enable")}
        >
          Turn On
        </Button>
      );
    }
    if (row.state === "added") {
      if (row.actions && !row.actions.includes("remove")) {
        return (
          <span className="inline-flex h-8 items-center gap-1 px-3 text-sm font-medium">
            <Check className="size-4" />
            Added
          </span>
        );
      }
      // Hover or focus swaps "✓ Added" for "Remove". The first click arms it, the second removes.
      return (
        <Button
          type="button"
          size="sm"
          variant={armed ? "destructive" : "ghost"}
          className="rounded-full"
          onClick={() => {
            if (!armed) {
              setArmed(true);
              return;
            }
            setArmed(false);
            onAction("remove");
          }}
        >
          <span
            className={
              armed
                ? "hidden"
                : "inline-flex items-center gap-1 group-hover/row:hidden group-focus-within/row:hidden"
            }
          >
            <Check />
            Added
          </span>
          <span
            className={
              armed ? "inline" : "hidden group-hover/row:inline group-focus-within/row:inline"
            }
          >
            {armed ? "Confirm" : "Remove"}
          </span>
        </Button>
      );
    }
    // Unavailable: the note says what is missing, on hover and focus.
    const add = (
      <Button
        type="button"
        size="sm"
        variant="secondary"
        className="rounded-full"
        disabled
        aria-label={`Add ${row.label}`}
      >
        Add
      </Button>
    );
    if (!row.note) return add;
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          {/* A disabled button takes no pointer or focus, so the tooltip sits on this wrapper. */}
          <span tabIndex={0} className="inline-flex rounded-full">
            {add}
          </span>
        </TooltipTrigger>
        <TooltipContent>{row.note}</TooltipContent>
      </Tooltip>
    );
  };

  return (
    <div className="group/row flex min-w-0 flex-col">
      <div className="hover:bg-accent/50 flex min-w-0 items-center gap-3 rounded-xl p-2 transition-colors">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          className="focus-visible:ring-ring/50 flex min-w-0 flex-1 items-center gap-3 rounded-lg text-left outline-none focus-visible:ring-[3px]"
        >
          <AppIcon row={row} />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="min-w-0 truncate text-sm font-medium">{row.label}</span>
              {(row.badges ?? []).map((badge) => (
                <Badge key={badge} variant="secondary" className="shrink-0">
                  {badge}
                </Badge>
              ))}
            </span>
            <span className="text-muted-foreground min-w-0 truncate text-xs">{row.detail}</span>
          </span>
        </button>
        <div className="flex shrink-0 items-center">{control()}</div>
      </div>
      {open ? <AppDetail row={row} /> : null}
    </div>
  );
}

/** The expanded facts: publisher, homepage, the exact Add command, the note. */
function AppDetail({ row }: { row: AppRow }) {
  // Web links only. The catalog is data, and a javascript: URL must never render as a link.
  const homepage = row.homepage && /^https?:\/\//.test(row.homepage) ? row.homepage : null;
  return (
    <div className="bg-muted/40 mx-2 mb-2 flex min-w-0 flex-col gap-2 rounded-lg border p-3 text-sm">
      {row.publisher ? (
        <p className="break-words">
          <span className="text-muted-foreground">Publisher: </span>
          {row.publisher}
        </p>
      ) : null}
      {homepage ? (
        <a
          href={homepage}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary w-fit max-w-full break-all underline underline-offset-4"
        >
          {homepage}
        </a>
      ) : null}
      {row.preview ? (
        <code className="bg-background block min-w-0 rounded-md border px-2 py-1.5 font-mono text-xs break-all">
          {row.preview}
        </code>
      ) : null}
      {row.note ? <p className="text-muted-foreground break-words">{row.note}</p> : null}
    </div>
  );
}
