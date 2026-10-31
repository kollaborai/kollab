import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ChevronDown, Loader2, Rocket } from "lucide-react";
import {
  errorText,
  type EngineApi,
  type LibraryProblem,
  type Persona,
  type Roster,
  type RostersResponse,
  type Team,
  type TeamLaunchResult,
  type TeamMember,
} from "@/api";
import { Avatar } from "@/components/agents/Avatar";
import { launchLines, teamRows } from "@/components/agents/team-model";
import { bundleName, titleCase } from "@/components/panels/panel-model";
import { WorkspacePicker } from "@/components/shell/WorkspacePicker";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

const SOURCES = { bundled: "Bundled", user: "User", project: "Project" } as const;

function Tab({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "border-b-2 px-1 pb-1.5 text-sm transition-colors",
        active ? "border-highlight text-foreground font-medium" : "text-muted-foreground hover:text-foreground border-transparent",
      )}
    >
      {children}
    </button>
  );
}

/** A persona or team seat: its picture and name, and under it, when opened, what it is told. */
function PersonaRow({
  persona,
  folder,
  indent = 0,
  lead = false,
  seat,
  size = 40,
}: {
  persona: Persona;
  folder: string;
  indent?: number;
  lead?: boolean;
  seat?: TeamMember;
  size?: number;
}) {
  const [open, setOpen] = useState(false);
  // A role that only repeats the name (an inline team seat) adds nothing under it.
  const named = seat?.role || persona.role;
  const role = named === persona.display_name ? "" : named;
  const bundle = bundleName(seat?.agent || persona.agent);
  const prompt = persona.prompt.trim();
  return (
    <li style={indent ? { paddingLeft: `${indent * 1.5}rem` } : undefined}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="hover:bg-accent/50 flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left transition-colors"
      >
        <Avatar
          gem={persona.name}
          avatar={persona.avatar}
          displayName={persona.display_name}
          folder={folder}
          caste={persona.caste}
          color={persona.color}
          state="idle"
          live
          season="auto"
          size={size}
        />
        <span className="flex min-w-0 flex-1 flex-col leading-tight">
          <span className="truncate text-sm font-medium">
            {persona.display_name}
            {lead ? <span className="text-muted-foreground text-xs font-normal"> · Lead</span> : null}
          </span>
          <span className="text-muted-foreground truncate text-xs">{role || persona.personality || titleCase(persona.caste)}</span>
        </span>
        <span className="text-muted-foreground hidden shrink-0 text-xs sm:inline">{bundle}</span>
        <ChevronDown className={cn("text-muted-foreground size-4 shrink-0 transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <dl className="bg-muted/40 mx-2 mt-1 mb-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 rounded-lg p-3 text-xs">
          <dt className="text-muted-foreground">Handle</dt>
          <dd className="break-words">@{persona.name}</dd>
          <dt className="text-muted-foreground">Type</dt>
          <dd>{bundle}</dd>
          <dt className="text-muted-foreground">Model Profile</dt>
          <dd>{seat?.profile || persona.profile || "Default"}</dd>
          <dt className="text-muted-foreground">Skills</dt>
          <dd>{persona.skills.length ? persona.skills.join(", ") : "None"}</dd>
          {persona.personality ? (
            <>
              <dt className="text-muted-foreground">Personality</dt>
              <dd>{persona.personality}</dd>
            </>
          ) : null}
          {seat?.reports_to ? (
            <>
              <dt className="text-muted-foreground">Reports To</dt>
              <dd>{titleCase(seat.reports_to)}</dd>
            </>
          ) : null}
          {seat?.prompt ? (
            <>
              <dt className="text-muted-foreground">Seat</dt>
              <dd className="break-words whitespace-pre-wrap">{seat.prompt}</dd>
            </>
          ) : null}
          <dt className="text-muted-foreground">Prompt</dt>
          <dd className="max-h-40 overflow-y-auto break-words whitespace-pre-wrap">{prompt || "None"}</dd>
        </dl>
      ) : null}
    </li>
  );
}

