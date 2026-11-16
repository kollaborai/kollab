/** Describe declared capabilities without implying that plugin code is sandboxed. */
export function describeUses(value: unknown): { throughKollab: string[]; onItsOwn: string[]; other: string[] } {
  const uses = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  const throughKollab = [
    ...(uses.includes("agents") ? ["Can start agent runs through Kollab."] : []),
    ...(uses.includes("agents:unattended") ? ["Can run agent tools without asking; those agents can do anything you can."] : []),
    ...uses.filter(item => item.startsWith("secret:")).map(item => `Can request the secret ${item.slice(7)} through Kollab.`),
  ];
  const onItsOwn = uses.flatMap(item => {
    const [kind, ...rest] = item.split(":");
    const detail = rest.join(":");
    if (!detail) return [];
    if (kind === "run") return [`Runs: ${detail}.`];
    if (kind === "net") return [`Talks to: ${detail}.`];
    if (kind === "files") return [`Reads or writes: ${detail}.`];
    return [];
  });
  const other = uses.filter(item => item !== "agents" && item !== "agents:unattended" &&
    !item.startsWith("secret:") && !/^(run|net|files):.+/.test(item));
  return { throughKollab, onItsOwn, other };
}

export const PLUGIN_TRUST_NOTICE = "Kollab refuses requests made through Kollab that the plugin did not declare. Plugin code runs as you, as do agents with a shell; they have your files and keys and can do more. Add only plugins you trust.";
