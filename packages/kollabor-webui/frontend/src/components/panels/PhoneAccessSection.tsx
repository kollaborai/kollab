import { useCallback, useEffect, useState } from "react";
import { errorText } from "@/api";
import type { EngineApi, PhoneSettings, PhoneState } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const FIELDS: Array<{ key: keyof PhoneSettings; label: string; hint: string }> = [
  { key: "endpoint", label: "WireGuard UDP Endpoint", hint: "vpn.example.net:51820" },
  { key: "server_public_key", label: "Server Public Key", hint: "32-byte WireGuard key" },
  { key: "subnet", label: "Tunnel Subnet", hint: "10.77.0.0/24" },
  { key: "allowed_ips", label: "Phone Routes", hint: "10.77.0.0/24, 192.168.1.0/24" },
  { key: "portal_url", label: "Portal Address", hint: "http://192.168.1.20:8080" },
  { key: "dns", label: "DNS (optional)", hint: "192.168.1.1" },
];

type Notice = { kind: "success" | "error"; text: string };

/** One WireGuard peer per phone; the returned QR stays in memory and is never reloaded. */
export function PhoneAccessSection({ api }: { api: EngineApi }) {
  const [state, setState] = useState<PhoneState | null>(null);
  const [draft, setDraft] = useState<Partial<PhoneSettings>>({});
  const [name, setName] = useState("");
  const [qr, setQr] = useState<{ name: string; svg: string; portalUrl: string } | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState<string | null>(null);

  const settingsDirty =
    state !== null &&
    FIELDS.some((field) => (draft[field.key] ?? "") !== state.settings[field.key]);

  const load = useCallback(async (initial = false) => {
    if (initial) setLoading(true);
    try {
      const next = await api.phoneState();
      setState(next);
      setDraft(next.settings);
      return true;
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
      return false;
    } finally {
      if (initial) setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load(true);
  }, [load]);

  const run = async (progressText: string, action: () => Promise<void>) => {
    setNotice(null);
    setProgress(progressText);
    setBusy(true);
    try {
      await action();
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  return (
    <section aria-label="Phone Access" className="grid min-w-0 gap-2 border-t pt-3">
      <h4 className="text-muted-foreground text-xs font-medium">Phone Access</h4>
      <p className="text-muted-foreground text-xs break-words">
        Provision phones through your WireGuard server. You must configure its UDP port-forward,
        server and home-network portal address yourself; Kollab does not change router settings.
      </p>

      <a
        href="/learn?topic=phone-access"
        className="text-xs underline underline-offset-2"
      >
        Learn about phone access
      </a>

      {loading ? (
        <p className="text-muted-foreground text-xs" role="status" aria-live="polite">
          Loading saved phone-access settings…
        </p>
      ) : null}
      {progress ? (
        <p className="text-muted-foreground text-xs" role="status" aria-live="polite">
          {progress}
        </p>
      ) : null}

      {state && !state.apply_configured ? (
        <p className="rounded-md border p-2 text-xs break-words" role="status">
          A host-admin WireGuard apply helper is required before phones can be added or revoked.
          Configure KOLLAB_PHONE_ACCESS_APPLY_COMMAND for the Kollab engine; it cannot be set
          from this browser.
        </p>
      ) : null}
      {state?.apply_configured && !state.can_create ? (
        <p className="rounded-md border p-2 text-xs break-words" role="status">
          Save a valid WireGuard endpoint and server public key before adding a phone.
        </p>
      ) : null}

      {settingsDirty && state?.can_create ? (
        <p className="rounded-md border p-2 text-xs break-words" role="status">
          Save changed phone settings before adding a phone.
        </p>
      ) : null}

      <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[8rem_minmax(0,1fr)] sm:items-center">
        {FIELDS.map((field) => (
          <label key={field.key} className="contents">
            <span className="text-muted-foreground break-words">{field.label}</span>
            <Input
              aria-label={field.label}
              value={draft[field.key] ?? ""}
              placeholder={field.hint}
              autoComplete="off"
              disabled={loading || !state}
              onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })}
            />
          </label>
        ))}
      </div>

      <p className="text-muted-foreground text-xs break-words">
        Phone Routes must include the tunnel network and your home LAN network if the portal is
        reached over LAN. Use the narrowest routes that provide access.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={busy || !state}
          onClick={() =>
            run("Saving phone-access settings…", async () => {
              const next = await api.savePhoneSettings(draft);
              setState(next);
              setDraft(next.settings);
              setNotice({ kind: "success", text: "Phone-access settings saved." });
            })
          }
        >
          Save Settings
        </Button>
      </div>

      <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
        <Input
          aria-label="Phone name"
          disabled={loading || !state}
          value={name}
          placeholder="Phone name"
          maxLength={48}
          autoComplete="off"
          onChange={(event) => setName(event.target.value)}
        />
        <Button
          size="sm"
          className="shrink-0"
          disabled={busy || !state?.can_create || settingsDirty}
          onClick={() =>
            run("Creating the phone peer and applying the server change…", async () => {
              const made = await api.addPhone(name);
              setQr({
                name: made.peer.name,
                svg: made.qr_svg,
                portalUrl: state?.settings.portal_url ?? "",
              });
              setName("");
              await load();
            })
          }
        >
          Add Phone
        </Button>
      </div>

      {qr ? (
        <div className="grid min-w-0 justify-items-start gap-2">
          <p className="text-xs break-words">
            On {qr.name}, open WireGuard, tap +, then Create from QR code. This code is sent only
            by the one-time create response. After scanning, open{" "}
            {qr.portalUrl ? (
              <span className="font-mono break-all">{qr.portalUrl}</span>
            ) : (
              "the private portal address from settings"
            )}{" "}
            in Safari and use Add to Home Screen.
          </p>
          <div
            role="img"
            aria-label={`WireGuard QR code for ${qr.name}`}
            className="w-full max-w-64 overflow-hidden rounded bg-white p-2 [&_svg]:h-auto [&_svg]:w-full"
            dangerouslySetInnerHTML={{ __html: qr.svg }}
          />
          <Button size="sm" variant="outline" onClick={() => setQr(null)}>
            Hide QR
          </Button>
        </div>
      ) : null}

      {state?.peers.length ? (
        <ul className="grid min-w-0 gap-1">
          {state.peers.map((peer) => (
            <li key={peer.id} className="flex min-w-0 items-center justify-between gap-2">
              <span className="min-w-0 break-words">
                {peer.name} <span className="text-muted-foreground font-mono text-xs">{peer.address}</span>
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                aria-label={`Revoke ${peer.name}`}
                onClick={() => {
                  if (!window.confirm(`Revoke ${peer.name}? This removes and disconnects that peer.`)) return;
                  void run(`Revoking ${peer.name} and applying the server change…`, async () => {
                    await api.revokePhone(peer.id);
                    setQr(null);
                    const refreshed = await load();
                    if (refreshed) {
                      setNotice({ kind: "success", text: `${peer.name} was revoked and disconnected.` });
                    }
                  });
                }}
              >
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      ) : state ? (
        <p className="text-muted-foreground text-xs" role="status">
          No phone peers yet. Save the server settings, then add a phone to provision its QR.
        </p>
      ) : null}
      {notice ? (
        <p
          role={notice.kind === "error" ? "alert" : "status"}
          className={`text-xs break-words ${notice.kind === "error" ? "text-destructive" : "text-emerald-700 dark:text-emerald-300"}`}
        >
          {notice.text}
        </p>
      ) : null}
    </section>
  );
}
