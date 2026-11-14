import { Button } from "@/components/ui/button";

interface EmptyEditorStateProps {
  showFilePicker: boolean;
  onOpenFiles: () => void;
}

export function EmptyEditorState({ showFilePicker, onOpenFiles }: EmptyEditorStateProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="relative flex flex-1 flex-col items-center justify-center gap-4">
        <div className="absolute inset-0 text-muted-foreground opacity-[0.03]" style={{ backgroundImage: "radial-gradient(circle, currentColor 1px, transparent 1px)", backgroundSize: "12px 12px" }} />
        <div className="relative flex flex-col items-center gap-3">
          <span className="select-none font-mono text-5xl font-bold tracking-tighter text-muted-foreground/10">{"{ }"}</span>
          <div className="space-y-2 text-center">
            <p className="font-mono text-[11px] text-muted-foreground">select a file to open</p>
            {showFilePicker && <Button type="button" size="lg" className="min-h-10" onClick={onOpenFiles}>Browse files</Button>}
            <div className="flex items-center gap-3 text-[10px] text-muted-foreground/70">
              <kbd className="font-mono">cmd+p</kbd>
              <span>quick open</span>
              <kbd className="font-mono">cmd+\</kbd>
              <span>split</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
