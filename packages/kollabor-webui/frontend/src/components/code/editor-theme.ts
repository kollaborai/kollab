export interface EditorThemePalette {
  background: string;
  foreground: string;
}

export function editorThemeColors(isDark: boolean, palette: EditorThemePalette) {
  return {
    base: isDark ? "vs-dark" as const : "vs" as const,
    colors: {
      "editor.background": palette.background,
      "editor.foreground": palette.foreground,
      "editorGutter.background": palette.background,
      "minimap.background": palette.background,
    },
  };
}
