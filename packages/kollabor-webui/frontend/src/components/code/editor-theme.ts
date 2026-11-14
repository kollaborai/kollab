export interface EditorThemePalette {
  background: string;
  foreground: string;
}

function mixHexColors(background: string, foreground: string, foregroundWeight: number): string {
  const channels = (color: string) => [1, 3, 5].map((start) => Number.parseInt(color.slice(start, start + 2), 16));
  const backgroundChannels = channels(background);
  const foregroundChannels = channels(foreground);
  return `#${backgroundChannels.map((channel, index) => {
    const foregroundChannel = foregroundChannels[index] ?? channel;
    return Math.round(channel + (foregroundChannel - channel) * foregroundWeight)
      .toString(16)
      .padStart(2, "0");
  }).join("")}`;
}

function luminance(color: string): number | null {
  if (!/^#[0-9a-f]{6}$/i.test(color)) return null;
  const channels = [1, 3, 5].map((start) => Number.parseInt(color.slice(start, start + 2), 16) / 255);
  const [red = 0, green = 0, blue = 0] = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function hasReadableContrast(background: string, foreground: string): boolean {
  const backgroundLuminance = luminance(background);
  const foregroundLuminance = luminance(foreground);
  if (backgroundLuminance === null || foregroundLuminance === null) return false;
  const lighter = Math.max(backgroundLuminance, foregroundLuminance);
  const darker = Math.min(backgroundLuminance, foregroundLuminance);
  return (lighter + 0.05) / (darker + 0.05) >= 4.5;
}

export function editorThemeColors(isDark: boolean, palette: EditorThemePalette) {
  const activePalette = hasReadableContrast(palette.background, palette.foreground)
    ? palette
    : isDark
      ? { background: "#0e0e14", foreground: "#f0f0f5" }
      : { background: "#ffffff", foreground: "#252525" };

  return {
    base: isDark ? "vs-dark" as const : "vs" as const,
    colors: {
      "editor.background": activePalette.background,
      "editor.foreground": activePalette.foreground,
      "editorGutter.background": activePalette.background,
      "editor.lineHighlightBackground": mixHexColors(activePalette.background, activePalette.foreground, 0.06),
      "editorLineNumber.foreground": mixHexColors(activePalette.background, activePalette.foreground, 0.65),
      "editorLineNumber.activeForeground": activePalette.foreground,
      "minimap.background": activePalette.background,
    },
  };
}
