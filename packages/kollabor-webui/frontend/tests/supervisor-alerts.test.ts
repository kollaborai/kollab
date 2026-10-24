import assert from "node:assert/strict";
import test from "node:test";
import { supervisorAlerts } from "../src/supervisor-alerts.ts";

test("only supervisor_alert events show, newest first, repeats collapsed", () => {
  const alerts = supervisorAlerts([
    { type: "turn_complete" },
    { type: "supervisor_alert", message: "coordinator has not completed a turn in 20m" },
    { type: "supervisor_alert", message: "task abc was handed to lapis\n and never acknowledged" },
    { type: "supervisor_alert", message: "coordinator has not completed a turn in 20m" },
    { type: "supervisor_alert", message: "   " },
    { type: "supervisor_alert" },
  ]);
  assert.deepEqual(alerts.map((a) => a.message), [
    "coordinator has not completed a turn in 20m",
    "task abc was handed to lapis and never acknowledged",
  ]);
});

test("no alerts means no banner rows", () => {
  assert.deepEqual(supervisorAlerts([{ type: "error", message: "boom" }]), []);
});
