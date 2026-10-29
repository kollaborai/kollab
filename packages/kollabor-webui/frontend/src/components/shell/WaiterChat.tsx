import { OctagonPause } from "lucide-react";
import { splitWaiterLine } from "./halted-line";

/** A chat opened on a halt waiter: its line, no composer, no turn (spec "TUI and Web UI"). */
export function WaiterChat({ line, onOpen }: { line: string; onOpen: () => void }) {
  const [said, where] = splitWaiterLine(line);
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-4 py-8">
      <div className="flex max-w-md items-start gap-3" role="status">
        <OctagonPause className="text-destructive mt-0.5 size-5 shrink-0" aria-hidden />
        <p className="min-w-0 text-sm leading-relaxed break-words">
          {said}
          {where ? (
            <button type="button" className="underline underline-offset-2" onClick={onOpen}>
              {where}
            </button>
          ) : null}
          {where ? "." : null}
        </p>
      </div>
    </div>
  );
}