function RosterSection({ roster, folderRoster, folder }: { roster: Roster; folderRoster: string; folder: string }) {
  return (
    <section aria-label={roster.display_name || roster.name}>
      <h3 className="flex flex-wrap items-baseline gap-x-2 px-2 text-sm font-semibold">
        {roster.display_name || titleCase(roster.name)}
        <span className="text-muted-foreground text-xs font-normal">
          {SOURCES[roster.source]} · {roster.personas.length} Agents{roster.name === folderRoster ? " · Folder Default" : ""}
        </span>
      </h3>
      {roster.description ? <p className="text-muted-foreground mt-0.5 px-2 text-xs">{roster.description}</p> : null}
      <ul className="mt-1.5 flex flex-col">
        {roster.personas.map((persona) => (
          <PersonaRow key={persona.name} persona={persona} folder={folder} />
        ))}
      </ul>
    </section>
  );
}

function TeamSection({
  team,
  folder,
  launching,
  result,
  onLaunch,
}: {
  team: Team;
  folder: string;
  launching: boolean;
  result: { lines: ReturnType<typeof launchLines>; error: string } | null;
  onLaunch: () => void;
}) {
  return (
    <section aria-label={titleCase(team.name)}>
      <div className="flex items-start justify-between gap-3 px-2">
        <div className="min-w-0">
          <h3 className="flex flex-wrap items-baseline gap-x-2 text-sm font-semibold">
            {titleCase(team.name)}
            <span className="text-muted-foreground text-xs font-normal">
              {SOURCES[team.source]} · {team.members.length} Agents
            </span>
          </h3>
          {team.description ? <p className="text-muted-foreground mt-0.5 text-xs">{team.description}</p> : null}
        </div>
        <Button type="button" size="sm" variant="outline" disabled={launching} onClick={onLaunch} className="shrink-0">
          {launching ? <Loader2 className="size-4 animate-spin" /> : <Rocket className="size-4" />}
          Launch Team
        </Button>
      </div>
      {result ? (
        <div role="status" className="mx-2 mt-2 flex flex-col gap-0.5 text-xs">
          {result.error ? <p className="text-destructive break-words">{result.error}</p> : null}
          {result.lines.length ? (
            result.lines.map((line) => (
              <p key={line.label} className={cn("break-words", line.failed && "text-destructive")}>
                <span className="font-medium">{line.label}</span>
                <span className="text-muted-foreground"> · {line.text}</span>
              </p>
            ))
          ) : result.error ? null : (
            <p className="text-muted-foreground">Nothing to start.</p>
          )}
        </div>
      ) : null}
      <ul className="mt-1.5 flex flex-col">
        {teamRows(team).map((row) => (
          <PersonaRow
            key={row.member.persona.name}
            persona={row.member.persona}
            folder={folder}
            indent={row.depth}
            lead={row.lead}
            seat={row.member}
            size={32}
          />
        ))}
      </ul>
    </section>
  );
}

