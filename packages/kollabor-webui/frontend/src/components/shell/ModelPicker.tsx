import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import type { EngineApi, ModelChoice, Profile } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { optionRowCheckClassName, optionRowClassName } from "@/components/ui/select";
import { choiceLabel, matchesChoice, sameChoice } from "@/components/shell/model-choice";
import { cn } from "@/lib/utils";

/**
 * "gpt-6-luna · max · codex-cli": the parts after the first in muted text. The level never shrinks: a
 * narrow label drops the profile first, then cuts the model short. The whole label shows on hover.
 */
function ChoiceLabel({ parts }: { parts: string[] }) {
  const [first, level, ...rest] = parts;
  const profile = rest.filter(Boolean).join(" · ");
  return (
    <span className="flex min-w-0 flex-1 overflow-hidden text-left" title={parts.filter(Boolean).join(" · ")}>
      <span data-part="model" className="truncate">{first}</span>
      {level ? <span data-part="level" className="text-muted-foreground shrink-0 whitespace-pre"> · {level}</span> : null}
      {profile ? (
        // basis 0: the profile gets only the room the model and level leave, so it is the first to go
        <span data-part="profile" className="text-muted-foreground min-w-0 flex-1 basis-0 overflow-hidden text-ellipsis whitespace-pre"> · {profile}</span>
      ) : null}
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
  folder,
}: {
  id?: string;
  api: EngineApi;
  /** Shown until the engine's list arrives, or when it can't send one. */
  profiles: readonly Profile[];
  value: Pick<ModelChoice, "profile" | "model" | "effort">;
  onPick: (choice: ModelChoice) => void;
  disabled?: boolean;
  className?: string;
  /** The workspace the picker lists for; empty is the engine's own folder. */
  folder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [loaded, setLoaded] = useState<ModelChoice[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [incomplete, setIncomplete] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState<string[]>([]);
  const [attempt, setAttempt] = useState(0); // Retry asks again
  const list = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let batches = 0;
    let pending: string[] = [];
    const load = async () => {
      try {
        const result = await api.listModelChoices(folder?.trim() || "");
        if (!live) return;
        setLoaded(result.choices);
        setFailed(false);
        const statuses = Object.entries(result.discovery ?? {});
        const advancing = statuses.filter(([, info]) => info.more).map(([name]) => name);
        const stalled = statuses.filter(([, info]) => info.status !== "ready" && !info.more).map(([name]) => name);
        batches += 1;
        pending = advancing;
        if (advancing.length && batches < 16) {
          setDiscovering(advancing);
          setIncomplete(stalled);
          timer = setTimeout(load, 250);
        } else {
          setDiscovering([]);
          setIncomplete([...stalled, ...advancing]);
        }
      } catch {
        if (live) {
          setDiscovering([]);
          setIncomplete((current) => [...new Set([...current, ...pending])]);
          setFailed(true);
        }
      }
    };
    void load();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
      setLoaded(null); // each opening asks again: a list fetched as an earlier login never shows
      setFailed(false);
      setIncomplete([]);
      setDiscovering([]);
    };
  }, [open, api, attempt, folder]);

  const choices = useMemo<ModelChoice[]>(
    () =>
      loaded ??
      profiles.map((profile) => ({
        profile: profile.name,
        provider: profile.provider || "",
        model: profile.provider === "acp" ? "" : profile.model || "",
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
        <Button
          id={id}
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          aria-label="Model"
          className={cn("w-full min-w-0 shrink justify-between font-normal", className)}
        >
          <ChoiceLabel parts={choiceLabel(value, current)} />
          <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        collisionPadding={12}
        className="w-(--radix-popover-trigger-width) max-w-[calc(100vw-1.5rem)] min-w-72 p-0"
      >
        <Input
          ref={search}
          autoFocus
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Search: codex luna max"
          aria-label="Search models"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={shown[active] ? `${listId}-${active}` : undefined}
          className="h-9 rounded-none border-0 border-b bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        {loaded === null ? (
          <p role="status" className="text-muted-foreground flex min-h-8 items-center gap-2 px-3 text-xs">
            {failed ? "The full model list did not load." : "Loading models…"}
            {failed ? (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                onClick={() => {
                  setAttempt((count) => count + 1);
                  search.current?.focus();
                }}
              >
                Retry
              </Button>
            ) : null}
          </p>
        ) : null}
        {loaded !== null && discovering.length > 0 ? (
          <p role="status" className="text-muted-foreground min-h-8 px-3 py-2 text-xs">
            Loading more model options: {discovering.join(", ")}…
          </p>
        ) : null}
        {loaded !== null && incomplete.length > 0 ? (
          <p role="status" className="text-muted-foreground flex min-h-8 items-center gap-2 px-3 text-xs">
            Some model options did not load: {incomplete.join(", ")}.
            <Button type="button" variant="ghost" size="xs" onClick={() => {
              setAttempt((count) => count + 1);
              search.current?.focus();
            }}>
              Retry
            </Button>
          </p>
        ) : null}
        <div ref={list} id={listId} role="listbox" aria-label="Models" className="max-h-72 overflow-y-auto p-1">
          {groups.length ? (
            groups.map(([label, rows]) => (
              <div key={label} role="group" aria-label={label}>
                <p className="text-muted-foreground px-2 py-1.5 text-xs">{label}</p>
                {rows.map(({ choice, index }) => (
                  <button
                    key={`${choice.profile}\u0000${choice.model}\u0000${choice.effort}`}
                    id={`${listId}-${index}`}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    data-index={index}
                    aria-selected={index === active}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => pick(choice)}
                    className={cn(optionRowClassName, "text-left", index === active && "bg-accent text-accent-foreground")}
                  >
                    <ChoiceLabel parts={[choice.model || "Default Model", choice.effort]} />
                    {sameChoice(choice, value) ? (
                      <>
                        <span className={optionRowCheckClassName}>
                          <CheckIcon className="size-4" />
                        </span>
                        <span className="sr-only">, current</span>
                      </>
                    ) : null}
                  </button>
                ))}
              </div>
            ))
          ) : loaded !== null ? (
            <p className="text-muted-foreground px-2 py-1.5 text-sm">No model matches.</p>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
