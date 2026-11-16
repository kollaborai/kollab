import { useEffect, useState } from "react";

const finitePositive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

/** Seconds since the last promised heartbeat (or process start), once overdue. */
export function heartbeatAge(value: unknown, now = Date.now() / 1000): number | null {
  if (!value || typeof value !== "object") return null;
  const service = value as Record<string, unknown>;
  if (service.state !== "running" || !finitePositive(service.heartbeat_s) || !Number.isFinite(now)) return null;
  const heartbeat = finitePositive(service.heartbeat) ? service.heartbeat : 0;
  const since = finitePositive(service.since) ? service.since : 0;
  const last = Math.max(heartbeat, since);
  if (!last || last > now) return null;
  const age = now - last;
  return age >= service.heartbeat_s ? age : null;
}

export function heartbeatLabel(age: number): string {
  const minutes = Math.floor(age / 60);
  return `No Heartbeat for ${minutes < 1 ? "less than 1" : minutes} min`;
}

/** Re-evaluate time-based status without requiring another inventory event. */
export function useHeartbeatClock(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now() / 1000);
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 15_000);
    return () => window.clearInterval(timer);
  }, [enabled]);
  return now;
}
