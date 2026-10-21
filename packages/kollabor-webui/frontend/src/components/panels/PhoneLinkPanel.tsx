import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, QrCode } from "lucide-react";
import type { EngineApi, PhoneLinkOffer, PhoneLinkState } from "@/api";
import { errorText } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { learnHref } from "@/learn/catalog";
import { PhoneAccessSection } from "./PhoneAccessSection";

type PhoneLinkPanelProps = {
  api: EngineApi;
  onOpenNetwork: () => void;
};

export function PhoneLinkPanel(props: PhoneLinkPanelProps) {
  if (window.location.pathname.startsWith("/relay/v1/phone/")) {
    return (
      <section className="mx-auto grid max-w-2xl gap-3 px-6 py-6">
        <h3 className="text-lg font-semibold">
          Manage PhoneLink on Your Computer
        </h3>
        <p className="text-muted-foreground text-sm">
          Open PhoneLink in Kollab on your computer to link or remove phones and
          change advanced connection settings.
        </p>
      </section>
    );
  }
  return <DesktopPhoneLinkPanel {...props} />;
}

/** Device-wide pairing. Unmounting drops the invitation QR and its secret. */
function DesktopPhoneLinkPanel({ api, onOpenNetwork }: PhoneLinkPanelProps) {
  const [state, setState] = useState<PhoneLinkState | null>(null);
  const [offer, setOffer] = useState<PhoneLinkOffer | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const epoch = useRef(0);
  const loading = useRef(false);
  const load = useCallback(
    async (refresh = false) => {
      if (loading.current) return;
      loading.current = true;
      const mine = epoch.current;
      try {
        const next = await api.phoneLinkState(refresh);
        if (mine === epoch.current) {
          setState(next);
          setLoadError("");
        }
      } catch (reason) {
        if (mine === epoch.current) setLoadError(errorText(reason));
      } finally {
        loading.current = false;
      }
    },
    [api],
  );
  useEffect(() => {
    void load();
    const poll = window.setInterval(() => {
      setNow(Date.now());
      void load();
    }, 3000);
    return () => {
      epoch.current += 1;
      window.clearInterval(poll);
    };
  }, [load]);
  useEffect(() => {
    if (
      offer &&
      state?.phones.some((phone) => phone.id === offer.id && phone.linked)
    )
      setOffer(null);
  }, [state, offer]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  };
  const expired = Boolean(offer && now / 1000 >= offer.expires_at);
  const phones = state?.phones ?? [];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
      <section
        aria-label="PhoneLink"
        className="mx-auto grid max-w-2xl min-w-0 gap-5 pb-3"
      >
        <div className="min-w-0">
          <h3 className="text-lg font-semibold">PhoneLink</h3>
          <p className="text-muted-foreground mt-1 text-sm">
            Open this computer’s Kollab app on your phone. Your agents keep
            running on this computer.
          </p>
        </div>
        <ol className="grid list-decimal gap-4 pl-5 text-sm">
          <li className="min-w-0 pl-1">
            <div className="grid min-w-0 flex-1 gap-3">
              <div>
                <h4 className="text-sm font-medium">Create a QR Code</h4>
                <p className="text-muted-foreground mt-1 text-sm">
                  Choose Link a Phone, then scan the code with your phone’s
                  camera. Kollab opens in Safari or Chrome. No extra app to
                  install.
                </p>
              </div>
              {!offer ? (
                <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
                  <Input
                    aria-label="Phone name"
                    placeholder="Phone name (optional)"
                    value={name}
                    maxLength={48}
                    disabled={busy || !state}
                    onChange={(event) => setName(event.target.value)}
                  />
                  <Button
                    disabled={busy || state?.state !== "ready"}
                    className="shrink-0"
                    onClick={() =>
                      void run(async () => {
                        setOffer(await api.linkPhone(name));
                        setName("");
                        await load();
                      })
                    }
                  >
                    <QrCode aria-hidden="true" />
                    {busy ? "Creating Code…" : "Link a Phone"}
                  </Button>
                </div>
              ) : (
                <div className="grid justify-items-start gap-3">
                  <p className="text-sm font-medium">{offer.name}</p>
                  {expired ? (
                    <p role="status" className="text-muted-foreground text-sm">
                      This QR code expired. Create a new code to link your
                      phone.
                    </p>
                  ) : (
                    <>
                      <div
                        role="img"
                        aria-label="PhoneLink pairing QR code"
                        className="w-full max-w-56 rounded-lg bg-white p-2 [&_svg]:h-auto [&_svg]:w-full"
                        dangerouslySetInnerHTML={{ __html: offer.qr_svg }}
                      />
                      <p className="text-muted-foreground text-xs">
                        Expires in{" "}
                        {Math.max(
                          1,
                          Math.ceil((offer.expires_at - now / 1000) / 60),
                        )}{" "}
                        min. Keep this invitation private.
                      </p>
                    </>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        setState(await api.revokePhoneLink(offer.id));
                        setOffer(null);
                      })
                    }
                  >
                    Cancel Invitation
                  </Button>
                </div>
              )}
              {!state ? (
                <p role="status" className="text-muted-foreground text-sm">
                  Checking your connection…
                </p>
              ) : state.state !== "ready" ? (
                <div role="status" className="grid justify-items-start gap-2">
                  <p className="text-muted-foreground text-sm">
                    {state.message}
                  </p>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={
                      state.state === "connect_needed"
                        ? onOpenNetwork
                        : () => void load(true)
                    }
                  >
                    {state.state === "connect_needed"
                      ? "Open Network"
                      : "Try Again"}
                  </Button>
                </div>
              ) : null}
            </div>
          </li>
          <li className="min-w-0 pl-1">
            <h4 className="text-sm font-medium">Approve Your Phone</h4>
            <p className="text-muted-foreground mt-1 text-sm">
              Compare the pairing code on both screens, then approve the phone
              on this computer.
            </p>
          </li>
          <li className="min-w-0 pl-1">
            <div>
              <h4 className="text-sm font-medium">Open Kollab</h4>
              <p className="text-muted-foreground mt-1 text-sm">
                Bookmark Kollab to return in the same phone browser. Keep this
                computer awake and online to use its agents from anywhere.
              </p>
            </div>
          </li>
        </ol>
        {error || loadError ? (
          <div className="grid justify-items-start gap-2">
            <p role="alert" className="text-destructive text-sm break-words">
              {error || loadError}
            </p>
            {!state ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => void load(true)}
              >
                Try Again
              </Button>
            ) : null}
          </div>
        ) : null}
        {phones.length ? (
          <section
            aria-label="Your phones"
            className="grid min-w-0 gap-3 border-t pt-4"
          >
            <h4 className="text-sm font-medium">Your Phones</h4>
            {phones.map((phone) => (
              <div key={phone.id} className="grid min-w-0 gap-2 border-t py-3">
                <div className="flex min-w-0 items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium break-words">
                      {phone.name}
                    </p>
                    <p className="text-muted-foreground text-xs">
                      {phone.connected
                        ? "Connected"
                        : phone.linked
                          ? "Offline"
                          : phone.expires_at < now / 1000
                            ? "Invitation Expired"
                            : "Waiting for Your Phone"}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    aria-label={`Remove ${phone.name}`}
                    onClick={() => {
                      if (
                        phone.linked &&
                        !window.confirm(
                          `Remove ${phone.name}? This phone will lose access to Kollab.`,
                        )
                      )
                        return;
                      void run(async () => {
                        setState(await api.revokePhoneLink(phone.id));
                        if (offer?.id === phone.id) setOffer(null);
                      });
                    }}
                  >
                    Remove
                  </Button>
                </div>
                {phone.pending.map((pending) => (
                  <div
                    key={pending.key}
                    className="flex flex-wrap items-center gap-3 border-t pt-3"
                  >
                    <span className="text-muted-foreground flex-1 text-xs">
                      Check this code on your phone:{" "}
                      <strong className="text-foreground font-mono tracking-wider">
                        {pending.code}
                      </strong>
                    </span>
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          setState(
                            await api.approvePhoneLink(phone.id, pending.key),
                          );
                        })
                      }
                    >
                      Approve Phone
                    </Button>
                  </div>
                ))}
              </div>
            ))}
          </section>
        ) : null}
        <div className="text-muted-foreground flex items-center gap-2 text-xs">
          <span>Uses your Kollab connection.</span>
          <a
            href={learnHref("phone-access")}
            className="ml-auto shrink-0 underline underline-offset-2"
          >
            How It Works
          </a>
        </div>
        <details className="group min-w-0 border-t pt-3">
          <summary className="text-muted-foreground flex cursor-pointer list-none items-center gap-2 text-xs font-medium">
            <ChevronRight
              className="size-3.5 transition-transform group-open:rotate-90"
              aria-hidden="true"
            />
            Advanced Settings
          </summary>
          <div className="mt-3 grid min-w-0 gap-3">
            <p className="text-muted-foreground text-xs">
              Use an existing WireGuard server for direct private-network
              access. These settings are optional and separate from PhoneLink.
            </p>
            <PhoneAccessSection api={api} />
          </div>
        </details>
      </section>
    </div>
  );
}
