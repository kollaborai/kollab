import { describe, expect, it } from "vitest";

import {
  stripManagedTokens,
  synchronizeComposerImageTokens,
} from "@/components/assistant-ui/managed-tokens";

describe("stripManagedTokens", () => {
  it("removes exactly the managed tokens", () => {
    expect(stripManagedTokens("a [image1] b", ["[image1]"])).toBe("a  b");
  });

  it("removes every managed token", () => {
    expect(
      stripManagedTokens("[image1] mid [image2] tail", ["[image1]", "[image2]"]),
    ).toBe(" mid  tail");
  });

  it("keeps a typed [[imageN]] literal intact while stripping tokens", () => {
    expect(stripManagedTokens("[[image1]] x", ["[image1]"])).toBe("[[image1]] x");
  });

  it("strips tokens and restores escapes in a mixed draft", () => {
    expect(
      stripManagedTokens("[[image1]] [image1] [[image2]] [image2]", [
        "[image1]",
        "[image2]",
      ]),
    ).toBe("[[image1]]  [[image2]] ");
  });

  it("handles the attachment-changed rewrite: old tokens out, unmanaged text kept", () => {
    // managedTokens holds the PREVIOUS attachment set's tokens; the new set
    // is prepended by the caller afterwards.
    expect(stripManagedTokens("[image1] [image2] my notes", ["[image1]"])).toBe(
      " [image2] my notes",
    );
  });

  it("keeps a malformed one-sided escape intact while stripping tokens", () => {
    // "[[image1]" is not an escape; the managed-token strip must never eat
    // into a bracket run that is not exactly the token (#1140 review).
    expect(stripManagedTokens("[[image1] hi", ["[image1]"])).toBe("[[image1] hi");
  });

  it("keeps a trailing-bracket run intact while stripping tokens", () => {
    expect(stripManagedTokens("[image1]] hi", ["[image1]"])).toBe("[image1]] hi");
  });

  it("round-trips escapes regardless of count", () => {
    const text = "[[image1]] then [[image2]] then [[image10]]";
    expect(stripManagedTokens(text, ["[image1]", "[image2]", "[image10]"])).toBe(text);
  });

  it("is a no-op with no tokens", () => {
    expect(stripManagedTokens("plain [[image3]] text", [])).toBe("plain [[image3]] text");
  });
});

/*
 * #1140 R1 (no-JS-lookbehind contract): the strip matcher must not rely on
 * lookbehind/lookahead — the PR explicitly promises a Safari-safe grammar.
 * Pinned at the source level because the strip BEHAVIOR is already covered
 * above; this contract test fails while the matcher still compiles a
 * lookaround pattern, regardless of runtime support.
 */
import source from "@/components/assistant-ui/managed-tokens.ts?raw";

describe("stripManagedTokens matcher contract", () => {
  it("compiles no lookbehind/lookahead patterns (match-first only)", () => {
    expect(source).not.toContain("(?<");
    expect(source).not.toMatch(/new RegExp\(`[^`]*\(\?/);
  });
});

describe("synchronizeComposerImageTokens", () => {
  it("rebuilds one token per current image and removes duplicates", () => {
    const text = "[image1]\n[image1]\n[image2]\nDescribe this picture";

    expect(synchronizeComposerImageTokens(text, 1)).toBe(
      "[image1]\nDescribe this picture",
    );
  });

  it("removes orphaned tokens and preserves escaped literals", () => {
    expect(
      synchronizeComposerImageTokens(
        "[image1]\n[[image2]]\nDescribe this picture",
        0,
      ),
    ).toBe("[[image2]]\nDescribe this picture");
  });

  it("preserves user text when there are no image tokens to remove", () => {
    const text = "  雪 café 🌱\nDescribe this picture";

    expect(synchronizeComposerImageTokens(text, 0)).toBe(text);
    expect(synchronizeComposerImageTokens(text, 1)).toBe(`[image1]\n${text}`);
  });

  it("handles very long non-ASCII drafts without changing their text", () => {
    const text = "雪 café 🌱 ".repeat(10_000);

    expect(synchronizeComposerImageTokens(text, 1)).toBe(`[image1]\n${text}`);
  });

  it("is idempotent for the same attachment count", () => {
    const first = synchronizeComposerImageTokens("[image01]\nnotes", 1);

    expect(synchronizeComposerImageTokens(first, 1)).toBe(first);
  });
});
