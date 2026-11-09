import { useId, useMemo, useState } from "react";
import { Check, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { todoItems } from "@/utils/todo-list";

// Whether the card is open, per browser. Unset until the person opens or closes it once.
const OPEN_KEY = "kollab.session-panel.todo";

function storedOpen(): boolean | null {
  try {
    const value = window.localStorage.getItem(OPEN_KEY);
    return value === "open" ? true : value === "closed" ? false : null;
  } catch {
    return null;
  }
}

function storeOpen(open: boolean) {
  try {
    window.localStorage.setItem(OPEN_KEY, open ? "open" : "closed");
  } catch {
    // Private windows and blocked storage just forget the choice.
  }
}

/**
 * The agent's latest task list (`latestTodoBlock`) under the Session Controls
 * panel: a count, and the items when it is open. Open unless every item is done
 * or it was closed here before; the header opens and closes it. A filled
 * surface like the panel, no line borders.
 */
export function TodoCard({ block, className }: { block: string; className?: string }) {
  const items = useMemo(() => todoItems(block), [block]);
  const [choice, setChoice] = useState<boolean | null>(storedOpen);
  const listId = useId();
  if (!items.length) return null;

  const done = items.filter((item) => item.done).length;
  const next = items.findIndex((item) => !item.done);
  const open = choice ?? done < items.length;
  const toggle = () => {
    setChoice(!open);
    storeOpen(!open);
  };

  return (
    <section
      data-testid="session-todo"
      aria-label="Todo"
      className={cn("bg-sidebar text-sidebar-foreground flex w-full shrink-0 flex-col rounded-xl p-3 text-xs shadow-lg", className)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={toggle}
        className="focus-visible:ring-ring/50 -m-1 flex items-center gap-2 rounded-md p-1 text-left outline-none focus-visible:ring-2"
      >
        <ChevronRight aria-hidden className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
        <span className="font-medium">Todo</span>
        <span className="text-muted-foreground ml-auto tabular-nums">
          {done} of {items.length}
        </span>
      </button>
      {open && (
        <ul id={listId} className="mt-2 flex max-h-72 min-w-0 flex-col gap-1.5 overflow-y-auto">
          {items.map((item, index) => (
            <li key={index} data-testid="session-todo-item" data-done={item.done} className="flex items-start gap-2">
              <span
                role="img"
                aria-label={item.done ? "Done" : "Open"}
                className={cn(
                  "mt-px flex size-3.5 shrink-0 items-center justify-center rounded-[4px]",
                  item.done ? "bg-foreground text-background" : "bg-muted",
                )}
              >
                {item.done && <Check aria-hidden className="size-2.5" strokeWidth={3} />}
              </span>
              <span
                data-todo-text
                className={cn("min-w-0 break-words", item.done ? "text-muted-foreground line-through" : index !== next && "text-muted-foreground")}
              >
                {item.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