function Problems({ problems }: { problems: LibraryProblem[] }) {
  if (!problems.length) return null;
  // One block per file: a file that fails more than one check reads once.
  const byFile = new Map<string, string[]>();
  for (const problem of problems) byFile.set(problem.file, [...(byFile.get(problem.file) ?? []), problem.message]);
  return (
    <section aria-label="Problems" className="bg-destructive/10 mx-2 rounded-lg p-3">
      <h3 className="text-destructive text-sm font-semibold">
        Problems <span className="font-normal tabular-nums">{byFile.size}</span>
      </h3>
      <p className="text-muted-foreground mt-0.5 text-xs">These files could not be loaded. Everything else is listed below.</p>
      <ul className="mt-2 flex flex-col gap-2 text-xs">
        {[...byFile].map(([file, messages]) => (
          <li key={file}>
            <span className="block font-mono font-medium break-all" title={file}>
              {file.split("/").pop()}
            </span>
            {messages.map((message) => (
              <span key={message} className="text-destructive block break-words">
                {message}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </section>
  );
}

function LibraryBody({
  api,
  workspace,
  profile,
  onLaunched,
}: {
  api: EngineApi;
  workspace: string;
  profile: string;
  onLaunched: () => void;
}) {
  const [folder, setFolder] = useState(workspace);
  const [rosters, setRosters] = useState<RostersResponse | null>(null);
  const [teams, setTeams] = useState<Team[] | null>(null);
  const [problems, setProblems] = useState<LibraryProblem[]>([]);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"rosters" | "teams">("rosters");
  const [launching, setLaunching] = useState("");
  const [results, setResults] = useState<Record<string, { lines: ReturnType<typeof launchLines>; error: string }>>({});

  useEffect(() => {
    let stale = false;
    setError("");
    Promise.all([api.listRosters(folder), api.listTeams(folder)]).then(
      ([rosterList, teamList]) => {
        if (stale) return;
        setRosters(rosterList);
        setTeams(teamList.teams);
        // Both lists carry the library's problems: one line each.
        const seen = new Map<string, LibraryProblem>();
        for (const problem of [...rosterList.problems, ...teamList.problems]) seen.set(`${problem.file}:${problem.message}`, problem);
        setProblems([...seen.values()]);
      },
      (reason) => {
        if (!stale) setError(errorText(reason));
      },
    );
    return () => {
      stale = true;
    };
  }, [api, folder]);

  const launch = useCallback(
    async (team: Team) => {
      setLaunching(team.name);
      const nameOf = (persona: string) => team.members.find((member) => member.persona.name === persona)?.persona.display_name || persona;
      try {
        // An empty folder is the engine's own; the launch wants its path.
        const target = folder || (await api.listWorkspaces()).home.path;
        const done: TeamLaunchResult = await api.launchTeam(team.name, target, profile);
        setResults((current) => ({ ...current, [team.name]: { lines: launchLines(done, nameOf), error: "" } }));
        onLaunched();
      } catch (reason) {
        setResults((current) => ({ ...current, [team.name]: { lines: [], error: errorText(reason) } }));
      } finally {
        setLaunching("");
      }
    },
    [api, folder, profile, onLaunched],
  );

  return (
    <>
      <DialogHeader className="px-5 pt-4 pb-3 text-left">
        <DialogTitle>Agents</DialogTitle>
        <DialogDescription>
          What this folder can start: rosters of agents, and teams that start several together.
        </DialogDescription>
      </DialogHeader>
      <div className="grid grid-cols-[4.75rem_minmax(0,1fr)] items-center gap-x-3 px-5 pb-3 sm:max-w-md">
        <Label htmlFor="agents-folder" className="text-muted-foreground text-xs font-normal">
          Folder
        </Label>
        <WorkspacePicker id="agents-folder" api={api} value={folder} onChange={setFolder} />
      </div>
      <div role="tablist" aria-label="Agents" className="flex gap-4 px-5">
        <Tab active={tab === "rosters"} onClick={() => setTab("rosters")}>
          Rosters{rosters ? <span className="text-muted-foreground tabular-nums"> {rosters.rosters.length}</span> : null}
        </Tab>
        <Tab active={tab === "teams"} onClick={() => setTab("teams")}>
          Teams{teams ? <span className="text-muted-foreground tabular-nums"> {teams.length}</span> : null}
        </Tab>
      </div>
      <div className="bg-border h-px shrink-0" />
      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-3 py-4">
        <Problems problems={problems} />
        {error ? (
          <p role="alert" className="text-destructive px-2 text-sm break-words">
            {error}
          </p>
        ) : !rosters || !teams ? (
          <p className="text-muted-foreground px-2 text-sm">Loading…</p>
        ) : tab === "rosters" ? (
          rosters.rosters.length ? (
            rosters.rosters.map((roster) => (
              <RosterSection key={roster.name} roster={roster} folderRoster={rosters.default} folder={folder} />
            ))
          ) : (
            <p className="text-muted-foreground px-2 text-sm">No rosters found.</p>
          )
        ) : teams.length ? (
          teams.map((team) => (
            <TeamSection
              key={team.name}
              team={team}
              folder={folder}
              launching={launching === team.name}
              result={results[team.name] ?? null}
              onLaunch={() => void launch(team)}
            />
          ))
        ) : (
          <p className="text-muted-foreground px-2 text-sm">No teams found.</p>
        )}
      </div>
    </>
  );
}

/** The agents this folder can start: every roster and team, and a button that starts a team here. */
export function AgentLibrary({
  api,
  open,
  onOpenChange,
  workspace,
  profile,
  onLaunched,
}: {
  api: EngineApi;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The folder New Session has chosen; the view starts there. */
  workspace: string;
  /** The Model New Session has chosen; every member of a team starts on it, as a New Session chat would. */
  profile: string;
  /** A team was started: the sidebar should list its agents now. */
  onLaunched: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[92vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(48rem,calc(100%-2rem))]">
        {/* Mounted per open, so each visit reads the files as they are now. */}
        <LibraryBody api={api} workspace={workspace} profile={profile} onLaunched={onLaunched} />
      </DialogContent>
    </Dialog>
  );
}
