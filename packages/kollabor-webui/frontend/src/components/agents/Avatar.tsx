import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import type { AvatarSpec, EngineApi } from "@/api";
import { GemAvatar, type GemAvatarProps } from "@/components/gems/GemAvatar";
import { useGemLook, useLookKey } from "@/components/gems/gem-appearance";
import { cn } from "@/lib/utils";
import { imageSource, initialsOf, inkFor, isGemAvatar, tileColor } from "./avatar-model";

export type AvatarProps = GemAvatarProps & {
  /** What the engine says the agent looks like; absent or a gem draws the 3D gem. */
  avatar?: AvatarSpec | null;
  /** The persona's name for people ("Maya Lindqvist"); its initials fill a tile with no picture. */
  displayName?: string | null;
  /** The folder whose project roster an image comes from, when it is not the engine's own. */
  folder?: string | null;
};

type LoadImage = (path: string) => Promise<string | null>;

const AvatarImageContext = createContext<LoadImage | null>(null);

/**
 * Fetches the roster image avatars the engine serves. An <img> cannot send the
 * bearer token, so each file is fetched once with it and shown from a blob; a
 * file that fails is remembered as failed and its tile keeps the initials.
 */
export function AvatarImageProvider({ api, children }: { api: EngineApi; children: ReactNode }) {
  const cache = useRef(new Map<string, Promise<string | null>>());
  useEffect(() => {
    const files = cache.current;
    return () => {
      for (const file of files.values()) void file.then((url) => url && URL.revokeObjectURL(url));
      files.clear();
    };
  }, []);
  const load = useCallback<LoadImage>(
    (path) => {
      let file = cache.current.get(path);
      if (!file) {
        file = api
          .request(path)
          .then((response) => response.blob())
          .then((blob) => URL.createObjectURL(blob))
          .catch(() => null);
        cache.current.set(path, file);
      }
      return file;
    },
    [api],
  );
  return <AvatarImageContext.Provider value={load}>{children}</AvatarImageContext.Provider>;
}

/** The picture of an image avatar: undefined until it is in, null when it cannot be shown. */
function useAvatarPicture(avatar: AvatarSpec | null | undefined, folder: string): string | null | undefined {
  const load = useContext(AvatarImageContext);
  const source = avatar?.kind === "image" ? imageSource(avatar, folder) : null;
  const path = source && "fetch" in source ? source.fetch : null;
  const [loaded, setLoaded] = useState<{ path: string; url: string | null } | null>(null);
  useEffect(() => {
    if (!path || !load) return;
    let current = true;
    void load(path).then((url) => {
      if (current) setLoaded({ path, url });
    });
    return () => {
      current = false;
    };
  }, [path, load]);
  if (!source) return null;
  if ("direct" in source) return source.direct;
  if (!load) return null;
  return loaded?.path === path ? loaded.url : undefined;
}

/**
 * One agent's picture, wherever a gem shows. A gem (or no avatar) is the 3D
 * GemAvatar, unchanged. Anything else is a flat square tile on the persona's
 * color in the same layout box: a picture, an emoji, or initials. An avatar
 * picked in the studio (Properties → Avatar) replaces the roster's.
 */
export function Avatar({ avatar, displayName, folder, ...gem }: AvatarProps) {
  const look = useGemLook(useLookKey(gem.gem, gem.where));
  const shown = look.avatar ?? avatar;
  if (isGemAvatar(shown)) return <GemAvatar {...gem} />;
  return (
    <FlatAvatar
      avatar={shown}
      displayName={displayName}
      folder={folder ?? ""}
      {...gem}
      color={look.color ?? gem.color}
    />
  );
}

function FlatAvatar({
  avatar,
  displayName,
  folder,
  gem: name,
  color,
  size = 32,
  className,
  label,
  live,
}: Omit<AvatarProps, "avatar" | "folder"> & { avatar: AvatarSpec; folder: string }) {
  const picture = useAvatarPicture(avatar, folder);
  // A picture the browser cannot draw keeps the initials, until a different one arrives.
  const [brokenPicture, setBrokenPicture] = useState<string | null>(null);
  const rgb = tileColor(color, name || "");
  // The tile sits where the gem's body does: a little inside the layout box.
  const tile = size * 0.8;
  const rem = (px: number) => `${px / 16}rem`;
  const face: CSSProperties = {
    width: rem(tile),
    height: rem(tile),
    borderRadius: rem(tile * 0.22),
    background: `rgb(${rgb.join(",")})`,
    color: inkFor(rgb),
  };
  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };
  const showPicture = avatar.kind === "image" && picture && picture !== brokenPicture;
  return (
    <span
      {...a11y}
      className={cn("inline-flex shrink-0 items-center justify-center", live === false && "opacity-50 grayscale", className)}
      style={{ width: rem(size), height: rem(size) }}
    >
      <span
        className="inline-flex shrink-0 items-center justify-center overflow-hidden leading-none font-semibold select-none"
        style={{ ...face, fontSize: rem(avatar.kind === "emoji" ? tile * 0.56 : tile * 0.4) }}
      >
        {showPicture ? (
          <img
            src={picture}
            alt=""
            draggable={false}
            className="size-full object-cover"
            onError={() => setBrokenPicture(picture)}
          />
        ) : avatar.kind === "emoji" && avatar.value ? (
          avatar.value
        ) : (
          initialsOf(displayName, name || "")
        )}
      </span>
    </span>
  );
}
