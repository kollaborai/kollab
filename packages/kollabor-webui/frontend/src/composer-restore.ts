/**
 * Composer image restoration for draft switches.
 *
 * Composer drafts survive a session switch as TEXT only (localStorage), but
 * an image attachment lives in the composer runtime, which is dropped on the
 * switch. The saved text keeps the `[imageN]` token the token synchronizer
 * wrote, so after a switch the placeholder points at nothing and sends only
 * its literal text.
 *
 * Fix shape (issue #1103):
 * - Image attachments are retained per draft key in memory (same tab) and
 *   re-added on restore; retention lives in composer-draft.ts next to the
 *   text draft it belongs to.
 * - All managed `[imageN]` token lines are stripped from the restored text
 *   before attachments come back; the synchronizer re-writes fresh tokens
 *   for the restored attachments, so the count always matches reality.
 * - Tokens with no attachment behind them (page reload) are removed and the
 *   user gets a small notice saying the image must be pasted again.
 */

import type { ThreadUserMessagePart } from "@assistant-ui/core";

export type ComposerAttachmentLike = Readonly<{
  id: string;
  type: string;
  name: string;
  status?: Readonly<{ type?: string }> | undefined;
  contentType?: string | undefined;
  content?: readonly unknown[] | undefined;
}>;

export type StoredComposerAttachment = Readonly<{
  id: string;
  name: string;
  contentType?: string | undefined;
  content: readonly ThreadUserMessagePart[];
}>;

const TOKEN_LINE = /^\s*\[image\d+\]\s*$/;

/** Remove managed `[imageN]` token lines; report how many were removed. */
export function stripImageTokens(text: string): {
  text: string;
  tokenCount: number;
} {
  let tokenCount = 0;
  const kept: string[] = [];
  for (const line of text.split("\n")) {
    if (TOKEN_LINE.test(line)) {
      tokenCount += 1;
      continue;
    }
    kept.push(line);
  }
  if (tokenCount === 0) return { text, tokenCount };
  // Mirror the token synchronizer's cleanup so the stripped draft reads the
  // same as one it had rewritten.
  const cleaned = kept
    .join("\n")
    .replace(/^\s+/, "")
    .replace(/\n{3,}/g, "\n\n");
  return { text: cleaned, tokenCount };
}

/** Serialize the composer's complete image attachments for retention. */
export function collectImageAttachments(
  attachments: readonly ComposerAttachmentLike[],
): StoredComposerAttachment[] {
  const stored: StoredComposerAttachment[] = [];
  for (const attachment of attachments) {
    if (attachment.type !== "image") continue;
    if (attachment.status?.type !== "complete") continue;
    if (!Array.isArray(attachment.content) || attachment.content.length === 0) {
      continue;
    }
    stored.push({
      id: attachment.id,
      name: attachment.name,
      contentType: attachment.contentType,
      content: [...attachment.content],
    });
  }
  return stored;
}

/**
 * The notice for tokens stripped on restore with no attachment behind them
 * (a reload keeps text but never attachments). Null when nothing was lost.
 */
export function orphanImageNotice(
  tokenCount: number,
  restoredCount: number,
): string | null {
  const orphans = tokenCount - restoredCount;
  if (orphans <= 0) return null;
  if (orphans === 1) {
    return "1 pasted image was not kept across the session switch; paste it again.";
  }
  return `${orphans} pasted images were not kept across the session switch; paste them again.`;
}
