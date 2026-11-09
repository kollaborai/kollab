import type { DefaultLoadout, ModelChoice, Profile } from "@/api";

/**
 * What New Session starts on. The terminal starts on the default loadout, but a web session always names a
 * profile, which makes the terminal skip it; so the form does: the loadout's profile, model and level. A
 * loadout on a profile that is gone, or on an ACP harness (which takes its model through its own options),
 * leaves the active profile's own model.
 */
export function startingChoice(
  profiles: Pick<Profile, "name">[],
  active: string | undefined,
  loadout?: DefaultLoadout | null,
): ModelChoice {
  if (loadout && loadout.provider !== "acp" && profiles.some((profile) => profile.name === loadout.profile)) {
    return { profile: loadout.profile, provider: loadout.provider, model: loadout.model, effort: loadout.effort };
  }
  return { profile: active || profiles[0]?.name || "default", provider: "", model: "", effort: "" };
}
