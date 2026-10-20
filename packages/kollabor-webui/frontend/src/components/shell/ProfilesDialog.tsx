import { useEffect, useState } from "react";
import { Copy, Pencil, Plus, Trash2, Zap } from "lucide-react";

import { EFFORT_LEVELS } from "@/api";
import type { Profile, ProfileUpdate, ProfileWrite } from "@/api";
import {
  profileLoadWarningLines,
  profileTargetLabel,
  shouldSubmitBaseUrlUpdate,
} from "./profile-target";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

type Api = {
  listProfiles: () => Promise<{
    providers?: string[];
    invalid_profiles?: Record<string, string>;
  }>;
  createProfile: (body: ProfileWrite) => Promise<unknown>;
  updateProfile: (name: string, body: ProfileUpdate) => Promise<unknown>;
  deleteProfile: (name: string) => Promise<unknown>;
  testProfile: (
    name: string,
  ) => Promise<{
    success: boolean;
    message?: string;
    error?: string;
    latency_ms?: number;
    warning?: string;
  }>;
};

type ProfileWriteBody = {
  name: string;
  provider: string;
  model: string;
  api_key?: string;
  base_url?: string;
  temperature?: number;
  max_tokens?: number | null;
  description?: string;
  timeout?: number;
  top_p?: number | null;
  effort?: string;
  streaming?: boolean;
  supports_tools?: boolean;
  extra_headers?: Record<string, string>;
  copy_from?: string;
};

type ProfileUpdateBody = Partial<Omit<ProfileWriteBody, "name" | "copy_from">> & {
  new_name?: string;
};

const EMPTY_DRAFT: ProfileWriteBody = {
  name: "",
  provider: "anthropic",
  model: "",
  api_key: "",
  base_url: "",
  temperature: 0.7,
  max_tokens: null,
  description: "",
  timeout: 0,
  top_p: null,
  effort: "",
  streaming: true,
  supports_tools: true,
};

