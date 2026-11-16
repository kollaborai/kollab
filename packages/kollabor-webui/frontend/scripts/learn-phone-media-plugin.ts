import { lstatSync, readFileSync, readdirSync, realpathSync } from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";

export const LEARN_MEDIA_MODULE_ID = "virtual:kollab-learn-media";
const resolvedLearnMediaModuleId = `\0${LEARN_MEDIA_MODULE_ID}`;

const mimeByExtension: Readonly<Record<string, string>> = {
  ".avif": "image/avif",
  ".gif": "image/gif",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
};

type TopicFile = {
  workflows?: {
    media?: { kind?: "image" | "video"; src?: string; poster?: string };
  }[];
};

/** Read only paths referenced by Learn topic JSON and embed bytes for signed PhoneLink. */
export function buildPhoneLearnMediaMap(
  topicsDir: string,
  publicDir: string,
  onWatchFile?: (file: string) => void,
): Record<string, string> {
  const mediaUrls = new Set<string>();
  for (const name of readdirSync(topicsDir).filter((file) => file.endsWith(".json")).sort()) {
    const topicPath = path.join(topicsDir, name);
    onWatchFile?.(topicPath);
    const topic = JSON.parse(readFileSync(topicPath, "utf8")) as TopicFile;
    for (const workflow of topic.workflows ?? []) {
      const media = workflow.media;
      // PhoneLink's CSP permits the inline image previews but intentionally does
      // not permit data: video or caption tracks. Videos use their poster + steps.
      const imageSource = media?.kind === "video" ? media.poster : media?.src;
      if (imageSource) mediaUrls.add(imageSource);
    }
  }

  const publicRoot = path.resolve(publicDir);
  const realPublicRoot = realpathSync(publicRoot);
  const map: Record<string, string> = {};
  for (const source of [...mediaUrls].sort()) {
    // Restrict the signed bundle to same-origin local Learn media; never fetch an
    // arbitrary URL or accidentally embed a path outside public/learn.
    if (!/^\/learn\/[a-z0-9-]+\/[a-z0-9._-]+$/i.test(source)) {
      throw new Error(`PhoneLink Learn media must use a local /learn/<topic>/<file> path: ${source}`);
    }
    const assetPath = path.resolve(publicRoot, `.${source}`);
    if (!assetPath.startsWith(`${publicRoot}${path.sep}`)) {
      throw new Error(`PhoneLink Learn media escaped its public directory: ${source}`);
    }
    let assetStat;
    let resolvedAssetPath: string;
    try {
      assetStat = lstatSync(assetPath);
      resolvedAssetPath = realpathSync(assetPath);
    } catch (error) {
      throw new Error(`PhoneLink signed Learn media is missing: ${source}`, { cause: error });
    }
    if (!assetStat.isFile() || assetStat.isSymbolicLink() || !resolvedAssetPath.startsWith(`${realPublicRoot}${path.sep}`)) {
      throw new Error(`PhoneLink Learn media must be a regular file inside public/: ${source}`);
    }
    const extension = path.extname(assetPath).toLowerCase();
    const mime = mimeByExtension[extension];
    if (!mime) throw new Error(`Unsupported PhoneLink Learn media type: ${source}`);
    onWatchFile?.(assetPath);
    let bytes: Buffer;
    try {
      bytes = readFileSync(assetPath);
    } catch (error) {
      throw new Error(`PhoneLink signed Learn media is missing: ${source}`, { cause: error });
    }
    map[source] = `data:${mime};base64,${bytes.toString("base64")}`;
  }
  return map;
}

export function learnPhoneMediaPlugin(options: {
  enabled: boolean;
  topicsDir: string;
  publicDir: string;
}): Plugin {
  return {
    name: "kollab-phone-learn-media",
    resolveId(source) {
      return source === LEARN_MEDIA_MODULE_ID ? resolvedLearnMediaModuleId : null;
    },
    load(id) {
      if (id !== resolvedLearnMediaModuleId) return null;
      const map = options.enabled
        ? buildPhoneLearnMediaMap(options.topicsDir, options.publicDir, (file) => this.addWatchFile(file))
        : {};
      return `export default Object.freeze(${JSON.stringify(map)});`;
    },
  };
}
