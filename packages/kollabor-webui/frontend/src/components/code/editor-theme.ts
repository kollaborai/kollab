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

export function editorThemeColors(isDark: boolean, palette: EditorThemePalette) {
  return {
    base: isDark ? "vs-dark" as const : "vs" as const,
    colors: {
      "editor.background": palette.background,
      "editor.foreground": palette.foreground,
      "editorGutter.background": palette.background,
      "editor.lineHighlightBackground": mixHexColors(palette.background, palette.foreground, 0.06),
      "editorLineNumber.foreground": mixHexColors(palette.background, palette.foreground, 0.65),
      "editorLineNumber.activeForeground": palette.foreground,
      "minimap.background": palette.background,
    },
  };
}
