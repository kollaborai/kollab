import type { ThreadUserMessagePart } from "@assistant-ui/core";

/**
 * Composer image restoration for draft switches.
 *
 * Composer text drafts use localStorage; image attachments are retained per
 * draft key in memory and IndexedDB because the composer runtime is dropped on
 * a session switch or page reload (issue #1103). The saved text keeps the
 * `[imageN]` placeholder the token synchronizer wrote, so it must be restored
 * with its attachment before the user can send it.
 *
 * Fix shape:
 * - Image attachments are retained in memory and IndexedDB per draft key
 *   (composer-draft)
 *   and re-added on restore through the same adapter path a manual attach
 *   uses, so the thumbnail sends with the message.
 * - Managed `[imageN]` tokens are stripped from the restored text first
 *   (inline or as their own lines); the synchronizer re-writes fresh tokens
 *   for the restored attachments, so text and attachments never disagree.
 * - If a stored attachment cannot be restored, its token disappears and
 *   orphanImageNotice tells the user to paste the image again.
 */

export type ComposerAttachmentLike = Readonly<{
  id: string;
  type: string;
  name: string;
  status?: Readonly<{ type?: string; reason?: string }> | undefined;
  contentType?: string | undefined;
  /** Pending attachments carry the file until send; complete ones carry content. */
  file?: File | undefined;
  content?: readonly ThreadUserMessagePart[] | undefined;
}>;

export type StoredComposerAttachment = Readonly<{
  id: string;
  type: string;
  name: string;
  contentType?: string | undefined;
  file?: File | undefined;
  content?: readonly ThreadUserMessagePart[] | undefined;
}>;

const LEADING_TOKEN_LINES = /^(?:\[image\d+\](?:\n|$))+/;

/**
 * Remove the leading `[imageN]` lines written by the attachment synchronizer
 * (including its bare-token form when the composer text is empty) and report
 * how many were removed. Token-like text elsewhere is ordinary draft content.
 */
export function stripImageTokens(text: string): {
  text: string;
  tokenCount: number;
} {
  const prefix = text.match(LEADING_TOKEN_LINES)?.[0];
  if (!prefix) return { text, tokenCount: 0 };
  const tokenCount = prefix.match(/\[image\d+\]/g)?.length ?? 0;
  const cleaned = text
    .slice(prefix.length)
    .replace(/^[ \t\n]+/, "")
    .replace(/\n{3,}/g, "\n\n");
  return { text: cleaned, tokenCount };
}

/**
 * The synchronizer writes a bare `[imageN]` when the box is empty, so text
 * typed next to it lands on the same line (`[image1]what is this`). Once the
 * saved images are known, strip up to `max` leading tokens in that form too;
 * `max` is how many images are saved minus the tokens already stripped, so a
 * `[image1]` the person typed on purpose is not touched.
 */
export function stripLeadingImageTokens(
  text: string,
  max: number,
): { text: string; count: number } {
  let rest = text;
  let count = 0;
  while (count < max) {
    const token = rest.match(/^\[image\d+\]\n?/)?.[0];
    if (!token) break;
    rest = rest.slice(token.length);
    count += 1;
  }
  return { text: rest, count };
}

/**
 * Serialize the composer's image attachments for retention. Pending
 * attachments keep their File (the restore path feeds it back through the
 * adapter); complete attachments keep their content parts.
 */
export function collectImageAttachments(
  attachments: readonly ComposerAttachmentLike[],
): StoredComposerAttachment[] {
  const stored: StoredComposerAttachment[] = [];
  for (const attachment of attachments) {
    if (attachment.type !== "image") continue;
    if (attachment.file instanceof File) {
      stored.push({
        id: attachment.id,
        type: attachment.type,
        name: attachment.name,
        contentType: attachment.contentType,
        file: attachment.file,
      });
      continue;
    }
    if (Array.isArray(attachment.content) && attachment.content.length > 0) {
      stored.push({
        id: attachment.id,
        type: attachment.type,
        name: attachment.name,
        contentType: attachment.contentType,
        content: [...attachment.content],
      });
    }
  }
  return stored;
}

/**
 * The notice for tokens stripped on restore with no available attachment.
 * Null when nothing was lost.
 */
export function orphanImageNotice(
  tokenCount: number,
  restoredCount: number,
): string | null {
  const orphans = tokenCount - restoredCount;
  if (orphans <= 0) return null;
  if (orphans === 1) {
    return "1 pasted image was not kept; paste it again.";
  }
  return `${orphans} pasted images were not kept; paste them again.`;
}
