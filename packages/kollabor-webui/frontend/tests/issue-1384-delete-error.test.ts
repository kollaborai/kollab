import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

it("announces a session delete error while the active chat remains open", () => {
  expect(app).toMatch(/activeSession\s*&&\s*error\s*\?\s*\([\s\S]*?<NoticeBar[\s\S]*?urgent[\s\S]*?\{error\}/);
});
