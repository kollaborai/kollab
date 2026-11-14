import type { ReactNode } from "react";
import { DialogContent } from "@/components/ui/dialog";

export function PanelDialogSurface({ children }: { children: ReactNode }) {
  return (
    <DialogContent className="flex h-[min(52rem,92dvh)] w-full flex-col gap-0 overflow-hidden border-0 bg-background p-0 sm:max-w-[min(72rem,94vw)]">
      {children}
    </DialogContent>
  );
}
