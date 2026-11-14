import type { ComponentProps } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { LearnPage } from "./LearnPage";

export function LearnDialog(props: ComponentProps<typeof LearnPage>) {
  return (
    <Dialog open onOpenChange={(open) => { if (!open) props.onExit(); }}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[min(48rem,88dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(64rem,calc(100%-2rem))]"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => {
          // An enlarged preview is a nested dialog. Radix can deliver the first Escape to this outer dialog
          // before the nested one registers as the top layer; the outer then consumes the event, so the nested
          // dialog never closes. Close the nested preview here instead, and keep Learn open.
          const previewClose = document.querySelector<HTMLButtonElement>('[data-learn-preview][data-state="open"] [data-slot="dialog-close"]');
          if (!previewClose) return;
          event.preventDefault();
          previewClose.click();
        }}
      >
        <DialogTitle className="sr-only">Learn</DialogTitle>
        <DialogDescription className="sr-only">Reference topics for using Kollab.</DialogDescription>
        <LearnPage {...props} />
      </DialogContent>
    </Dialog>
  );
}
