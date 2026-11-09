import { expect, test } from "vitest";

import {
  envHiddenLabel,
  envHiddenTitle,
  isDaemonOutdated,
  shellName,
  type TerminalEnvReport,
} from "../src/components/terminal/terminal-env";
import { OUTDATED_NOTICE } from "../src/lib/code-fetch";

const report: TerminalEnvReport = {
  count: 6,
  hidden: {
    secret: ["API_KEY", "DB_PASS", "TOKEN"],
    kollab: ["KOLLAB_HUB_TOKEN", "KOLLAB_X"],
    denied: ["HOME_SECRET"],
  },
};

test("the header label counts the hidden variables and is empty when none are hidden", () => {
  expect(envHiddenLabel(report)).toBe("Env: 6 Hidden");
  expect(envHiddenLabel({ count: 0, hidden: { secret: [], kollab: [], denied: [] } })).toBe("");
  expect(envHiddenLabel(null)).toBe("");
});

test("the title lists each reason's names and ends with the settings path", () => {
  expect(envHiddenTitle(report)).toBe(
    [
      "Secret names (3): API_KEY, DB_PASS, TOKEN",
      "Kollab names (2): KOLLAB_HUB_TOKEN, KOLLAB_X",
      "Denied by setting (1): HOME_SECRET",
      "Change in Settings: Code And Terminal",
    ].join("\n"),
  );
});

test("a reason with no names is left out of the title", () => {
  const onlyKollab: TerminalEnvReport = { count: 1, hidden: { secret: [], kollab: ["KOLLAB_A"], denied: [] } };
  expect(envHiddenTitle(onlyKollab)).toBe(
    ["Kollab names (1): KOLLAB_A", "Change in Settings: Code And Terminal"].join("\n"),
  );
});

test("the title is empty when nothing is hidden or the report is missing", () => {
  expect(envHiddenTitle(null)).toBe("");
  expect(envHiddenTitle({ count: 0, hidden: { secret: [], kollab: [], denied: [] } })).toBe("");
});

test("isDaemonOutdated is true only for the engine's daemon_outdated envelope", () => {
  const envelope = {
    success: false,
    error: { code: "daemon_outdated", message: OUTDATED_NOTICE },
  };
  expect(isDaemonOutdated(envelope)).toBe(true);
  expect(isDaemonOutdated({ success: false, error: { code: "request_failed", message: "x" } })).toBe(false);
  expect(isDaemonOutdated({ success: false, error: { message: "no code" } })).toBe(false);
  expect(isDaemonOutdated({ success: true, data: {} })).toBe(false);
  expect(isDaemonOutdated("daemon_outdated")).toBe(false);
  expect(isDaemonOutdated(null)).toBe(false);
});

test("shellName is the last path part of the configured shell", () => {
  expect(shellName("/bin/zsh")).toBe("zsh");
  expect(shellName("/usr/local/bin/fish/")).toBe("fish");
  expect(shellName("C:\\Windows\\System32\\cmd.exe")).toBe("cmd.exe");
  expect(shellName("")).toBe("");
  expect(shellName("   ")).toBe("");
});

test("the outdated notice is the plain sentence with no version or code in it", () => {
  expect(OUTDATED_NOTICE).toBe("This agent runs older code. Restart it to use Code, Git and Terminal.");
  expect(OUTDATED_NOTICE).not.toMatch(/\d+\.\d+|daemon_outdated/);
});

test("the env report type carries the shell the daemon resolved", () => {
  const report: TerminalEnvReport = { count: 0, shell: "zsh", hidden: { secret: [], kollab: [], denied: [] } };
  expect(report.shell).toBe("zsh");
});
