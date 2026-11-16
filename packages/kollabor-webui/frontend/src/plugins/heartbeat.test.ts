import { describe, expect, it } from "vitest";
import { heartbeatAge, heartbeatLabel } from "./heartbeat";

describe("service heartbeat status", () => {

  it("formats a known runtime without inventing time for missing or future starts", async () => {
    const { runtimeLabel } = await import("./heartbeat");
    expect(runtimeLabel(2500, 10000)).toBe("2 h");
    expect(runtimeLabel(9970, 10000)).toBe("less than 1 min");
    expect(runtimeLabel(9820, 10000)).toBe("3 min");
    expect(runtimeLabel(null, 10000)).toBeNull();
    expect(runtimeLabel(10001, 10000)).toBeNull();
    expect(runtimeLabel(9000, null)).toBeNull();
    expect(runtimeLabel(Infinity, 10000)).toBeNull();
  });

  it("only reports a promised heartbeat after its interval", () => {
    expect(heartbeatAge({ state: "running", heartbeat_s: 60, heartbeat: 950, since: 900 }, 1000)).toBeNull();
    expect(heartbeatAge({ state: "running", heartbeat_s: 60, heartbeat: 940, since: 900 }, 1000)).toBe(60);
    expect(heartbeatLabel(370)).toBe("No Heartbeat for 6 min");
    expect(heartbeatLabel(30)).toBe("No Heartbeat for less than 1 min");
  });

  it("uses the current process start when there is no heartbeat or the heartbeat predates it", () => {
    expect(heartbeatAge({ state: "running", heartbeat_s: 60, heartbeat: null, since: 900 }, 1000)).toBe(100);
    expect(heartbeatAge({ state: "running", heartbeat_s: 60, heartbeat: 800, since: 990 }, 1000)).toBeNull();
  });

  it("gives a restarted engine a fresh observation interval", () => {
    const service = { state: "running", heartbeat_s: 60, heartbeat: null, since: 100, heartbeat_monitor_since: 980 };
    expect(heartbeatAge(service, 1000)).toBeNull();
    expect(heartbeatAge(service, 1040)).toBe(60);
  });

  it("uses an engine clock sample and monotonic elapsed time instead of the browser wall clock", async () => {
    const { engineClock } = await import("./heartbeat");
    expect(engineClock(1000, 3000, 4500)).toBe(1001.5);
    expect(engineClock(null, 3000, 4500)).toBeNull();
    expect(engineClock(1000, 3000, 2500)).toBeNull();
    expect(engineClock(Infinity, 3000, 4500)).toBeNull();
  });


  it("does not invent a failure for missing, invalid, future or stopped data", () => {
    expect(heartbeatAge({ state: "running", heartbeat: 800 }, 1000)).toBeNull();
    expect(heartbeatAge({ state: "running", heartbeat_s: 60 }, 1000)).toBeNull();
    expect(heartbeatAge({ state: "running", heartbeat_s: -1, heartbeat: 800 }, 1000)).toBeNull();
    expect(heartbeatAge({ state: "running", heartbeat_s: 60, heartbeat: 1200 }, 1000)).toBeNull();
    expect(heartbeatAge({ state: "stopped", heartbeat_s: 60, heartbeat: 800 }, 1000)).toBeNull();
  });
});
