/**
 * The composer's managed [imageN] tokens and the user's typed [[imageN]]
 * literals must survive attachment edits independently (#1124 P2a).
 *
 * `stripManagedTokens` removes exactly the managed single-bracket tokens.
 * An escaped literal [[image1]] contains the substring [image1], so a bare
 * split/join strip would corrupt it to []; escapes are held out behind
 * sentinels (which contain no brackets, so a token strip can never touch
 * them) and restored verbatim afterwards.
 */

const ESCAPE_SENTINEL = "\u0002";

const ESCAPED_TOKEN = /\[\[image\d+\]\]/g;

export function stripManagedTokens(text: string, tokens: readonly string[]): string {
  const escapes: string[] = [];
  let stripped = text.replace(ESCAPED_TOKEN, (match) => {
    escapes.push(match);
    return `${ESCAPE_SENTINEL}${escapes.length - 1}${ESCAPE_SENTINEL}`;
  });
  for (const token of tokens) {
    stripped = stripped.split(token).join("");
  }
  return stripped.replace(new RegExp(`${ESCAPE_SENTINEL}(\\d+)${ESCAPE_SENTINEL}`, "g"), (_, index) => {
    return escapes[Number(index)] ?? "";
  });
}
