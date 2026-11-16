/**
 * The composer's managed [imageN] tokens and the user's typed [[imageN]]
 * literals must survive attachment edits independently (#1124 P2a).
 *
 * `stripManagedTokens` removes exactly the managed single-bracket tokens,
 * matched by the token grammar rather than by substring (#1140 review): a
 * candidate must not open a bracket run, so an escaped [[image1]] and a
 * one-sided run like "[[image1]" keep every bracket.
 *
 * Deliberately stricter than the splitter's grammar: the strip edits the
 * user's draft and must never consume a bracket run's pieces, while the
 * engine counter aligns with the splitter (which treats the inner token of
 * "[[image1]" as one). Two grammars, two jobs.
 *
 * Match-first, no lookbehind/lookahead (#1140 R1 contract): occurrences are
 * found with a plain pattern and the bracket-run check happens in the
 * replace callback against the surrounding characters.
 */

const escapeRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function stripManagedTokens(text: string, tokens: readonly string[]): string {
  let stripped = text;
  for (const token of tokens) {
    stripped = stripped.replace(new RegExp(escapeRegExp(token), "g"), (match, offset: number, whole: string) => {
      const before = whole[offset - 1];
      const after = whole[offset + match.length];
      if (before === "[" || after === "]") return match;
      return "";
    });
  }
  return stripped;
}
