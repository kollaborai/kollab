import type { ReactNode } from "react";
import { DialogContent } from "@/components/ui/dialog";

const SURFACE =
  "flex h-[min(52rem,92dvh)] w-full flex-col gap-0 overflow-hidden border-0 bg-background p-0 sm:max-w-[min(72rem,94vw)]";

// A responsive surface covers the whole viewport below md instead of floating as a card.
const FULL_BELOW_MD =
  "max-md:inset-0 max-md:h-dvh max-md:max-w-none max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none";

export function PanelDialogSurface({
  children,
  responsive = false,
}: {
  children: ReactNode;
  responsive?: boolean;
}) {
  return (
    <DialogContent className={responsive ? `${SURFACE} ${FULL_BELOW_MD}` : SURFACE}>
      {children}
    </DialogContent>
  );
}
