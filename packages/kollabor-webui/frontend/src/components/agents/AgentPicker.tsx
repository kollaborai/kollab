import { Fragment, useMemo } from "react";
import { Avatar } from "@/components/agents/Avatar";
import type { LaunchChoice } from "@/components/agents/launch-choices";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { lastRanLabel } from "@/last-ran";
import { agentName } from "@/utils/session-display";

/** What a choice is called for people: a persona's display name, else its name in Title Case. */
export const choiceLabel = (choice: Pick<LaunchChoice, "name" | "display_name">) =>
  agentName(choice.name, choice.display_name);

function Face({ choice, size, folder }: { choice: LaunchChoice; size: number; folder: string }) {
  return (
    <Avatar
      gem={choice.name}
      avatar={choice.avatar}
      displayName={choice.display_name}
      folder={folder}
      caste={choice.caste}
      color={choice.color}
      state="idle"
      live
      season="auto"
      size={size}
    />
  );
}

/**
 * New Session's agent: every persona the folder can start, grouped by roster, each with its
 * picture, name and job title. The ones already running in the folder stay listed, disabled.
 * A persona of a roster the folder does not draw from is picked as `roster/name`.
 */
export function AgentPicker({
  id,
  choices,
  value,
  onChange,
  folder,
}: {
  id: string;
  choices: readonly LaunchChoice[];
  /** The `ref` of the pick; empty is "the next free one". */
  value: string;
  onChange: (ref: string) => void;
  folder: string;
}) {
  const picked = choices.find((choice) => choice.ref === value);
  // Rosters in the order they first appear; free agents before the ones the folder already runs.
  const groups = useMemo(() => {
    const byRoster = new Map<string, LaunchChoice[]>();
    for (const choice of choices) byRoster.set(choice.rosterLabel, [...(byRoster.get(choice.rosterLabel) ?? []), choice]);
    return [...byRoster].map(([label, rows]) => ({
      label,
      rows: [...rows].sort((a, b) => Number(Boolean(a.active)) - Number(Boolean(b.active))),
    }));
  }, [choices]);

  const item = (choice: LaunchChoice) => (
    <SelectItem key={choice.ref} value={choice.ref} disabled={choice.active}>
      <Face choice={choice} size={28} folder={folder} />
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="truncate">
          {choiceLabel(choice)}
          {choice.active ? " · In Use" : ""}
          {!choice.active && choice.last_ran ? (
            <span className="text-muted-foreground"> · {lastRanLabel(choice.last_ran)}</span>
          ) : null}
        </span>
        {choice.role ? <span className="text-muted-foreground truncate text-[0.6875rem]">{choice.role}</span> : null}
      </span>
    </SelectItem>
  );

  return (
    <Select value={value} onValueChange={onChange} disabled={!choices.length}>
      <SelectTrigger id={id} size="sm" className="w-full">
        {/* The name only: the list's "Ran 3d Ago" tag and job title would crowd the field. */}
        <SelectValue placeholder="Next Free Agent">
          {picked ? (
            <>
              <Face choice={picked} size={20} folder={folder} />
              <span className="truncate">{choiceLabel(picked)}</span>
            </>
          ) : undefined}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {groups.map((group) =>
          group.label ? (
            <SelectGroup key={group.label}>
              <SelectLabel>{group.label}</SelectLabel>
              {group.rows.map(item)}
            </SelectGroup>
          ) : (
            <Fragment key="ungrouped">{group.rows.map(item)}</Fragment>
          ),
        )}
      </SelectContent>
    </Select>
  );
}
