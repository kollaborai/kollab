import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, RefreshCw } from "lucide-react";
import type { EngineApi, Profile, Session, SessionState } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatSessionName } from "@/utils/session-display";
import { titleCase } from "./panel-model";

/**
 * The Session tab: profile / model / effort overrides for this daemon only.
 * Nothing here is written to a profile or to config.
 */
export function SessionSettings({
  api,
  session,
  profiles,
  onSessionUpdated,
}: {
  api: EngineApi;
  session: Session;
  profiles: Profile[];
  onSessionUpdated: (session: Session) => void;
}) {
  const [settings, setSettings] = useState<SessionState | null>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [editProfile, setEditProfile] = useState(
    session.profile || profiles[0]?.name || "default",
  );
  const [editModel, setEditModel] = useState(session.model || "");
  const [editEffort, setEditEffort] = useState(session.effort || "");
  const [applyBusy, setApplyBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );

  const sessionLabel = formatSessionName(session.name, session.session_id);
  const workspace = String(
    settings?.system?.cwd || session.workspace || "current project",
  );

  const loadSettings = useCallback(async () => {
    setSettingsBusy(true);
    try {
      setSettings(await api.getSessionState(session.session_id));
    } catch (error) {
      setMessage({
        ok: false,
        text: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setSettingsBusy(false);
    }
  }, [api, session.session_id]);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  // The Model and Loadouts tabs change the session underneath this form. Follow
  // it, or Apply would put the stale model back as an override.
  useEffect(() => {
    setEditProfile(session.profile || profiles[0]?.name || "default");
    setEditModel(session.model || "");
    setEditEffort(session.effort || "");
  }, [session.profile, session.model, session.effort]); // profiles only seeds the fallback name

  const apply = async () => {
    setApplyBusy(true);
    try {
      const updated = await api.setSessionProfile(
        session.session_id,
        editProfile,
        editModel.trim() || undefined,
        editEffort.trim() || undefined,
      );
      onSessionUpdated(updated);
      setEditModel(updated.model || "");
      setEditEffort(updated.effort || "");
      setMessage({
        ok: true,
        text: `Session settings applied: ${updated.model || editProfile}`,
      });
      await loadSettings();
    } catch (error) {
      setMessage({
        ok: false,
        text: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setApplyBusy(false);
    }
  };

  const reset = () => {
    setEditProfile(session.profile || profiles[0]?.name || "default");
    setEditModel(session.model || "");
    setEditEffort(session.effort || "");
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="grid min-h-0 flex-1 gap-3 overflow-y-auto px-4 py-3 text-sm sm:px-6">
        <p className="text-muted-foreground text-sm">
          Live settings for this daemon. Changes apply to the current session and
          do not rewrite your saved profile.
        </p>
        <div className="grid grid-cols-[6rem_minmax(0,1fr)] items-center gap-2 sm:grid-cols-[7rem_minmax(0,1fr)]">
          <span className="text-muted-foreground">Session</span>
          <span className="font-mono break-all">{sessionLabel}</span>
          <span className="text-muted-foreground">Agent</span>
          <span className="break-words">{session.agent || "default"}</span>
          <span className="text-muted-foreground">Gem</span>
          <span className="break-words">
            {session.identity ? titleCase(session.identity) : "Unassigned"}
          </span>
          <span className="text-muted-foreground">Workspace</span>
          <span className="break-all">{workspace}</span>
        </div>
        <div className="grid gap-2">
          <label
            className="text-muted-foreground text-xs font-medium"
            htmlFor="settings-profile"
          >
            Profile
          </label>
          <Select
            value={editProfile}
            onValueChange={setEditProfile}
            disabled={applyBusy}
          >
            <SelectTrigger
              id="settings-profile"
              className="w-full min-w-0"
              aria-label="Session profile"
            >
              <SelectValue placeholder="default" />
            </SelectTrigger>
            <SelectContent>
              {profiles.length ? (
                profiles.map((profile) => (
                  <SelectItem key={profile.name} value={profile.name}>
                    {profile.name}
                    {profile.model ? ` · ${profile.model}` : ""}
                  </SelectItem>
                ))
              ) : (
                <SelectItem value={editProfile}>{editProfile}</SelectItem>
              )}
            </SelectContent>
          </Select>
          <label
            className="text-muted-foreground text-xs font-medium"
            htmlFor="settings-model"
          >
            Model Override (Optional)
          </label>
          <Input
            id="settings-model"
            value={editModel}
            onChange={(event) => setEditModel(event.target.value)}
            placeholder={session.model || "profile default"}
            disabled={applyBusy}
            className="font-mono text-xs"
          />
          <label
            className="text-muted-foreground text-xs font-medium"
            htmlFor="settings-effort"
          >
            Effort Override (Optional)
          </label>
          <Input
            id="settings-effort"
            value={editEffort}
            onChange={(event) => setEditEffort(event.target.value)}
            placeholder={session.effort || "profile default"}
            disabled={applyBusy}
            className="font-mono text-xs"
          />
        </div>
        <div className="bg-muted/30 rounded-md border p-3 text-xs">
          {settingsBusy ? (
            <span className="text-muted-foreground">
              Refreshing daemon state…
            </span>
          ) : settings ? (
            <div className="grid gap-1.5">
              <div className="flex items-center gap-2 font-medium">
                <CheckCircle2 className="size-3.5 text-emerald-500" />
                Engine connected
              </div>
              <span className="text-muted-foreground break-words">
                PID{" "}
                {String(
                  settings.system?.daemon_pid || session.daemon_pid || "—",
                )}{" "}
                · {String(settings.system?.git_branch || "No git branch")}
              </span>
              <span className="text-muted-foreground break-words">
                Hub{" "}
                {titleCase(
                  String(settings.hub?.my_identity || session.identity || "unassigned"),
                )}{" "}
                ·{" "}
                {settings.processing?.is_processing ? "Working" : "Idle"}
              </span>
              {settings.agent?.description ? (
                <span className="text-muted-foreground break-words">
                  {String(settings.agent.description)}
                </span>
              ) : null}
            </div>
          ) : (
            <span className="text-muted-foreground">
              No live state available.
            </span>
          )}
        </div>
      </div>
      <footer className="flex shrink-0 flex-col gap-2 border-t px-4 py-3 sm:px-6">
        {message ? (
          <p
            role="status"
            className={
              message.ok
                ? "text-xs text-emerald-700 break-words dark:text-emerald-300"
                : "text-destructive text-xs break-words"
            }
          >
            {message.text}
          </p>
        ) : null}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="outline"
            onClick={reset}
            disabled={applyBusy}
          >
            Reset
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => void loadSettings()}
          >
            <RefreshCw className="size-4" />
            Refresh
          </Button>
          <Button
            type="button"
            onClick={() => void apply()}
            disabled={applyBusy}
          >
            {applyBusy ? "Applying…" : "Apply"}
          </Button>
        </div>
      </footer>
    </div>
  );
}
