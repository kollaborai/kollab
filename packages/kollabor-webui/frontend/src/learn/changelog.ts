export type ChangeGroup = {
  category: string;
  items: string[];
};

export type ChangeSection = {
  version: string;
  date?: string;
  groups: ChangeGroup[];
};

export type WhatsNewResult =
  | { status: "loading" }
  | { status: "ready"; sections: ChangeSection[] }
  | { status: "empty" }
  | { status: "error"; message: string };

const SECTION_HEADING = /^##\s+(?:\[([^\]]+)\]|([^\n]+?))(?:\s+-\s*(.*))?\s*$/;
const CATEGORY_HEADING = /^###\s+(.+?)\s*$/;
const BULLET = /^\s*[-*]\s+(.+?)\s*$/;
const CHANGELOG_URL = "https://github.com/kollaborai/kollab/blob/main/kollabor/updates/CHANGELOG.md";

/** Parse only top-level release sections; nested markdown is shown as plain text. */
export function parseChangelog(markdown: string): ChangeSection[] {
  const sections: ChangeSection[] = [];
  let version: string | null = null;
  let date: string | undefined;
  let groups: ChangeGroup[] = [];
  let category = "Updates";
  let activeGroup: ChangeGroup | undefined;

  const finish = () => {
    if (version && groups.some((group) => group.items.length > 0)) {
      sections.push({ version, ...(date ? { date } : {}), groups });
    }
  };

  for (const line of markdown.split(/\r?\n/)) {
    const heading = SECTION_HEADING.exec(line);
    if (heading) {
      finish();
      version = (heading[1] ?? heading[2] ?? "").trim();
      date = heading[3]?.trim() || undefined;
      groups = [];
      category = "Updates";
      activeGroup = undefined;
      continue;
    }

    if (!version) continue;
    const subgroup = CATEGORY_HEADING.exec(line);
    if (subgroup) {
      category = subgroup[1].trim();
      activeGroup = undefined;
      continue;
    }

    const bullet = BULLET.exec(line);
    if (!bullet) {
      if (/^\s{2,}\S/.test(line) && activeGroup?.items.length) {
        const last = activeGroup.items.length - 1;
        activeGroup.items[last] += ` ${line.trim().replace(/`([^`]+)`/g, "$1")}`;
      } else if (line.trim()) {
        activeGroup = undefined;
      }
      continue;
    }
    let group = groups.find((item) => item.category === category);
    if (!group) {
      group = { category, items: [] };
      groups.push(group);
    }
    group.items.push(bullet[1].replace(/`([^`]+)`/g, "$1"));
    activeGroup = group;
  }
  finish();

  return sections.slice(0, 2);
}


export function whatsNewFromMarkdown(markdown: string): WhatsNewResult {
  if (!markdown.trim()) return { status: "empty" };
  if (!/^##\s+/m.test(markdown)) {
    return {
      status: "error",
      message: "The changelog format was not recognized. Open the full changelog.",
    };
  }
  const sections = parseChangelog(markdown);
  return sections.length > 0 ? { status: "ready", sections } : { status: "empty" };
}

export async function loadCanonicalWhatsNew(): Promise<WhatsNewResult> {
  try {
    const { default: markdown } = await import("virtual:kollab-changelog");
    return whatsNewFromMarkdown(markdown);
  } catch {
    return {
      status: "error",
      message: "Updates could not be loaded. Retry, or open the full changelog.",
    };
  }
}

export { CHANGELOG_URL };
