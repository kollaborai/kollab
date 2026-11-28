import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const threadSource = readFileSync(
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/components/assistant-ui/thread.tsx"),
  "utf8",
);

describe("history error Retry touch target", () => {
  it("has a 44px minimum height when a touch pointer is available", () => {
    const button = /<Button type="button" size="sm" variant="outline" onClick=\{retryBanner\}([^>]*)>\s*Retry/.exec(threadSource);

    expect(button, "the history error Retry button").not.toBeNull();
    expect(button?.[1]).toContain("any-pointer-coarse:min-h-11");
  });
});
