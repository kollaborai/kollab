import { useEffect, useState } from "react";
import { ArrowLeft, Loader2, Rocket, RotateCcw } from "lucide-react";
import type { EngineApi, Profile, Team, TeamLaunchResult } from "@/api";
import { Avatar } from "@/components/agents/Avatar";
import { effectiveTeam, launchLines, teamRows } from "@/components/agents/team-model";
import type { TeamSelectionPreference } from "@/components/agents/team-preferences";
import { bundleName, titleCase } from "@/components/panels/panel-model";
import { WorkspacePicker } from "@/components/shell/WorkspacePicker";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

const TEAM_DEFAULT_PROFILE = "__team_member_profiles__";

type LaunchFeedback = { lines: ReturnType<typeof launchLines>; error: string };

export function TeamLaunchSheet({
  api,
  team,
  folder,
  onFolderChange,
  profile,
  profiles,
  preference,
  onPreferenceChange,
  onResetSelection,
  persistenceWarning,
  teamLookupLoading,
  teamLookupError,
  onBack,
  onLaunched,
}: {
  api: EngineApi;
  team: Team;
  folder: string;
  onFolderChange: (folder: string) => void;
  profile: string;
  profiles: Profile[];
  preference: TeamSelectionPreference;
  onPreferenceChange: (preference: TeamSelectionPreference) => void;
  onResetSelection: () => void;
  persistenceWarning: string;
  teamLookupLoading: boolean;
  teamLookupError: string;
  onBack: () => void;
  onLaunched: () => void;
}) {
  const [selectedProfile, setSelectedProfile] = useState(profile);
  const [mission, setMission] = useState("");
  const [launching, setLaunching] = useState(false);
  const [feedback, setFeedback] = useState<LaunchFeedback | null>(null);
  useEffect(() => setSelectedProfile(profile), [profile]);
  useEffect(() => setFeedback(null), [folder, team.name]);
  const teamNames = team.members.map((member) => member.persona.name);
  const selectedSet = new Set(preference.members.filter((name) => teamNames.includes(name)));
  const selectedMembers = teamNames.filter((name) => selectedSet.has(name));
  const validLead = selectedSet.has(preference.lead);
  const preview = effectiveTeam(team, selectedMembers, preference.lead);
  const nameOf = (name: string) =>
    team.members.find((member) => member.persona.name === name)?.persona.display_name || name;
  const excluded = team.members.filter((member) => !selectedSet.has(member.persona.name));

  const updateMembers = (name: string, checked: boolean) => {
    const next = new Set(selectedSet);
    if (checked) next.add(name);
    else next.delete(name);
    onPreferenceChange({
      ...preference,
      members: teamNames.filter((memberName) => next.has(memberName)),
    });
    setFeedback(null);
  };

  const launch = async () => {
    if (!preview || launching || teamLookupLoading || teamLookupError) return;
    setLaunching(true);
    setFeedback(null);
    try {
      // The empty folder is the engine's default; the launch endpoint expects its path.
      const target = folder || (await api.listWorkspaces()).home.path;
      const result: TeamLaunchResult = await api.launchTeam(team.name, target, selectedProfile, {
        members: selectedMembers,
        lead: preference.lead,
        mission: mission.trim(),
      });
      setFeedback({ lines: launchLines(result, nameOf), error: "" });
      onLaunched();
    } catch (reason) {
      setFeedback({ lines: [], error: reason instanceof Error ? reason.message : String(reason) });
    } finally {
      setLaunching(false);
    }
  };

  return (
    <>
      <div className="flex shrink-0 items-center gap-2 px-5 pt-4">
        <Button type="button" variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-4" />
          Teams
        </Button>
        <span className="text-muted-foreground ml-auto text-xs">{team.members.length} members</span>
      </div>
      <DialogHeader className="px-5 pt-2 pb-3 text-left">
        <DialogTitle>Launch {titleCase(team.name)}</DialogTitle>
        <DialogDescription>
          Choose where and how this team starts. The preview shows the reporting tree
          after omitted members are removed.
        </DialogDescription>
        {teamLookupLoading ? (
          <p role="status" className="text-muted-foreground text-xs">Checking this team's configuration in the selected folder…</p>
        ) : null}
        {teamLookupError ? (
          <p role="alert" className="text-destructive text-xs">{teamLookupError}</p>
        ) : null}
      </DialogHeader>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 pb-4">
        <div className="grid grid-cols-[4.75rem_minmax(0,1fr)] items-center gap-x-3 gap-y-2.5">
          <Label htmlFor="team-launch-folder" className="text-muted-foreground text-xs font-normal">
            Folder
          </Label>
          <WorkspacePicker id="team-launch-folder" api={api} value={folder} onChange={onFolderChange} />
          <Label htmlFor="team-launch-profile" className="text-muted-foreground text-xs font-normal">
            Profile
          </Label>
          <Select
            disabled={launching || teamLookupLoading || Boolean(teamLookupError)}
            value={selectedProfile || TEAM_DEFAULT_PROFILE}
            onValueChange={(value) => setSelectedProfile(value === TEAM_DEFAULT_PROFILE ? "" : value)}
          >
            <SelectTrigger id="team-launch-profile" size="sm" className="w-full">
              <SelectValue placeholder="Member defaults" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={TEAM_DEFAULT_PROFILE}>Use each member's profile</SelectItem>
              {selectedProfile && !profiles.some((entry) => entry.name === selectedProfile) ? (
                <SelectItem value={selectedProfile}>{selectedProfile} · Current</SelectItem>
              ) : null}
              {profiles.map((entry) => (
                <SelectItem key={entry.name} value={entry.name}>
                  {entry.model || entry.name}
                  {entry.model && entry.name !== entry.model ? ` · ${entry.name}` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <section aria-labelledby="team-launch-members-heading">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 id="team-launch-members-heading" className="text-sm font-semibold">Members</h3>
              <p className="text-muted-foreground mt-0.5 text-xs">
                Unselected managers are bypassed so retained reports stay connected.
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={launching || teamLookupLoading || Boolean(teamLookupError)}
              onClick={onResetSelection}
            >
              <RotateCcw className="size-3.5" />
              Reset
            </Button>
          </div>
          {persistenceWarning ? (
            <p role="status" className="text-muted-foreground mt-2 text-xs">{persistenceWarning}</p>
          ) : null}
          <fieldset className="mt-2 grid gap-1.5">
            <legend className="sr-only">Choose team members</legend>
            {team.members.map((member) => {
              const name = member.persona.name;
              const checked = selectedSet.has(name);
              return (
                <label
                  key={name}
                  className="hover:bg-accent/50 flex min-w-0 cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={launching || teamLookupLoading || Boolean(teamLookupError)}
                    onChange={(event) => updateMembers(name, event.target.checked)}
                    aria-label={`Include ${member.persona.display_name}`}
                    className="accent-primary size-4 shrink-0"
                  />
                  <Avatar
                    gem={name}
                    avatar={member.persona.avatar}
                    displayName={member.persona.display_name}
                    folder={folder}
                    caste={member.persona.caste}
                    color={member.persona.color}
                    state="idle"
                    live
                    season="auto"
                    size={30}
                  />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-sm font-medium">{member.persona.display_name}</span>
                    <span className="text-muted-foreground truncate text-xs">
                      {member.role || titleCase(member.persona.caste)}
                      {member.reports_to ? ` · Reports to ${titleCase(member.reports_to)}` : ""}
                    </span>
                  </span>
                  <span className="text-muted-foreground hidden shrink-0 text-xs sm:inline">
                    {bundleName(member.agent || member.persona.agent)}
                  </span>
                </label>
              );
            })}
          </fieldset>
        </section>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="team-launch-lead" className="text-sm font-semibold">Team lead</Label>
            <Select
              disabled={launching || teamLookupLoading || Boolean(teamLookupError)}
              value={validLead ? preference.lead : ""}
              onValueChange={(lead) => {
                onPreferenceChange({ ...preference, lead });
                setFeedback(null);
              }}
            >
              <SelectTrigger id="team-launch-lead" className="w-full">
                <SelectValue placeholder="Choose a replacement lead" />
              </SelectTrigger>
              <SelectContent>
                {team.members.filter((member) => selectedSet.has(member.persona.name)).map((member) => (
                  <SelectItem key={member.persona.name} value={member.persona.name}>
                    {member.persona.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {!validLead ? (
              <p role="alert" className="text-destructive text-xs">
                Choose a selected member as lead before launching.
              </p>
            ) : null}
            {!selectedMembers.length ? (
              <p role="alert" className="text-destructive text-xs">Select at least one member.</p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="team-launch-mission" className="text-sm font-semibold">Mission</Label>
            <textarea
              id="team-launch-mission"
              className="border-input placeholder:text-muted-foreground focus-visible:ring-ring min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 disabled:cursor-not-allowed disabled:opacity-50"
              value={mission}
              disabled={launching || teamLookupLoading || Boolean(teamLookupError)}
              onChange={(event) => setMission(event.target.value)}
              placeholder="Optional task or mission for the team lead"
              rows={3}
            />
          </div>
        </div>

        <section aria-labelledby="team-launch-preview-heading" className="bg-muted/40 rounded-xl p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 id="team-launch-preview-heading" className="text-sm font-semibold">Effective reporting tree</h3>
            <span className="text-muted-foreground text-xs">
              {selectedMembers.length} selected · {excluded.length} not selected
            </span>
          </div>
          {preview ? (
            <ul className="mt-2 flex flex-col">
              {teamRows(preview).map((row) => (
                <li
                  key={row.member.persona.name}
                  style={row.depth ? { paddingLeft: `${row.depth * 1.25}rem` } : undefined}
                  className="flex min-w-0 items-center gap-2 py-1"
                >
                  <span className="truncate text-xs font-medium">
                    {row.member.persona.display_name}
                    {row.lead ? <span className="text-muted-foreground font-normal"> · Lead</span> : null}
                  </span>
                  <span className="text-muted-foreground min-w-0 truncate text-xs">
                    {row.member.role || bundleName(row.member.agent)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground mt-2 text-xs">Select members and choose a selected lead to preview.</p>
          )}
          {excluded.length ? (
            <p className="text-muted-foreground mt-2 text-xs">
              Not selected: {excluded.map((member) => member.persona.display_name).join(", ")}
            </p>
          ) : null}
        </section>

        {feedback ? (
          <div role={feedback.error ? "alert" : "status"} className="space-y-1 text-xs">
            {feedback.error ? <p className="text-destructive break-words">{feedback.error}</p> : null}
            {feedback.lines.map((line) => (
              <p key={line.label} className={cn("break-words", line.failed && "text-destructive")}>
                <span className="font-medium">{line.label}</span>
                <span className="text-muted-foreground"> · {line.text}</span>
              </p>
            ))}
            {!feedback.error && !feedback.lines.length ? (
              <p className="text-muted-foreground">Nothing to start; the selected members are already running.</p>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="bg-foreground/[0.04] flex shrink-0 items-center justify-between gap-2 px-5 py-3">
        <span className="text-muted-foreground text-xs">
          {selectedMembers.length} of {team.members.length} members selected
        </span>
        <Button
          type="button"
          disabled={!preview || launching || teamLookupLoading || Boolean(teamLookupError)}
          onClick={() => void launch()}
        >
          {launching ? <Loader2 className="size-4 animate-spin" /> : <Rocket className="size-4" />}
          Launch Team
        </Button>
      </div>
    </>
  );
}
