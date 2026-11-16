import type { ReactNode } from "react";
import { DialogContent } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export function PanelDialogSurface({ children, responsive = false }: { children: ReactNode; responsive?: boolean }) {
  return (
    <DialogContent className={cn("flex h-[min(52rem,92dvh)] w-full flex-col gap-0 overflow-hidden border-0 bg-background p-0 sm:max-w-[min(72rem,94vw)]",
      responsive && "max-md:!top-0 max-md:!left-auto max-md:!right-0 max-md:!h-dvh max-md:!max-w-none max-md:!translate-x-0 max-md:!translate-y-0 max-md:!rounded-none")}>
      {children}
    </DialogContent>
  );
}
