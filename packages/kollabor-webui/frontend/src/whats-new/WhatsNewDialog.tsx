import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  dialogLines,
  loadSeenRecord,
  readWhatsNewSeenRaw,
  recordWhatsNewSeen,
  sublineVersion,
  type DialogRelease,
} from "./whats-new-model";

export type WhatsNewDialogProps = {
  open: boolean;
  /** The release to announce; null renders nothing. */
  release: DialogRelease | null;
  /** Closing through any path records the version as seen. */
  onOpenChange: (open: boolean) => void;
  /** Open the existing What's New page (the See All Changes link). */
  onSeeAllChanges: () => void;
};

/**
 * Release announcement on the first load of a version this browser has not
 * seen: the guided setup's dialog shell, the release's Added and Changed
 * entries (capped, the rest behind See All Changes), fixes as a count.
 */
export function WhatsNewDialog({
  open,
  release,
  onOpenChange,
  onSeeAllChanges,
}: WhatsNewDialogProps) {
  if (!open || !release) return null;

  const { lines, moreCount } = dialogLines(release);
  const previous = sublineVersion(
    release,
    loadSeenRecord(readWhatsNewSeenRaw()),
  );
  const close = () => {
    recordWhatsNewSeen(release.version);
    onOpenChange(false);
  };
  const seeAllChanges = () => {
    recordWhatsNewSeen(release.version);
    onOpenChange(false);
    onSeeAllChanges();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <DialogContent
        className="flex h-[min(36rem,85dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-xl"
        aria-label={`What’s New in ${release.version}`}
      >
        <div className="flex shrink-0 items-start justify-between gap-2 px-6 pt-5">
          <div className="min-w-0">
            <DialogTitle className="text-lg leading-none font-semibold">
              What’s New in {release.version}
            </DialogTitle>
            {previous ? (
              <DialogDescription className="mt-1.5 break-words">
                Updated from {previous}.
              </DialogDescription>
            ) : null}
          </div>
          <Button
            type="button"
            size="xs"
            variant="ghost"
            aria-label="Close What’s New"
            onClick={close}
          >
            <X />
          </Button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-4">
          {release.added.length > 0 ? (
            <section>
              <h4 className="text-xs font-medium text-muted-foreground">Added</h4>
              <ul className="mt-1.5 list-disc space-y-1.5 pl-4">
                {release.added.map((item, index) => (
                  <li key={`added-${index}`} className="break-words text-sm text-foreground/90">
                    {item}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {release.changed.length > 0 ? (
            <section>
              <h4 className="text-xs font-medium text-muted-foreground">Changed</h4>
              <ul className="mt-1.5 list-disc space-y-1.5 pl-4">
                {release.changed.map((item, index) => (
                  <li key={`changed-${index}`} className="break-words text-sm text-foreground/90">
                    {item}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {moreCount > 0 ? (
            <p className="text-xs text-muted-foreground">And {moreCount} more.</p>
          ) : null}
          {release.fixedCount > 0 ? (
            <p className="text-xs text-muted-foreground">Plus {release.fixedCount} fixes.</p>
          ) : null}
        </div>
        <footer className="flex shrink-0 items-center justify-between gap-2 px-6 pt-3 pb-4">
          <Button type="button" variant="ghost" onClick={seeAllChanges}>
            See All Changes
          </Button>
          <Button type="button" data-testid="whats-new-got-it" onClick={close}>
            Got It
          </Button>
        </footer>
      </DialogContent>
    </Dialog>
  );
}