export function ProfilesDialog({
  api,
  profiles,
  open,
  onOpenChange,
  onSaved,
}: {
  api: Api;
  profiles: Profile[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void | Promise<void>;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<ProfileWriteBody>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [providers, setProviders] = useState<string[]>([]);
  const [invalidProfiles, setInvalidProfiles] = useState<Record<string, string>>({});
  const [copyingFrom, setCopyingFrom] = useState<string | null>(null);
  const [originalBaseUrl, setOriginalBaseUrl] = useState("");
  const [baseUrlHasHiddenData, setBaseUrlHasHiddenData] = useState(false);
  const [replaceHiddenBaseUrl, setReplaceHiddenBaseUrl] = useState(false);
  const loadWarnings = profileLoadWarningLines(invalidProfiles);

  // The engine names every provider it can run, so the picker never lags it.
  useEffect(() => {
    if (!open) return;
    let live = true;
    api
      .listProfiles()
      .then((result) => {
        if (!live) return;
        setProviders(result.providers ?? []);
        setInvalidProfiles(result.invalid_profiles ?? {});
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [api, open]);

  // A stored provider the engine no longer lists stays selectable.
  const providerOptions = providers.includes(draft.provider)
    ? providers
    : [...providers, draft.provider];

  const startCreate = () => {
    setEditing(null);
    setCopyingFrom(null);
    setOriginalBaseUrl("");
    setBaseUrlHasHiddenData(false);
    setReplaceHiddenBaseUrl(false);
    setDraft(EMPTY_DRAFT);
    setError(null);
    setTestResult(null);
  };

  // GET /profiles never returns the key, so the form starts with it blank.
  const fromProfile = (p: Profile, name: string): ProfileWriteBody => ({
    ...EMPTY_DRAFT,
    name,
    provider: p.provider || "anthropic",
    model: p.model || "",
    description: p.description || "",
    base_url: p.base_url ?? "",
    temperature: p.temperature ?? EMPTY_DRAFT.temperature,
    effort: p.effort || EMPTY_DRAFT.effort,
    streaming: p.streaming ?? EMPTY_DRAFT.streaming,
    supports_tools: p.supports_tools ?? EMPTY_DRAFT.supports_tools,
  });

  const startEdit = (p: Profile) => {
    setEditing(p.name);
    setCopyingFrom(null);
    setOriginalBaseUrl(p.base_url ?? "");
    setBaseUrlHasHiddenData(Boolean(p.base_url_has_hidden_data));
    setReplaceHiddenBaseUrl(false);
    setDraft(fromProfile(p, p.name));
    setError(null);
    setTestResult(null);
  };

  // Copy from the stored profile so hidden fields remain server-side and are
  // never re-sent from redacted API metadata.
  const startCopy = (p: Profile) => {
    let name = `${p.name}-copy`;
    if (profiles.some((q) => q.name === name)) {
      let n = 2;
      while (profiles.some((q) => q.name === `${p.name}-copy-${n}`)) n++;
      name = `${p.name}-copy-${n}`;
    }
    setEditing(null);
    setCopyingFrom(p.name);
    setOriginalBaseUrl(p.base_url ?? "");
    setBaseUrlHasHiddenData(Boolean(p.base_url_has_hidden_data));
    setReplaceHiddenBaseUrl(false);
    setDraft(fromProfile(p, name));
    setError(null);
    setTestResult(null);
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (copyingFrom) {
        await api.createProfile({
          name: draft.name,
          provider: draft.provider,
          model: draft.model,
          copy_from: copyingFrom,
          effort: draft.effort || "",
        });
      } else if (editing) {
        const body: ProfileUpdateBody = {
          provider: draft.provider,
          model: draft.model,
          description: draft.description,
          temperature: draft.temperature,
          effort: draft.effort || "",
          streaming: draft.streaming,
          supports_tools: draft.supports_tools,
        };
        if (
          shouldSubmitBaseUrlUpdate(
            originalBaseUrl,
            draft.base_url ?? "",
            baseUrlHasHiddenData,
            replaceHiddenBaseUrl,
          )
        ) {
          body.base_url = draft.base_url ?? "";
        }
        if (draft.api_key) body.api_key = draft.api_key;
        if (draft.name && draft.name !== editing) body.new_name = draft.name;
        await api.updateProfile(editing, body);
      } else {
        await api.createProfile(draft);
      }
      await onSaved();
      startCreate();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (name: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteProfile(name);
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const test = async (name: string) => {
    setTestResult(`Testing ${name}…`);
    try {
      const r = await api.testProfile(name);
      setTestResult(
        r.success
          ? `${name}: OK (${r.message ?? "connected"}${
              r.latency_ms ? `, ${Math.round(r.latency_ms)}ms` : ""
            })`
          : `${name}: Failed — ${r.message ?? r.error ?? "unknown error"}`,
      );
    } catch (e) {
      setTestResult(
        `${name}: Failed — ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Profiles</DialogTitle>
          <DialogDescription>
            Create, edit, test, and delete LLM profiles. Changes save to
            ~/.kollab/config.json and apply to new sessions.
          </DialogDescription>
        </DialogHeader>

        {loadWarnings.length > 0 && (
          <div
            role="alert"
            className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
          >
            <p className="font-medium">Some profiles were skipped as invalid.</p>
            <ul className="mt-1 list-inside list-disc text-muted-foreground">
              {loadWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex min-w-0 flex-col gap-2">
          {profiles.map((p) => (
            <div
              key={p.name}
              className="flex items-center gap-2 rounded-md bg-muted/30 p-2"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{p.name}</div>
                <div className="text-muted-foreground truncate text-xs">
                  {[p.provider, p.model, p.effort && `${p.effort} effort`]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
                {profileTargetLabel(p) && (
                  <div className="truncate text-xs" title={profileTargetLabel(p) ?? undefined}>
                    {profileTargetLabel(p)}
                  </div>
                )}
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void test(p.name)}
                title="Test Connection"
                aria-label={`Test ${p.name}`}
              >
                <Zap className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => startEdit(p)}
                title="Edit"
                aria-label={`Edit ${p.name}`}
              >
                <Pencil className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => startCopy(p)}
                disabled={busy}
                title="Copy profile"
                aria-label={`Copy ${p.name}`}
              >
                <Copy className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPendingDelete(p.name)}
                disabled={busy}
                title="Delete"
                aria-label={`Delete ${p.name}`}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
          {profiles.length === 0 && (
            <p className="text-muted-foreground text-sm">No profiles yet.</p>
          )}
        </div>

        <div className="rounded-md bg-muted/30 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              {copyingFrom
                ? `Copy: ${copyingFrom}`
                : editing
                  ? `Edit: ${editing}`
                  : "New Profile"}
            </h3>
            {(editing || copyingFrom) && (
              <Button variant="ghost" size="sm" onClick={startCreate}>
                {copyingFrom ? "Cancel Copy" : "Cancel Edit"}
              </Button>
            )}
          </div>
          {copyingFrom && (
            <p
              className="mb-3 text-muted-foreground text-xs leading-relaxed"
              role="note"
            >
              Eligible profiles copy stored credentials and hidden settings,
              including ACP runtime fields, server-side. Environment-backed API
              keys stay with the source; configure the new profile's API-key
              environment variable before copying. System-keyring references
              remain shared when used. OAuth and provisioned profiles cannot be
              copied. You can change the name and reasoning effort here.
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="profile-name">Name</Label>
              <Input
                id="profile-name"
                value={draft.name}
                onChange={(e) =>
                  setDraft({ ...draft, name: e.target.value })
                }
                placeholder="glm-5.3"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="profile-provider">Provider</Label>
              <Select
                value={draft.provider}
                onValueChange={(v) => setDraft({ ...draft, provider: v })}
                disabled={Boolean(copyingFrom)}
              >
                <SelectTrigger id="profile-provider">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {providerOptions.map((provider) => (
                    <SelectItem key={provider} value={provider}>
                      {provider}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="profile-model">Model</Label>
              <Input
                id="profile-model"
                value={draft.model}
                disabled={Boolean(copyingFrom)}
                onChange={(e) =>
                  setDraft({ ...draft, model: e.target.value })
                }
                placeholder="claude-sonnet-4-5"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="profile-base-url">Base URL (Optional)</Label>
              <Input
                id="profile-base-url"
                value={draft.base_url || ""}
                disabled={Boolean(copyingFrom)}
                onChange={(e) =>
                  setDraft({ ...draft, base_url: e.target.value })
                }
                placeholder="https://api.example.com/v1"
              />
              {editing && baseUrlHasHiddenData && (
                <div className="space-y-2 text-xs text-amber-600">
                  <p>
                    Credentials, query parameters, or fragments are hidden.
                    Saving without changes preserves the stored URL.
                  </p>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={replaceHiddenBaseUrl}
                      onChange={(event) =>
                        setReplaceHiddenBaseUrl(event.target.checked)
                      }
                    />
                    Replace the stored URL with the visible URL
                  </label>
                </div>
              )}
            </div>
            <div className="grid gap-1.5 sm:col-span-2">
              <Label htmlFor="profile-api-key">
                API Key (Leave Blank to Keep Current or Use Environment)
              </Label>
              <Input
                id="profile-api-key"
                type="password"
                value={draft.api_key || ""}
                disabled={Boolean(copyingFrom)}
                onChange={(e) =>
                  setDraft({ ...draft, api_key: e.target.value })
                }
                placeholder="sk-…"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="profile-temperature">Temperature</Label>
              <Input
                id="profile-temperature"
                type="number"
                step="0.1"
                min="0"
                max="2"
                value={draft.temperature ?? 0.7}
                disabled={Boolean(copyingFrom)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    temperature: Number(e.target.value),
                  })
                }
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="profile-effort">Reasoning Effort</Label>
              <Select
                value={draft.effort || ""}
                onValueChange={(v) => setDraft({ ...draft, effort: v })}
              >
                <SelectTrigger id="profile-effort">
                  <SelectValue placeholder="Model Default" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">Model Default</SelectItem>
                  {EFFORT_LEVELS.map((level) => (
                    <SelectItem key={level} value={level}>
                      {level}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="profile-description">Description</Label>
              <Input
                id="profile-description"
                value={draft.description || ""}
                disabled={Boolean(copyingFrom)}
                onChange={(e) =>
                  setDraft({ ...draft, description: e.target.value })
                }
                placeholder="workhorse profile"
              />
            </div>
            <div className="flex items-center gap-4 sm:col-span-2">
              <div className="flex items-center gap-2">
                <Switch
                  id="profile-streaming"
                  checked={draft.streaming ?? true}
                  disabled={Boolean(copyingFrom)}
                  onCheckedChange={(v) => setDraft({ ...draft, streaming: v })}
                />
                <Label htmlFor="profile-streaming">Streaming</Label>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  id="profile-tools"
                  checked={draft.supports_tools ?? true}
                  disabled={Boolean(copyingFrom)}
                  onCheckedChange={(v) =>
                    setDraft({ ...draft, supports_tools: v })
                  }
                />
                <Label htmlFor="profile-tools">Tools</Label>
              </div>
            </div>
          </div>
        </div>

        {error && <p className="text-destructive text-sm">{error}</p>}
        {testResult && (
          <p className="text-muted-foreground text-sm">{testResult}</p>
        )}

        <DialogFooter>
          <Button
            onClick={() => void save()}
            disabled={
              busy ||
              !draft.name.trim() ||
              (!copyingFrom && draft.provider !== "acp" && !draft.model.trim()) ||
              (!editing && !copyingFrom && !draft.provider.trim())
            }
          >
            {busy
              ? "Saving…"
              : copyingFrom
                ? "Copy Profile"
                : editing
                  ? "Save Changes"
                  : "Create"}
          </Button>
          {!editing && !copyingFrom && (
            <Button variant="ghost" onClick={startCreate} disabled={busy}>
              <Plus className="mr-1 size-4" /> Reset
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(next) => !next && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete This Profile?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete} will be removed from ~/.kollab/config.json.
              Sessions already running on it keep going.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                const name = pendingDelete;
                setPendingDelete(null);
                if (name) void remove(name);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  );
}
