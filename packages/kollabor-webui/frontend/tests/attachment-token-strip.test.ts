import { describe, expect, it } from "vitest";

import { stripManagedTokens } from "@/components/assistant-ui/managed-tokens";

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

  it("round-trips escapes regardless of count", () => {
    const text = "[[image1]] then [[image2]] then [[image10]]";
    expect(stripManagedTokens(text, ["[image1]", "[image2]", "[image10]"])).toBe(text);
  });

  it("is a no-op with no tokens", () => {
    expect(stripManagedTokens("plain [[image3]] text", [])).toBe("plain [[image3]] text");
  });
});
