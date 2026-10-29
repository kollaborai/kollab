import { Fragment } from "react";

import { cn } from "@/lib/utils";

/** `path` with its middle folders as `…` once more than one would be hidden. */
export function shortPath(path: string, tail?: number): string {
  const parts = path.split("/");
  return tail && parts.length > tail + 2 ? [parts[0], "…", ...parts.slice(-tail)].join("/") : path;
}

/**
 * A file path that wraps after a slash, never inside a name; the whole path is
 * its tooltip. With `tail`, a longer path keeps its root and last `tail` parts
 * (`~/…/web/.kollab/config.json`).
 */
export function PathText({ path, tail, className }: { path: string; tail?: number; className?: string }) {
  return (
    <span className={cn("break-words", className)} title={path}>
      {shortPath(path, tail).split(/(?<=\/)/).map((part, i) => (
        <Fragment key={i}>
          {part}
          <wbr />
        </Fragment>
      ))}
    </span>
  );
}
