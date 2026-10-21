import type { ComponentProps } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { LearnPage } from "./LearnPage";

export function LearnDialog(props: ComponentProps<typeof LearnPage>) {
  return (
    <Dialog open onOpenChange={(open) => { if (!open) props.onExit(); }}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[min(40rem,80dvh)] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(64rem,calc(100%-2rem))]"
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <DialogTitle className="sr-only">Learn</DialogTitle>
        <DialogDescription className="sr-only">Reference topics for using Kollab.</DialogDescription>
        <LearnPage {...props} />
      </DialogContent>
    </Dialog>
  );
}
