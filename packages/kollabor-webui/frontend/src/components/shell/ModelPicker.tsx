import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import type { EngineApi, ModelChoice, Profile } from "@/api";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { matchesChoice, sameChoice } from "@/components/shell/model-choice";
import { cn } from "@/lib/utils";

/** "gpt-6-luna · max · codex-cli": the parts after the first in muted text. */
function ChoiceLabel({ parts }: { parts: string[] }) {
  const [first, ...rest] = parts.filter(Boolean);
  return (
    <span className="min-w-0 truncate">
      {first}
      {rest.length ? <span className="text-muted-foreground"> · {rest.join(" · ")}</span> : null}
    </span>
  );
}

/**
 * Pick a provider profile, model and effort in one searchable list: "codex luna max" finds Codex
 * CLI's gpt-6-luna at max. Lists every model of every set-up profile at each effort its provider
 * takes; an ACP harness offers what its sessions last reported.
 */
export function ModelPicker({
  id,
  api,
  profiles,
  value,
  onPick,
  disabled,
  className,
}: {
  id?: string;
  api: EngineApi;
  /** Shown until the engine's list arrives, or when it can't send one. */
  profiles: readonly Profile[];
  value: Pick<ModelChoice, "profile" | "model" | "effort">;
  onPick: (choice: ModelChoice) => void;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [loaded, setLoaded] = useState<ModelChoice[] | null>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    api.listModelChoices().then(
      (result) => live && setLoaded(result.choices),
      () => undefined, // an engine without the list keeps the profile rows
    );
    return () => {
      live = false;
    };
  }, [open, api]);

  const choices = useMemo<ModelChoice[]>(
    () =>
      loaded ??
      profiles.map((profile) => ({
        profile: profile.name,
        provider: profile.provider || "",
        model: profile.model || "",
        effort: "",
      })),
    [loaded, profiles],
  );
  const shown = useMemo(() => choices.filter((choice) => matchesChoice(choice, query.trim())), [choices, query]);
  const groups = useMemo(() => {
    const byGroup = new Map<string, { choice: ModelChoice; index: number }[]>();
    shown.forEach((choice, index) => {
      const label = choice.profile;
      byGroup.set(label, [...(byGroup.get(label) ?? []), { choice, index }]);
    });
    return [...byGroup];
  }, [shown]);

  const pick = (choice: ModelChoice | undefined) => {
    if (!choice) return;
    setOpen(false);
    onPick(choice);
  };
  const move = (step: number) => {
    const next = Math.min(Math.max(active + step, 0), shown.length - 1);
    setActive(next);
    list.current?.querySelector(`[data-index="${next}"]`)?.scrollIntoView({ block: "nearest" });
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      pick(shown[active]);
    }
  };
  const current = profiles.find((profile) => profile.name === value.profile);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        setQuery("");
        setActive(0);
      }}
    >
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          disabled={disabled}
          aria-label="Model"
          className={cn(
            "flex h-8 w-full min-w-0 items-center justify-between gap-2 rounded-md border bg-muted/50 px-3 py-2 text-sm whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50",
            className,
          )}
        >
          <ChoiceLabel
            parts={[value.model || current?.model || value.profile, value.effort, value.model || current?.model ? value.profile : ""]}
          />
          <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        collisionPadding={12}
        className="w-(--radix-popover-trigger-width) max-w-[calc(100vw-1.5rem)] min-w-72 p-0"
      >
        <Input
          autoFocus
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Search: codex luna max"
          aria-label="Search models"
          className="h-9 rounded-none border-0 border-b bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <div ref={list} role="listbox" aria-label="Models" className="max-h-72 overflow-y-auto p-1">
          {groups.length ? (
            groups.map(([label, rows]) => (
              <div key={label} role="group" aria-label={label}>
                <p className="text-muted-foreground px-2 py-1.5 text-xs">{label}</p>
                {rows.map(({ choice, index }) => (
                  <button
                    key={`${choice.profile}\u0000${choice.model}\u0000${choice.effort}`}
                    type="button"
                    role="option"
                    data-index={index}
                    aria-selected={index === active}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => pick(choice)}
                    className={cn(
                      "relative flex w-full items-center rounded-sm py-1.5 pr-8 pl-2 text-left text-sm outline-hidden select-none",
                      index === active && "bg-accent text-accent-foreground",
                    )}
                  >
                    <ChoiceLabel parts={[choice.model || "Default Model", choice.effort]} />
                    {sameChoice(choice, value) ? <CheckIcon className="absolute right-2 size-4" /> : null}
                  </button>
                ))}
              </div>
            ))
          ) : (
            <p className="text-muted-foreground px-2 py-1.5 text-sm">No model matches.</p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
