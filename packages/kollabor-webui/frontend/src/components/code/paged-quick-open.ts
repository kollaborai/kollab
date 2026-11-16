export type PagedFile = { name: string; path: string; type: "file" | "dir"; ext?: string };
export type DirectoryPage = { entries: PagedFile[]; next: string | null };
export type QuickFile = { name: string; path: string; ext: string; relativePath: string };

export function fuzzyMatch(query: string, text: string): boolean {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let qi = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) if (t[ti] === q[qi]) qi++;
  return qi === q.length;
}

/** Walk bounded pages with a depth and visit limit; return whether some entries were not examined. */
export async function scanQuickFiles(
  root: string, query: string,
  fetchPage: (path: string, after: string | null) => Promise<DirectoryPage>,
  cancelled: () => boolean,
): Promise<{ files: QuickFile[]; partial: boolean }> {
  const stack: { path: string; after: string | null; depth: number }[] = [{ path: root, after: null, depth: 0 }];
  const files: QuickFile[] = [];
  let visited = 0;
  let partial = false;
  while (stack.length && !cancelled()) {
    const current = stack.pop()!;
    const page = await fetchPage(current.path, current.after);
    if (cancelled()) break;
    if (page.next) stack.push({ ...current, after: page.next });
    const directories: typeof stack = [];
    for (const entry of page.entries) {
      if (++visited > 20000) return { files, partial: true };
      if (entry.type === "dir") {
        if (current.depth >= 8) partial = true;
        else directories.push({ path: entry.path, after: null, depth: current.depth + 1 });
      } else if (entry.type === "file") {
        const relativePath = entry.path.startsWith(root) ? entry.path.slice(root.length + 1) : entry.path;
        if (fuzzyMatch(query, relativePath)) {
          files.push({ name: entry.name, path: entry.path, ext: entry.ext ?? "", relativePath });
          if (files.length >= 50) return { files, partial: true };
        }
      }
    }
    stack.push(...directories.reverse());
  }
  return { files, partial };
}
