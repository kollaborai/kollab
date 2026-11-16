import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { usePane } from "@/components/code/editor-store";

interface BreadcrumbProps {
  paneId: string;
  rootPath: string;
}

export function Breadcrumb({ paneId, rootPath }: BreadcrumbProps) {
  const { activeFile } = usePane(paneId);
  const activeFilePath = activeFile?.path ?? null;

  if (!activeFilePath) return null;

  const relative = activeFilePath.startsWith(rootPath)
    ? activeFilePath.slice(rootPath.length + 1)
    : activeFilePath;

  const segments = relative.split("/");

  return (
    <div className="flex items-center gap-0.5 px-3 py-0.5 shrink-0 bg-transparent overflow-x-auto [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {segments.map((seg, i) => {
        const isLast = i === segments.length - 1;
        return (
          <div key={i} className="flex items-center gap-0.5 shrink-0">
            {i > 0 && (
              <ChevronRight
                aria-hidden="true"
                className="h-2.5 w-2.5 shrink-0 text-muted-foreground/40"
              />
            )}
            <span
              className={cn(
                "text-[10px] font-mono",
                isLast
                  ? "text-foreground/80"
                  : "text-muted-foreground/60 hover:text-muted-foreground cursor-default",
              )}
            >
              {seg}
            </span>
          </div>
        );
      })}
    </div>
  );
}
