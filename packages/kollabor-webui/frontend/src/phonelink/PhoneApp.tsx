import { useEffect, useState } from "react";
import App from "@/App";
import { Button } from "@/components/ui/button";
import { phoneIdentity, PhoneTransport } from "./phone-transport";

export default function PhoneApp() {
  const [state, setState] = useState("connecting");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [active, setActive] = useState(false);
  useEffect(() => {
    let stopped = false;
    let transport: PhoneTransport | undefined;
    let uninstall: (() => void) | undefined;
    void phoneIdentity()
      .then(async (identity) => {
        if (stopped) return;
        transport = new PhoneTransport(identity, (next, pairing) => {
          if (stopped) return;
          setState(next);
          if (pairing) setCode(pairing);
          if ((next === "connected" || next === "temporary") && transport) {
            uninstall ??= transport.installFetch();
            setError("");
            setActive(true);
          }
        });
        await transport.connect();
        if (stopped) return;
        uninstall ??= transport.installFetch();
        setActive(true);
      })
      .catch((reason) => {
        if (!stopped)
          setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      stopped = true;
      uninstall?.();
      transport?.close();
    };
  }, []);
  if (active)
    return (
      <div className="flex h-dvh min-h-0 flex-col overflow-hidden">
        {state === "offline" || state === "connecting" ? (
          <div
            role="status"
            className="shrink-0 bg-amber-500/15 px-4 py-2 text-center text-sm"
          >
            Reconnecting to your computer… Keep Kollab running there.
          </div>
        ) : null}
        {state === "temporary" ? (
          <div
            role="status"
            className="shrink-0 bg-amber-500/15 px-4 py-2 text-center text-sm"
          >
            Connected for this window. Browser storage is unavailable, so this
            link cannot be remembered.
          </div>
        ) : null}
        <div className="min-h-0 flex-1">
          <App contained />
        </div>
      </div>
    );
  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="grid w-full max-w-sm justify-items-center gap-5 text-center">
        <h1 className="text-lg font-semibold">PhoneLink</h1>
        <p
          className="text-muted-foreground text-sm"
          role={error ? "alert" : "status"}
        >
          {error ||
            (state === "approval"
              ? "Approve this phone in PhoneLink on your computer. Check that the pairing codes match."
              : "Connecting to your Kollab computer…")}
        </p>
        {code && !error ? (
          <div className="py-2">
            <p className="text-muted-foreground mb-2 text-xs">Pairing Code</p>
            <strong className="font-mono text-2xl tracking-widest">
              {code}
            </strong>
          </div>
        ) : null}
        {error ? (
          <Button variant="outline" onClick={() => location.reload()}>
            Try Again
          </Button>
        ) : null}
        <p className="text-muted-foreground text-xs">
          Your agents run on your computer. Keep it awake and Kollab open.
        </p>
      </div>
    </main>
  );
}
