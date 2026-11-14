/**
 * Engine/frontend version comparison.
 *
 * The engine reports its version with a PEP 440 local version segment in
 * source checkouts (`0.18.5+<sha8>`, or `0.18.5+unknown` when the checkout
 * has no git metadata), while the web bundle carries the plain release
 * version. Only the base version identifies a genuine drift, so the local
 * segment is stripped before comparing (#684).
 */
export function sameVersionBase(engine: string, bundle: string): boolean {
  const base = (version: string) => version.split("+")[0];
  return base(engine) === base(bundle);
}
