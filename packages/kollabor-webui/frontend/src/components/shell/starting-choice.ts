import type { DefaultLoadout, ModelChoice, Profile } from "@/api";

/**
 * An applicable default loadout wins. Otherwise New Session uses the active profile's last completed model while
 * available. With no usable completed choice, blank model and effort leave the profile or harness defaults in control.
 */
export function startingChoice(
  profiles: Pick<
    Profile,
    "name" | "provider" | "last_completed_turn" | "unavailable_models"
  >[],
  active: string | undefined,
  loadout?: DefaultLoadout | null,
): ModelChoice {
  if (loadout && loadout.provider !== "acp" && profiles.some((profile) => profile.name === loadout.profile)) {
    return { profile: loadout.profile, provider: loadout.provider, model: loadout.model, effort: loadout.effort };
  }
  const activeProfile = profiles.find((profile) => profile.name === active) ?? profiles[0];
  const completed = activeProfile?.last_completed_turn;
  if (
    activeProfile &&
    completed?.model &&
    !activeProfile.unavailable_models?.includes(completed.model)
  ) {
    return {
      profile: activeProfile.name,
      provider: activeProfile.provider ?? "",
      model: completed.model,
      effort: completed.effort,
    };
  }
  return { profile: active || profiles[0]?.name || "default", provider: "", model: "", effort: "" };
}
